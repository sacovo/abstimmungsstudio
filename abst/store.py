import datetime
from collections import defaultdict
from contextlib import contextmanager
from datetime import date
from typing import Literal

import pandas as pd
import polars as pl
import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from influxdb_client.client.influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from abst.models import Abstimmungstag, Gemeinde, GeoStand, Kanton, Partei, Vorlage

from .schema import GemeindeResult, Result, VorlageSchema

BASE_URL = "https://ckan.opendata.swiss/api/3/action/package_show"
WAHLEN_META_URL = (
    "https://ogd-static.voteinfo-app.ch/v4/ogd/sd-t-17.02-NRW2023-metadaten.json"
)
WAHLEN_RESULTATE_URL = (
    "https://ogd-static.voteinfo-app.ch/v4/ogd/sd-t-17.02-NRW2023-parteien.json"
)


def import_abst_meta(
    ds_id="echtzeitdaten-am-abstimmungstag-zu-eidgenoessischen-abstimmungsvorlagen",
):
    url = BASE_URL + "?id=" + ds_id

    data = requests.get(url).json()
    new = []
    for resource in data["result"]["resources"]:
        coverage_date = date.fromisoformat(resource["coverage"])
        stand = GeoStand.objects.order_by("-date").first()
        url = resource["url"]
        obj, created = Abstimmungstag.objects.get_or_create(
            date=coverage_date,
            defaults={
                "url_eidg": url,
                "name": resource["name"]["de"],
                "stand": stand,
            },
        )
        if created:
            new.append(obj)

    return new


def import_abst_kantonal_meta(
    ds_id="echtzeitdaten-am-abstimmungstag-zu-kantonalen-abstimmungsvorlagen",
):
    url = BASE_URL + "?id=" + ds_id
    data = requests.get(url).json()
    for resource in data["result"]["resources"]:
        coverage_date = date.fromisoformat(resource["coverage"])
        url = resource["url"]
        Abstimmungstag.objects.filter(
            date=coverage_date).update(url_kantonal=url)


@contextmanager
def get_influx_client(timeout=20):
    client = InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
        timeout=timeout * 1000,  # Convert seconds to milliseconds
    )
    try:
        yield client
    finally:
        client.close()


def _convert_result_data(
    timestamp, gemeinde, vorlage, kanton, result_data
) -> GemeindeResult:
    return GemeindeResult(
        timestamp=timestamp,
        geo_id=int(gemeinde["geoLevelnummer"]),
        vorlage_id=int(vorlage["vorlagenId"]),
        geo_name=gemeinde["geoLevelname"],
        kanton=kanton["geoLevelname"],
        kanton_id=int(kanton["geoLevelnummer"]),
        result=Result(
            final=result_data["gebietAusgezaehlt"],
            anzahl_stimmberechtigte=result_data["anzahlStimmberechtigte"] or 0,
            ja_stimmen=result_data["jaStimmenAbsolut"],
            nein_stimmen=result_data["neinStimmenAbsolut"],
            ja_prozent=result_data["jaStimmenInProzent"],
            stimmbeteiligung=result_data["stimmbeteiligungInProzent"] or 0,
        )
        if (result_data["gebietAusgezaehlt"])
        else None,
    )


def get_first_name(names: list) -> str:
    for name in names:
        if name["text"]:
            return name["text"]
    return "Unknown"


def get_name(names, lang):
    for n in names:
        if n["langKey"] == lang:
            return n["text"]
    return "Unknown"


def get_localized_name(names: list[dict], lang: str = "de") -> str:
    for name in names or []:
        if name.get("langKey") == lang and name.get("text"):
            return name["text"]
    for name in names or []:
        if name.get("text"):
            return name["text"]
    return "Unknown"


def import_wahlen_metadata(
    json_url: str = WAHLEN_META_URL, tag: Abstimmungstag | None = None
) -> int:
    data = requests.get(json_url).json()
    parteien = data.get("parteien", [])

    imported = 0
    for partei in parteien:
        partei_id = partei.get("partei_id")
        if partei_id is None:
            continue

        Partei.objects.update_or_create(
            partei_id=int(partei_id),
            defaults={
                "name": get_localized_name(partei.get("partei_bezeichnung", []), "de"),
                "kurzname": get_localized_name(
                    partei.get("partei_bezeichnung_kurz", []), "de"
                ),
                "parteigruppen_id": partei.get("parteigruppen_id"),
                "parteigruppen_name": get_localized_name(
                    partei.get("parteigruppen_bezeichnung", []), "de"
                ),
                "parteipolitische_lager_id": partei.get("parteipolitische_lager_id"),
                "parteipolitische_lager_name": get_localized_name(
                    partei.get("parteipolitische_lager_bezeichnung", []), "de"
                ),
                "tag": tag,
            },
        )
        imported += 1

    return imported


def fetch_and_store_wahlen_results(json_url: str = WAHLEN_RESULTATE_URL) -> int:
    data = requests.get(json_url).json()
    rows = data.get("level_gemeinden", [])
    if not rows:
        return 0

    timestamp = int(datetime.datetime.now().timestamp() * 1_000_000_000)
    points = []

    for row in rows:
        geo_id = row.get("gemeinde_nummer")
        partei_id = row.get("partei_id")
        if geo_id is None or partei_id is None:
            continue

        points.append(
            {
                "measurement": "wahlen_result",
                "tags": {
                    "geo_id": int(geo_id),
                    "partei_id": int(partei_id),
                },
                "fields": {
                    "partei_staerke": float(row.get("partei_staerke") or 0.0),
                    "letzte_wahl_partei_staerke": float(
                        row.get("letzte_wahl_partei_staerke") or 0.0
                    ),
                    "differenz_partei_staerke": float(
                        row.get("differenz_partei_staerke") or 0.0
                    ),
                },
                "time": timestamp,
            }
        )

    if not points:
        return 0

    with get_influx_client() as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(bucket=settings.INFLUX_BUCKET, record=points)

    return len(points)


def get_wahlen_results(partei_id: int, mode: str = "current"):
    field_map = {
        "current": "partei_staerke",
        "last": "letzte_wahl_partei_staerke",
        "diff": "differenz_partei_staerke",
    }
    field_name = field_map.get(mode, "partei_staerke")

    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "wahlen_result" and r.partei_id == "{partei_id}")
          |> filter(fn: (r) => r._field == "{field_name}")
          |> group(columns: ["geo_id", "_field"])
          |> last()
          |> pivot(rowKey:["geo_id"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["geo_id"])
        '''
        result = query_api.query_data_frame(query)

        if isinstance(result, list):
            if len(result) == 0:
                return None
            result = pd.concat(result)

        if len(result) == 0:
            return None

        df = pl.from_pandas(result)
        if field_name not in df.columns:
            return None

        return df.with_columns(
            geo_id=pl.col("geo_id").cast(pl.Int32),
            value=pl.col(field_name).cast(pl.Float64),
        ).select("geo_id", "value")


def get_wahlen_results_multi(partei_ids: list[int], mode: str = "current"):
    if not partei_ids:
        return None

    field_map = {
        "current": "partei_staerke",
        "last": "letzte_wahl_partei_staerke",
        "diff": "differenz_partei_staerke",
    }
    field_name = field_map.get(mode, "partei_staerke")

    id_filter = " or ".join([f'r.partei_id == "{pid}"' for pid in partei_ids])

    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "wahlen_result")
          |> filter(fn: (r) => {id_filter})
          |> filter(fn: (r) => r._field == "{field_name}")
          |> group(columns: ["geo_id", "partei_id", "_field"])
          |> last()
          |> group(columns: ["geo_id", "_field"])
          |> sum()
          |> pivot(rowKey:["geo_id"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["geo_id"])
        '''
        result = query_api.query_data_frame(query)

        if isinstance(result, list):
            if len(result) == 0:
                return None
            result = pd.concat(result)

        if len(result) == 0:
            return None

        df = pl.from_pandas(result)
        if field_name not in df.columns:
            return None

        return df.with_columns(
            geo_id=pl.col("geo_id").cast(pl.Int32),
            value=pl.col(field_name).cast(pl.Float64),
        ).select("geo_id", "value")


def get_wahlen_results_parteigruppe(parteigruppen_id: int, mode: str = "current"):
    partei_ids = list(
        Partei.objects.filter(parteigruppen_id=parteigruppen_id).values_list(
            "partei_id", flat=True
        )
    )
    return get_wahlen_results_multi(partei_ids=partei_ids, mode=mode)


def get_wahlen_results_lager(lager_id: int, mode: str = "current"):
    partei_ids = list(
        Partei.objects.filter(parteipolitische_lager_id=lager_id).values_list(
            "partei_id", flat=True
        )
    )
    return get_wahlen_results_multi(partei_ids=partei_ids, mode=mode)


SCATTER_ALLOWED_METRICS = {
    "ja_prozent",
    "stimmbeteiligung",
    "anzahl_stimmberechtigte",
    "wahlen_result",
    "abstimmung_result",
}


def _empty_scatter_df() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "geo_id": pl.Int32,
            "name": pl.Utf8,
            "kanton": pl.Utf8,
            "kanton_id": pl.Int32,
            "status": pl.Utf8,
            "ja_prozent": pl.Float64,
            "stimmbeteiligung": pl.Float64,
            "anzahl_stimmberechtigte": pl.Int64,
            "wahlen_value": pl.Float64,
            "abstimmung_value": pl.Float64,
            "x_value": pl.Float64,
            "y_value": pl.Float64,
            "size_value": pl.Float64,
        }
    )


def _get_scatter_geo_df(vorlage: Vorlage) -> pl.DataFrame:
    gemeinden = Gemeinde.objects.filter(stand=vorlage.tag.stand)

    if vorlage.kantonal:
        kanton = Kanton.objects.filter(short=vorlage.region).first()
        if kanton is None:
            return pl.DataFrame(schema={"geo_id": pl.Int32})
        gemeinden = gemeinden.filter(kanton_id=kanton.kanton_id)

    rows = list(
        gemeinden.values(
            "geo_id",
            "name",
            "kanton",
            "kanton_id",
        )
    )
    if not rows:
        return pl.DataFrame(schema={"geo_id": pl.Int32})

    return pl.DataFrame(rows).with_columns(pl.col("geo_id").cast(pl.Int32))


def _get_scatter_wahlen_df(
    scope: Literal["partei", "parteigruppe", "lager"],
    option_id: int,
    mode: Literal["current", "last", "diff"] = "current",
) -> pl.DataFrame | None:
    if scope == "partei":
        return get_wahlen_results(partei_id=option_id, mode=mode)
    if scope == "parteigruppe":
        return get_wahlen_results_parteigruppe(parteigruppen_id=option_id, mode=mode)
    return get_wahlen_results_lager(lager_id=option_id, mode=mode)


def _get_scatter_abstimmung_df(
    other_vorlage_id: int,
    metric: Literal["ja_prozent", "stimmbeteiligung"] = "ja_prozent",
) -> pl.DataFrame | None:
    other_df = get_abst_results(other_vorlage_id)
    if other_df is None or other_df.is_empty():
        return None

    return other_df.select(
        "geo_id",
        pl.col(metric).cast(pl.Float64).alias("abstimmung_value"),
    )


def get_scatterplot_data(
    vorlage_id: int,
    x_metric: str,
    y_metric: str,
    size_metric: str,
    wahlen_scope: Literal["partei", "parteigruppe", "lager"] = "partei",
    wahlen_option_id: int | None = None,
    wahlen_mode: Literal["current", "last", "diff"] = "current",
    abstimmung_vorlage_id: int | None = None,
    abstimmung_result_mode: Literal["ja_prozent",
                                    "stimmbeteiligung"] = "ja_prozent",
) -> pl.DataFrame:
    metrics = {x_metric, y_metric, size_metric}
    invalid = metrics - SCATTER_ALLOWED_METRICS
    if invalid:
        raise ValueError(f"Ungueltige Metrik: {', '.join(sorted(invalid))}")

    needs_wahlen = "wahlen_result" in metrics
    if needs_wahlen and wahlen_option_id is None:
        raise ValueError(
            "wahlen_option_id ist erforderlich fuer wahlen_result")

    needs_abstimmung = "abstimmung_result" in metrics
    if needs_abstimmung and abstimmung_vorlage_id is None:
        raise ValueError(
            "abstimmung_vorlage_id ist erforderlich fuer abstimmung_result")

    cache_key = (
        f"scatter:{vorlage_id}:{x_metric}:{y_metric}:{size_metric}:"
        f"{wahlen_scope}:{wahlen_option_id}:{wahlen_mode}:"
        f"{abstimmung_vorlage_id}:{abstimmung_result_mode}"
    )
    cached_rows = cache.get(cache_key)
    if cached_rows is not None:
        return pl.DataFrame(cached_rows)

    vorlage = Vorlage.objects.select_related(
        "tag__stand").get(vorlagen_id=vorlage_id)
    geo_df = _get_scatter_geo_df(vorlage)
    if geo_df.is_empty():
        return _empty_scatter_df()

    abst_df = get_abst_results(vorlage_id)
    if abst_df is None:
        return _empty_scatter_df()

    merged = abst_df.join(geo_df, on="geo_id", how="inner")

    if needs_wahlen:
        if wahlen_option_id is None:
            raise ValueError(
                "wahlen_option_id ist erforderlich fuer wahlen_result")

        wahlen_df = _get_scatter_wahlen_df(
            scope=wahlen_scope,
            option_id=wahlen_option_id,
            mode=wahlen_mode,
        )
        if wahlen_df is not None and not wahlen_df.is_empty():
            merged = merged.join(
                wahlen_df.rename({"value": "wahlen_value"}),
                on="geo_id",
                how="left",
            )
        else:
            merged = merged.with_columns(
                pl.lit(None).cast(pl.Float64).alias("wahlen_value"))
    else:
        merged = merged.with_columns(
            pl.lit(None).cast(pl.Float64).alias("wahlen_value"))

    if needs_abstimmung:
        if abstimmung_vorlage_id is None:
            raise ValueError(
                "abstimmung_vorlage_id ist erforderlich fuer abstimmung_result")

        abstimmung_df = _get_scatter_abstimmung_df(
            other_vorlage_id=abstimmung_vorlage_id,
            metric=abstimmung_result_mode,
        )
        if abstimmung_df is not None and not abstimmung_df.is_empty():
            merged = merged.join(abstimmung_df, on="geo_id", how="left")
        else:
            merged = merged.with_columns(pl.lit(None).cast(
                pl.Float64).alias("abstimmung_value"))
    else:
        merged = merged.with_columns(pl.lit(None).cast(
            pl.Float64).alias("abstimmung_value"))

    metric_to_expr = {
        "ja_prozent": pl.col("ja_prozent").cast(pl.Float64),
        "stimmbeteiligung": pl.col("stimmbeteiligung").cast(pl.Float64),
        "anzahl_stimmberechtigte": pl.col("anzahl_stimmberechtigte").cast(pl.Float64),
        "wahlen_result": pl.col("wahlen_value").cast(pl.Float64),
        "abstimmung_result": pl.col("abstimmung_value").cast(pl.Float64),
    }

    result = merged.with_columns(
        metric_to_expr[x_metric].alias("x_value"),
        metric_to_expr[y_metric].alias("y_value"),
        metric_to_expr[size_metric].alias("size_value"),
    ).select(
        "geo_id",
        "name",
        "kanton",
        "kanton_id",
        "status",
        "ja_prozent",
        "stimmbeteiligung",
        "anzahl_stimmberechtigte",
        "wahlen_value",
        "abstimmung_value",
        "x_value",
        "y_value",
        "size_value",
    ).sort("geo_id")

    cache.set(cache_key, result.to_dicts(), timeout=120)
    return result


def import_tag(tag: Abstimmungstag):
    results, vorlagen = fetch_results_eidg(tag.url_eidg)
    store_results(results)
    store_vorlagen(vorlagen, tag)

    if tag.url_kantonal:
        results, vorlagen_kantonal = fetch_results_kantonal(tag.url_kantonal)
        store_vorlagen(vorlagen_kantonal, tag)
        store_results(results)

    from .tasks import predict_results_task

    unfinished_vorlagen = Vorlage.objects.filter(tag=tag, finished=False)
    for v in unfinished_vorlagen:
        predict_results_task.delay(v.vorlagen_id)


def fetch_results_kantonal(
    json_url,
) -> tuple[list[GemeindeResult], list[VorlageSchema]]:
    data = requests.get(json_url).json()

    date = datetime.datetime.strptime(data["abstimmtag"], "%Y%m%d")
    if date.date() < date.today().date():
        timestamp = date.replace(hour=18).timestamp()
    else:
        timestamp = datetime.datetime.now().timestamp()

    results = []
    vorlagen = []

    for kanton in data["kantone"]:
        name = kanton["geoLevelname"]
        k = Kanton.objects.filter(short=name[:2].upper()).first()
        for vorlage in kanton["vorlagen"]:
            vorlage_results = []
            has_zk = False

            for gemeinde in vorlage["gemeinden"]:
                result_data = gemeinde["resultat"]
                gemeinde_result = _convert_result_data(
                    timestamp, gemeinde, vorlage, kanton, result_data
                )
                vorlage_results.append(gemeinde_result)

            if "zaehlkreise" in kanton:
                has_zk = True
                for zaehlkreis in kanton["zaehlkreise"]:
                    result_data = zaehlkreis["resultat"]
                    gemeinde_result = _convert_result_data(
                        timestamp, zaehlkreis, vorlage, kanton, result_data
                    )
                    vorlage_results.append(gemeinde_result)

            vorlagen.append(
                VorlageSchema(
                    name=get_name(vorlage["vorlagenTitel"],
                                  k.lang_code if k else "de"),
                    vorlagen_id=int(vorlage["vorlagenId"]),
                    has_zk=has_zk,
                    finished=vorlage["vorlageBeendet"],
                    doppeltes_mehr=False,
                    angenommen=vorlage["vorlageAngenommen"] or False,
                    ja_staende=0,
                    nein_staende=0,
                    region=name,
                    result=vorlage["resultat"],
                    kantonal=True,
                )
            )
            results.extend(vorlage_results)

    return results, vorlagen


def get_final_filter(final_ids: dict[int, set[tuple[int, int, int]]]):

    def _filter_fun(r: GemeindeResult):
        if r is None or r.result is None:
            return False

        if (r.geo_id, r.result.ja_stimmen, r.result.nein_stimmen) in final_ids[
            r.vorlage_id
        ]:
            return False
        return True

    return _filter_fun


def fetch_and_store_eidg(tag: Abstimmungstag):
    results, vorlagen = fetch_results_eidg(tag.url_eidg)
    final_ids = {
        vorlage.vorlagen_id: set(get_final_geo_ids(vorlage.vorlagen_id))
        for vorlage in vorlagen
    }

    # Only store new final results
    results = list(
        filter(
            get_final_filter(final_ids),
            results,
        )
    )
    new_results_per_vorlage = defaultdict(int)

    for r in results:
        new_results_per_vorlage[r.vorlage_id] += 1

    store_results(results)
    store_vorlagen(vorlagen, tag)
    return dict(new_results_per_vorlage)


def fetch_and_store_kantonal(tag: Abstimmungstag):
    results, vorlagen = fetch_results_kantonal(tag.url_kantonal)
    final_ids = {
        vorlage.vorlagen_id: set(get_final_geo_ids(vorlage.vorlagen_id))
        for vorlage in vorlagen
    }
    results = list(
        filter(
            get_final_filter(final_ids),
            results,
        )
    )
    new_results_per_vorlage = defaultdict(int)
    for r in results:
        new_results_per_vorlage[r.vorlage_id] += 1
    store_results(results)
    store_vorlagen(vorlagen, tag)
    return dict(new_results_per_vorlage)


def fetch_results_eidg(json_url) -> tuple[list[GemeindeResult], list[VorlageSchema]]:
    data = requests.get(json_url).json()

    date = datetime.datetime.strptime(data["abstimmtag"], "%Y%m%d")
    if date.date() < date.today().date():
        timestamp = date.replace(hour=18).timestamp()
    else:
        timestamp = datetime.datetime.now().timestamp()

    results = []
    vorlagen = []

    data = data["schweiz"]["vorlagen"]

    for vorlage in data:
        vorlage_results = []
        staende = vorlage["staende"]

        has_zk = False

        for kanton in vorlage["kantone"]:
            for gemeinde in kanton["gemeinden"]:
                result_data = gemeinde["resultat"]
                gemeinde_result = _convert_result_data(
                    timestamp, gemeinde, vorlage, kanton, result_data
                )
                vorlage_results.append(gemeinde_result)

            if "zaehlkreise" in kanton:
                has_zk = True

                for zaehlkreis in kanton["zaehlkreise"]:
                    result_data = zaehlkreis["resultat"]

                    gemeinde_result = _convert_result_data(
                        timestamp, zaehlkreis, vorlage, kanton, result_data
                    )
                    vorlage_results.append(gemeinde_result)
        results.extend(vorlage_results)

        vorlagen.append(
            VorlageSchema(
                name=get_first_name(vorlage["vorlagenTitel"]),
                has_zk=has_zk,
                vorlagen_id=int(vorlage["vorlagenId"]),
                finished=vorlage["vorlageBeendet"],
                doppeltes_mehr=vorlage["doppeltesMehr"],
                angenommen=vorlage["vorlageAngenommen"] or False,
                ja_staende=(staende["jaStaendeGanz"] +
                            0.5 * staende["jaStaendeHalb"])
                if staende["jaStaendeGanz"] is not None
                else 0,
                nein_staende=(
                    staende["neinStaendeGanz"] + 0.5 *
                    staende["neinStaendeHalb"]
                )
                if staende["neinStaendeGanz"] is not None
                else 0,
                result=vorlage["resultat"],
            )
        )
    return results, vorlagen


def store_vorlagen(vorlagen: list[VorlageSchema], tag: Abstimmungstag):
    for vorlage in vorlagen:
        obj, _ = Vorlage.objects.update_or_create(
            vorlagen_id=vorlage.vorlagen_id,
            defaults={
                "name": vorlage.name,
                "finished": vorlage.finished,
                "doppeltes_mehr": vorlage.doppeltes_mehr,
                "angenommen": vorlage.angenommen,
                "ja_staende": vorlage.ja_staende,
                "nein_staende": vorlage.nein_staende,
                "has_zk": vorlage.has_zk,
                "result": vorlage.result,
                "tag": tag,
                "region": vorlage.region,
                "kantonal": vorlage.kantonal,
            },
        )
        update_vorlage(vorlage.vorlagen_id)


def store_results(results: list[GemeindeResult]):
    with get_influx_client() as client:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        points = []
        for result in results:
            if result.result is None:
                continue

            point = {
                "measurement": "result",
                "tags": {
                    "geo_id": result.geo_id,
                    "vorlage_id": result.vorlage_id,
                    "kanton": result.kanton_id,
                    "status": "final" if result.result.final else "prediction",
                },
                "fields": {
                    "final": result.result.final,
                    "ja_stimmen": result.result.ja_stimmen,
                    "nein_stimmen": result.result.nein_stimmen,
                    "anzahl_stimmberechtigte": result.result.anzahl_stimmberechtigte,
                    "ja_prozent": result.result.ja_prozent,
                    "stimmbeteiligung": result.result.stimmbeteiligung,
                },
                # InfluxDB expects time in nanoseconds by default
                "time": int(result.timestamp * 1_000_000_000),
            }
            points.append(point)
        write_api.write(bucket=settings.INFLUX_BUCKET, record=points)


def update_vorlage(abst_id):
    vorlage = Vorlage.objects.get(vorlagen_id=abst_id)
    total = get_abst_result_total(abst_id)
    total = total.rows(named=True) if total is not None else []

    projection = defaultdict(int)
    final = defaultdict(int)

    for r in total:
        if r["status"] == "prediction":
            projection = r
        elif r["status"] == "final":
            final = r

    with transaction.atomic():
        result = vorlage.result or {}
        result["jaPredicted"] = projection["ja_stimmen"]
        result["neinPredicted"] = projection["nein_stimmen"]
        result["stimmberechtigtePredicted"] = projection["anzahl_stimmberechtigte"]
        result["jaStimmenAbsolut"] = final["ja_stimmen"]
        result["neinStimmenAbsolut"] = final["nein_stimmen"]
        result["anzahlStimmberechtigte"] = final["anzahl_stimmberechtigte"]

        vorlage.result = result
        vorlage.save()


def filter_zk(abst_id) -> str:
    vorlage = Vorlage.objects.select_related("tag").get(vorlagen_id=abst_id)

    if not vorlage.has_zk:
        return ""

    zk_parents = (
        Gemeinde.objects.filter(stand=vorlage.tag.stand)
        .exclude(zaehlkreis=None)
        .values_list("geo_id", flat=True)
    )
    parts = [f'r["geo_id"] != "{geo_id}"' for geo_id in zk_parents]
    f = " and ".join(parts)

    r = f"|> filter(fn: (r) => {f})"

    return r


def get_abst_result_total(abst_id: int):

    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
            |> range(start: -100y)
            |> filter(fn: (r) => r["_measurement"] == "result")
            |> filter(fn: (r) => r["_field"] != "ja_prozent" and r["_field"] != "stimmbeteiligung" and r["_field"] != "final")
            {filter_zk(abst_id)}
            |> filter(fn: (r) => r["vorlage_id"] == "{abst_id}")
            |> group(columns: ["_field", "geo_id"])
            |> last()
            |> group(columns: ["_field", "status"])
            |> sum()
            |> pivot(rowKey: ["status"], columnKey: ["_field"], valueColumn: "_value")
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)

        if len(result) == 0:
            return None

        result = pl.from_pandas(result).select(
            "status",
            "ja_stimmen",
            "nein_stimmen",
            "anzahl_stimmberechtigte",
        )

        return result


def get_abst_result_kantone(abst_id: int):
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
            |> range(start: -100y)
            |> filter(fn: (r) => r["_measurement"] == "result")
            |> filter(fn: (r) => r["_field"] != "ja_prozent" and r["_field"] != "stimmbeteiligung" and r["_field"] != "final")
            {filter_zk(abst_id)}
            |> filter(fn: (r) => r["vorlage_id"] == "{abst_id}")
            |> group(columns: ["_field", "geo_id"])
            |> last()
            |> group(columns: ["_field", "kanton", "status"])
            |> sum()
            |> pivot(rowKey: ["kanton", "status"], columnKey: ["_field"], valueColumn: "_value")
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        if len(result) == 0:
            return pl.DataFrame()

        result = pl.from_pandas(result).select(
            "kanton",
            "status",
            "ja_stimmen",
            "nein_stimmen",
            "anzahl_stimmberechtigte",
        )

        return result


def get_abst_results(abst_id: int):
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "result" and r.vorlage_id == "{abst_id}")
          |> last()
          |> pivot(rowKey:["geo_id"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["geo_id"], desc: true)
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        result = pl.from_pandas(result)
        if len(result) == 0:
            return None

        result = result.with_columns(
            geo_id=pl.col("geo_id").cast(pl.Int32),
        ).select(
            "geo_id",
            "status",
            "anzahl_stimmberechtigte",
            "ja_stimmen",
            "nein_stimmen",
            "ja_prozent",
            "stimmbeteiligung",
        )

        result = result.sort("status").unique(subset=["geo_id"], keep="first")

        return result


def get_final_geo_ids(abst_id: int) -> list[tuple[int, int, int]]:
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "result" and r.vorlage_id == "{abst_id}" and r.status == "final")
          |> pivot(rowKey:["geo_id"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["geo_id"], desc: true)
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        if len(result) == 0:
            return []
        result = pl.from_pandas(result).select(
            geo_id=pl.col("geo_id").cast(pl.Int32),
            ja_stimmen=pl.col("ja_stimmen"),
            nein_stimmen=pl.col("nein_stimmen"),
        )

        return result.rows()


def get_abst_result_history(abst_id: int, geo_id: int):
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "result" and r.vorlage_id == "{abst_id}" and r.geo_id == "{geo_id}")
          |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"], desc: true)
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        result = (
            pl.from_pandas(result)
            .with_columns(
                time=pl.col("_time").cast(pl.Int64),
            )
            .drop("result", "table", "_start", "_stop", "_measurement")
            .sort("time")
        )

        return result


def get_stimmberechtigte():
    latest_finished_vote = (
        Vorlage.objects.filter(region="CH", finished=True)
        .order_by("-tag__date")
        .first()
    )
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -1y)
          |> filter(fn: (r) => r._measurement == "result")
          |> filter(fn: (r) => r._field == "anzahl_stimmberechtigte")
          |> filter(fn: (r) => r["vorlage_id"] == "{latest_finished_vote.vorlagen_id}")
          |> group(columns: ["geo_id"])
          |> pivot(rowKey:["geo_id"], columnKey: ["_field"], valueColumn: "_value")
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        result = (
            pl.from_pandas(result)
            .with_columns(
                geo_id=pl.col("geo_id").cast(pl.Int32),
            )
            .drop(
                "result",
                "table",
                "_start",
                "_stop",
            )
            .sort("geo_id")
        )

        return result


def get_vorlagen_table(vorlagen_ids: list[int]):
    """
    Get a table with a row for every geo_id and columns for ja_prozent and stimmbeteiligung for the specified vorlagen.
    """
    if not vorlagen_ids:
        return pl.DataFrame()

    id_filters = " or ".join(
        [f'r.vorlage_id == "{v_id}"' for v_id in vorlagen_ids])

    with get_influx_client(timeout=600000) as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "result")
          |> filter(fn: (r) => {id_filters})
          |> filter(fn: (r) => r._field == "ja_prozent" or r._field == "stimmbeteiligung")
          |> group(columns: ["geo_id", "vorlage_id", "_field"])
          |> last()
          |> keep(columns: ["geo_id", "vorlage_id", "_field", "_value"])
        '''

        import warnings

        from influxdb_client.client.warnings import MissingPivotFunction

        warnings.simplefilter("ignore", MissingPivotFunction)

        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            if len(result) == 0:
                import pandas as pd

                result = pd.DataFrame(
                    columns=["geo_id", "vorlage_id", "_field", "_value"]
                )
            else:
                import pandas as pd

                all_dfs = []
                for r in result:
                    cols = [
                        c
                        for c in ["geo_id", "vorlage_id", "_field", "_value"]
                        if c in r.columns
                    ]
                    all_dfs.append(r[cols])
                result = pd.concat(all_dfs, ignore_index=True)

    if result.empty:
        return pl.DataFrame()

    df = pl.from_pandas(result)

    # Flatten structure properly so fields end with _ja_prozent or _stimmbeteiligung
    # separator argument is available in newer polars versions
    pivoted = df.pivot(
        values="_value",
        index="geo_id",
        on=["vorlage_id", "_field"],
        separator="_",
    )

    return pivoted.with_columns(pl.col("geo_id").cast(pl.Int32)).sort("geo_id")
