import datetime
from collections import defaultdict
from contextlib import contextmanager
from datetime import date

import pandas as pd
import polars as pl
import requests
from django.conf import settings
from django.db import transaction
from influxdb_client.client.influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from abst.models import Abstimmungstag, Gemeinde, GeoStand, Kanton, Vorlage

from .schema import GemeindeResult, Result, VorlageSchema

BASE_URL = "https://ckan.opendata.swiss/api/3/action/package_show"


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

    projection = defaultdict(int)

    for r in total.rows(named=True):
        if r["status"] == "predicted":
            projection = r

    with transaction.atomic():
        result = vorlage.result or {}
        result["jaPredicted"] = projection["ja_stimmen"]
        result["neinPredicted"] = projection["nein_stimmen"]
        result["stimmberechtigtePredicted"] = projection["anzahl_stimmberechtigte"]

        vorlage.result = result
        vorlage.save(update_fields=["result"])


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
