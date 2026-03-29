import polars as pl
from typing import Literal

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.pagination import paginate
from ninja.security import django_auth

from abst.geo import get_geo_id_list
from abst.models import Abstimmungstag, Gemeinde, Kanton, Partei, Vorlage, Zaehlkreis
from abst.predict import predict_results, prepare_predict_data
from abst.schema import (
    AbstimmungstagSchema,
    GemeindeResult,
    GemeindeSchema,
    KantonSchema,
    ScatterOptionsSchema,
    ScatterPointSchema,
    ResultsGemeindeSchema,
    ResultsKantonSchema,
    ResultsTotalSchema,
    VorlageListingSchema,
)
from abst.store import (
    get_abst_result_history,
    get_abst_result_kantone,
    get_abst_result_total,
    get_abst_results,
    get_scatterplot_data,
)

router = Router()


@router.get("kantone/", response=list[KantonSchema])
def get_kantone(request):
    kantone = Kanton.objects.all()
    return kantone


@router.get("tage/", response=list[AbstimmungstagSchema])
def get_abstimmungstage(request):
    tage = Abstimmungstag.objects.all().order_by("-date")
    return tage


@router.get("vorlagen", response=list[VorlageListingSchema])
@paginate(per_page=50)
def get_vorlagen(
    request,
    region: str | None = None,
    date: str | None = None,
    name: str | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
):
    vorlagen = Vorlage.objects.all()
    if region is not None:
        vorlagen = vorlagen.filter(region=region)

    if date is not None:
        vorlagen = vorlagen.filter(tag__date=date)

    if name is not None:
        vorlagen = vorlagen.filter(name__icontains=name.lower())

    if sort_by:
        valid_sort_fields = {
            "vorlagen_id": "vorlagen_id",
            "name": "name",
            "region": "region",
            "finished": "finished",
            "angenommen": "angenommen",
            "date": "tag__date",
            "result.jaStimmenInProzent": "result__jaStimmenInProzent",
            "result.stimmbeteiligungInProzent": "result__stimmbeteiligungInProzent",
        }
        if sort_by in valid_sort_fields:
            sort_field = valid_sort_fields[sort_by]
            if sort_dir == "desc":
                sort_field = "-" + sort_field
            vorlagen = vorlagen.order_by(sort_field)
        else:
            vorlagen = vorlagen.order_by("-tag__date", "vorlagen_id")
    else:
        vorlagen = vorlagen.order_by("-tag__date", "vorlagen_id")

    return vorlagen


@router.get("{vorlage_id}/geodata", response=str)
def get_geodata_link(request, vorlage_id: int):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    return vorlage.tag.stand.document.url if vorlage.tag.stand.document else ""


@router.get("{vorlage_id}/total", response=list[ResultsTotalSchema])
def get_results_total(request, vorlage_id: int):
    total = get_abst_result_total(vorlage_id)
    if total is None:
        return []
    return total.to_dicts()


@router.get("{vorlage_id}/kantone", response=list[ResultsKantonSchema])
def get_results_kantone(request, vorlage_id: int):
    return get_abst_result_kantone(vorlage_id).to_dicts()


@router.get("{vorlage_id}/gemeinden/stand", response=list[GemeindeSchema])
def get_gemeinden_stand(request, vorlage_id: int):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    stand = vorlage.tag.stand

    if not stand.document:
        return []

    gemeinden = Gemeinde.objects.filter(stand=stand).order_by("geo_id")

    return gemeinden


@router.get("{vorlage_id}/zaehlkreise/stand", response=list[GemeindeSchema])
def get_zaehlkreise_stand(request, vorlage_id: int):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    stand = vorlage.tag.stand

    if not stand.document:
        return []

    zaehlkreise = (
        Zaehlkreis.objects.filter(gemeinde__stand=stand)
        .select_related("gemeinde")
        .order_by("geo_id")
    )

    return [
        {
            "name": z.name,
            "geo_id": z.geo_id,
            "kanton": z.gemeinde.kanton,
            "kanton_id": z.gemeinde.kanton_id,
        }
        for z in zaehlkreise
    ]


@router.get("{vorlage_id}/gemeinden", response=list[ResultsGemeindeSchema])
def get_results_gemeinden(request, vorlage_id: int):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)

    if vorlage.kantonal:
        kanton = Kanton.objects.get(short=vorlage.region)
        geo_ids = get_geo_id_list(
            vorlage.tag.stand, kanton_id=kanton.kanton_id)
    else:
        geo_ids = get_geo_id_list(vorlage.tag.stand)

    df_geo = pl.DataFrame({"geo_id": geo_ids})

    df_results = get_abst_results(vorlage_id)
    if df_results is None:
        return []

    return df_results.to_dicts()


@router.get("{vorlage_id}/{geo_id}/result", response=list[GemeindeResult])
def get_result_history(request, vorlage_id: int, geo_id: int):
    result = get_abst_result_history(vorlage_id, geo_id)
    print(result)

    return result.to_dicts()


class PredictTestSchema(Schema):
    known_geo_ids: list[int]


class ResultPredictionReportSchema(Schema):
    mae_ja_prozent: float
    mae_stimmbeteiligung: float
    num_evaluated: int
    pred_ja_prozent: float


class SinglePredictionSchema(Schema):
    geo_id: int
    ja_prozent: float
    stimmbeteiligung: float


class TestPredictionResponseSchema(Schema):
    report: ResultPredictionReportSchema
    predictions: list[SinglePredictionSchema]


def _scatter_metrics() -> list[dict[str, str]]:
    return [
        {"id": "ja_prozent", "name": "Abstimmungsresultat Ja in %"},
        {"id": "stimmbeteiligung", "name": "Stimmbeteiligung in %"},
        {"id": "anzahl_stimmberechtigte", "name": "Anzahl Stimmberechtigte"},
        {"id": "wahlen_result", "name": "Wahlresultat"},
        {"id": "abstimmung_result", "name": "Andere Abstimmung"},
    ]


def _scatter_scopes() -> list[dict[str, str]]:
    return [
        {"id": "partei", "name": "Partei"},
        {"id": "parteigruppe", "name": "Parteigruppe"},
        {"id": "lager", "name": "Parteipolitisches Lager"},
    ]


def _scatter_wahlen_options() -> tuple[
    list[dict[str, int | str]],
    list[dict[str, int | str]],
    list[dict[str, int | str]],
]:
    parteien = [
        {
            "id": p.partei_id,
            "name": p.kurzname or p.name,
        }
        for p in Partei.objects.all().order_by("name")
    ]

    parteigruppen = [
        {
            "id": int(g["parteigruppen_id"]),
            "name": g["parteigruppen_name"],
        }
        for g in Partei.objects.exclude(parteigruppen_id=None)
        .exclude(parteigruppen_name="")
        .values("parteigruppen_id", "parteigruppen_name")
        .distinct()
        .order_by("parteigruppen_name")
    ]

    lager = [
        {
            "id": int(l["parteipolitische_lager_id"]),
            "name": l["parteipolitische_lager_name"],
        }
        for l in Partei.objects.exclude(parteipolitische_lager_id=None)
        .exclude(parteipolitische_lager_name="")
        .values("parteipolitische_lager_id", "parteipolitische_lager_name")
        .distinct()
        .order_by("parteipolitische_lager_name")
    ]

    return parteien, parteigruppen, lager


def _scatter_color_modes():
    base_modes = [
        {"id": "solid", "name": "Feste Farbe"},
        {"id": "canton", "name": "Nach Kanton"},
    ]

    color_metrics = _scatter_metrics()
    for metric in color_metrics:
        base_modes.append(
            {"id": metric["id"], "name": f"Nach {metric['name']}"})

    return base_modes


@router.get("{vorlage_id}/scatter/options", response=ScatterOptionsSchema)
def get_scatter_options(request, vorlage_id: int):
    parteien, parteigruppen, lager = _scatter_wahlen_options()
    return {
        "metrics": _scatter_metrics(),
        "scopes": _scatter_scopes(),
        "color_modes": _scatter_color_modes(),
        "parteien": parteien,
        "parteigruppen": parteigruppen,
        "lager": lager,
    }


@router.get("{vorlage_id}/scatter/data", response=list[ScatterPointSchema])
def get_scatter_data(
    request,
    vorlage_id: int,
    x_metric: str = "ja_prozent",
    y_metric: str = "stimmbeteiligung",
    size_metric: str = "anzahl_stimmberechtigte",
    color_metric: str | None = None,
    wahlen_scope: Literal["partei", "parteigruppe", "lager"] = "partei",
    wahlen_option_id: int | None = None,
    wahlen_mode: Literal["current", "last", "diff"] = "current",
    abstimmung_vorlage_id: int | None = None,
    abstimmung_result_mode: Literal["ja_prozent",
                                    "stimmbeteiligung"] = "ja_prozent",
):
    try:
        df = get_scatterplot_data(
            vorlage_id=vorlage_id,
            x_metric=x_metric,
            y_metric=y_metric,
            size_metric=size_metric,
            wahlen_scope=wahlen_scope,
            wahlen_option_id=wahlen_option_id,
            wahlen_mode=wahlen_mode,
            abstimmung_vorlage_id=abstimmung_vorlage_id,
            abstimmung_result_mode=abstimmung_result_mode,
            color_metric=color_metric,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc

    if df.is_empty():
        return []

    df = df.filter(
        pl.col("x_value").is_not_null()
        & pl.col("y_value").is_not_null()
        & pl.col("size_value").is_not_null()
    )

    return df.to_dicts()


@router.get("{vorlage_id}/scatter/export.xlsx")
def export_scatter_xlsx(
    request,
    vorlage_id: int,
    x_metric: str = "ja_prozent",
    y_metric: str = "stimmbeteiligung",
    size_metric: str = "anzahl_stimmberechtigte",
    color_metric: str | None = None,
    wahlen_scope: Literal["partei", "parteigruppe", "lager"] = "partei",
    wahlen_option_id: int | None = None,
    wahlen_mode: Literal["current", "last", "diff"] = "current",
    abstimmung_vorlage_id: int | None = None,
    abstimmung_result_mode: Literal["ja_prozent",
                                    "stimmbeteiligung"] = "ja_prozent",
):
    try:
        df = get_scatterplot_data(
            vorlage_id=vorlage_id,
            x_metric=x_metric,
            y_metric=y_metric,
            size_metric=size_metric,
            wahlen_scope=wahlen_scope,
            wahlen_option_id=wahlen_option_id,
            wahlen_mode=wahlen_mode,
            abstimmung_vorlage_id=abstimmung_vorlage_id,
            abstimmung_result_mode=abstimmung_result_mode,
            color_metric=color_metric,
        )
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc

    if not df.is_empty():
        df = df.filter(
            pl.col("x_value").is_not_null()
            & pl.col("y_value").is_not_null()
            & pl.col("size_value").is_not_null()
        )

    import io

    output = io.BytesIO()
    export_df = df.rename(
        {
            "name": "gemeinde",
            "x_value": "x_wert",
            "y_value": "y_wert",
            "size_value": "groesse_wert",
        }
    )
    export_df.write_excel(workbook=output, worksheet="Scatterplot")
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="scatterplot_{vorlage_id}.xlsx"'
    )
    return response


@router.post(
    "{vorlage_id}/test_prediction",
    response=TestPredictionResponseSchema,
    auth=django_auth,
)
@csrf_exempt
def test_prediction(request, vorlage_id: int, payload: PredictTestSchema):
    predicted = predict_results(
        vorlage_id, known_geo_ids=payload.known_geo_ids)

    ja_values, bet_values, mask, geo_ids = prepare_predict_data(vorlage_id)

    true_ja = dict(zip(geo_ids, ja_values))
    true_bet = dict(zip(geo_ids, bet_values))
    old_mask = dict(zip(geo_ids, mask))

    accuracies_ja = []
    accuracies_bet = []

    pred_dicts = []

    pred_ja = 0
    pred_nein = 0

    for p in predicted or []:
        if p.result is None:
            continue
        geo_id = p.geo_id
        if not old_mask.get(geo_id, True):
            diff_ja = abs(p.result.ja_prozent - true_ja.get(geo_id, 0.0))
            diff_bet = abs(p.result.stimmbeteiligung -
                           true_bet.get(geo_id, 0.0))
            accuracies_ja.append(diff_ja)
            accuracies_bet.append(diff_bet)

        pred_ja += p.result.ja_stimmen
        pred_nein += p.result.nein_stimmen

        pred_dicts.append(
            {
                "geo_id": geo_id,
                "ja_prozent": p.result.ja_prozent,
                "stimmbeteiligung": p.result.stimmbeteiligung,
            }
        )

    report = {
        "mae_ja_prozent": sum(accuracies_ja) / len(accuracies_ja)
        if accuracies_ja
        else 0.0,
        "mae_stimmbeteiligung": sum(accuracies_bet) / len(accuracies_bet)
        if accuracies_bet
        else 0.0,
        "num_evaluated": len(accuracies_ja),
        "pred_ja_prozent": pred_ja / (pred_ja + pred_nein) * 100
        if (pred_ja + pred_nein) > 0
        else 0.0,
    }

    return {"report": report, "predictions": pred_dicts}
