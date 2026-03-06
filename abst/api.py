import time

from ninja import Router, Schema
from ninja.security import django_auth
from ninja.pagination import paginate
import polars as pl

from abst.geo import get_geo_id_list
from abst.models import Kanton, Vorlage
from abst.schema import GemeindeResult, KantonSchema, ResultsGemeindeSchema, ResultsKantonSchema, ResultsTotalSchema, VorlageListingSchema
from abst.store import get_abst_result_history, get_abst_result_kantone, get_abst_result_total, get_abst_results
from abst.predict import predict_results, prepare_predict_data

router = Router()


@router.get("kantone/", response=list[KantonSchema])
def get_kantone(request):
    kantone = Kanton.objects.all()
    return kantone


@router.get("vorlagen", response=list[VorlageListingSchema])
@paginate(per_page=50)
def get_vorlagen(request, region: str | None = None, date: str | None = None):
    vorlagen = Vorlage.objects.all().order_by("-tag__date", "vorlagen_id")
    if region is not None:
        vorlagen = vorlagen.filter(region=region)

    if date is not None:
        vorlagen = vorlagen.filter(tag__date=date)
    return vorlagen


@router.get("{vorlage_id}/geodata", response=str)
def get_geodata_link(request, vorlage_id: int):
    vorlage = Vorlage.objects.get(vorlagen_id=vorlage_id)
    return vorlage.tag.stand.document.url if vorlage.tag.stand.document else ""


@router.get("{vorlage_id}/total", response=ResultsTotalSchema)
def get_results_total(request, vorlage_id: int):
    return get_abst_result_total(vorlage_id).to_dicts()[0]


@router.get("{vorlage_id}/kantone", response=list[ResultsKantonSchema])
def get_results_kantone(request, vorlage_id: int):
    return get_abst_result_kantone(vorlage_id).to_dicts()


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
    df_merged = df_geo.join(df_results, on="geo_id", how="left").filter(
        pl.col("ja_prozent").is_not_null())

    return df_merged.to_dicts()


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


@router.post("{vorlage_id}/test_prediction", response=TestPredictionResponseSchema, auth=django_auth)
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

    for p in (predicted or []):
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

        pred_dicts.append({
            "geo_id": geo_id,
            "ja_prozent": p.result.ja_prozent,
            "stimmbeteiligung": p.result.stimmbeteiligung
        })

    report = {
        "mae_ja_prozent": sum(accuracies_ja) / len(accuracies_ja) if accuracies_ja else 0.0,
        "mae_stimmbeteiligung": sum(accuracies_bet) / len(accuracies_bet) if accuracies_bet else 0.0,
        "num_evaluated": len(accuracies_ja),
        "pred_ja_prozent": pred_ja / (pred_ja + pred_nein) * 100 if (pred_ja + pred_nein) > 0 else 0.0,
    }

    return {"report": report, "predictions": pred_dicts}
