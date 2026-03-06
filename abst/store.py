import datetime
from datetime import date
import pandas as pd
import polars as pl

from influxdb_client.client.influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS
import requests
from django.conf import settings
from contextlib import contextmanager

from abst.models import Abstimmungstag, GeoStand, Kanton, Vorlage

from .schema import GemeindeResult, Result, VorlageSchema


def import_abst_meta(url):
    data = requests.get(url).json()
    for resource in data["result"]["resources"]:
        coverage_date = date.fromisoformat(resource["coverage"])
        stand = GeoStand.objects.order_by("-date").first()
        url = resource["url"]
        Abstimmungstag.objects.get_or_create(
            date=coverage_date,
            defaults={
                "url_eidg": url,
                "name": resource["name"]["de"],
                "stand": stand,
            }
        )


def import_abst_kantonal_meta(url):
    data = requests.get(url).json()
    for resource in data["result"]["resources"]:
        coverage_date = date.fromisoformat(resource["coverage"])
        url = resource["url"]
        Abstimmungstag.objects.filter(
            date=coverage_date).update(url_kantonal=url)


@contextmanager
def get_influx_client(timeout=1000):
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


def _convert_result_data(timestamp, gemeinde, vorlage, kanton, result_data) -> GemeindeResult:
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
        ) if (result_data["gebietAusgezaehlt"]) else None
    )


def get_first_name(names: list) -> str:
    for name in names:
        if name['text']:
            return name['text']
    return "Unknown"


def get_name(names, lang):
    for n in names:
        if n['langKey'] == lang:
            return n['text']
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


def fetch_results_kantonal(json_url) -> tuple[list[GemeindeResult], list[VorlageSchema]]:
    data = requests.get(json_url).json()

    timestamp = datetime.datetime.now().timestamp()

    results = []
    vorlagen = []

    for kanton in data["kantone"]:
        name = kanton["geoLevelname"]
        k = Kanton.objects.filter(short=name[:2].upper()).first()
        for vorlage in kanton["vorlagen"]:
            vorlagen.append(
                VorlageSchema(
                    name=get_name(vorlage["vorlagenTitel"],
                                  k.lang_code if k else "de"),
                    vorlagen_id=int(vorlage["vorlagenId"]),
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
            vorlage_results = []
            remove_ids = set()

            for gemeinde in vorlage["gemeinden"]:
                result_data = gemeinde["resultat"]
                gemeinde_result = _convert_result_data(
                    timestamp, gemeinde, vorlage, kanton, result_data)
                vorlage_results.append(gemeinde_result)

            if "zaehlkreise" in kanton:
                for zaehlkreis in kanton["zaehlkreise"]:
                    result_data = zaehlkreis["resultat"]
                    remove_ids.add(int(zaehlkreis["geoLevelParentnummer"]))
                    gemeinde_result = _convert_result_data(
                        timestamp, zaehlkreis, vorlage, kanton, result_data)
                    vorlage_results.append(gemeinde_result)
            vorlage_results = [
                r for r in vorlage_results if r.geo_id not in remove_ids]
            results.extend(vorlage_results)

    return results, vorlagen


def fetch_results_eidg(json_url) -> tuple[list[GemeindeResult], list[VorlageSchema]]:
    data = requests.get(json_url).json()["schweiz"]["vorlagen"]

    # 20260308
    date = datetime.datetime.strptime(data["abstimmtag"], "%Y%m%d")
    if date.date() < date.today():
        timestamp = date.replace(hour=18).timestamp()
    else:
        timestamp = datetime.datetime.now().timestamp()

    results = []
    vorlagen = []

    for vorlage in data:
        vorlage_results = []
        remove_ids = set()
        staende = vorlage["staende"]

        vorlagen.append(VorlageSchema(
            name=get_first_name(vorlage["vorlagenTitel"]),
            vorlagen_id=int(vorlage["vorlagenId"]),
            finished=vorlage["vorlageBeendet"],
            doppeltes_mehr=vorlage["doppeltesMehr"],
            angenommen=vorlage["vorlageAngenommen"] or False,
            ja_staende=(staende["jaStaendeGanz"] +
                        0.5 * staende["jaStaendeHalb"]) if staende["jaStaendeGanz"] is not None else 0,
            nein_staende=(staende["neinStaendeGanz"] +
                          0.5 * staende["neinStaendeHalb"]) if staende["neinStaendeGanz"] is not None else 0,
            result=vorlage["resultat"]
        ))

        for kanton in vorlage["kantone"]:
            for gemeinde in kanton["gemeinden"]:
                result_data = gemeinde["resultat"]
                gemeinde_result = _convert_result_data(
                    timestamp, gemeinde, vorlage, kanton, result_data)
                vorlage_results.append(gemeinde_result)

            if "zaehlkreise" in kanton:

                for zaehlkreis in kanton["zaehlkreise"]:
                    result_data = zaehlkreis["resultat"]
                    remove_ids.add(int(zaehlkreis["geoLevelParentnummer"]))

                    gemeinde_result = _convert_result_data(
                        timestamp, zaehlkreis, vorlage, kanton, result_data)
                    vorlage_results.append(gemeinde_result)
        vorlage_results = [
            r for r in vorlage_results if r.geo_id not in remove_ids]
        results.extend(vorlage_results)
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
                "result": vorlage.result,
                "tag": tag,
                "region": vorlage.region,
                "kantonal": vorlage.kantonal,
            }
        )


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


def get_abst_result_total(abst_id: int):
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
            |> range(start: -100y)
            |> filter(fn: (r) => r["_measurement"] == "result")
            |> filter(fn: (r) => r["_field"] != "ja_prozent" and r["_field"] != "stimmbeteiligung")
            |> filter(fn: (r) => r["vorlage_id"] == "{abst_id}")
            |> group(columns: ["_field", "status"])
            |> sum()
            |> pivot(rowKey: ["status"], columnKey: ["_field"], valueColumn: "_value")
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        result = pl.from_pandas(result).drop(
            'table', "result", '_start', '_stop')

        return result


def get_abst_result_kantone(abst_id: int):
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
            |> range(start: -100y)
            |> filter(fn: (r) => r["_measurement"] == "result")
            |> filter(fn: (r) => r["_field"] != "ja_prozent" and r["_field"] != "stimmbeteiligung")
            |> filter(fn: (r) => r["vorlage_id"] == "{abst_id}")
            |> group(columns: ["_field", "kanton", "status"])
            |> sum()
            |> pivot(rowKey: ["kanton", "status"], columnKey: ["_field"], valueColumn: "_value")
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        result = pl.from_pandas(result).drop(
            'table', "result", '_start', '_stop')

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
        result = pl.from_pandas(result).with_columns(
            geo_id=pl.col("geo_id").cast(pl.Int32),
        ).drop('result', 'table', 'kanton', '_start', '_stop', '_measurement').sort("geo_id")

        return result


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
        result = pl.from_pandas(result).with_columns(
            time=pl.col("_time").cast(pl.Int64),
        ).drop('result', 'table', '_start', '_stop', '_measurement').sort("time")

        return result


def get_stimmberechtigte():
    with get_influx_client() as client:
        query_api = client.query_api()
        query = f'''
        from(bucket: "{settings.INFLUX_BUCKET}")
          |> range(start: -100y)
          |> filter(fn: (r) => r._measurement == "result")
          |> filter(fn: (r) => r._field == "anzahl_stimmberechtigte")
          |> group(columns: ["geo_id"])
          |> last()
          |> pivot(rowKey:["geo_id"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["geo_id"], desc: true)
        '''
        result = query_api.query_data_frame(query)
        if isinstance(result, list):
            result = pd.concat(result)
        result = pl.from_pandas(result).with_columns(
            geo_id=pl.col("geo_id").cast(pl.Int32),
        ).drop('result', 'table', '_start', '_stop', ).sort("geo_id")

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
                    columns=["geo_id", "vorlage_id", "_field", "_value"])
            else:
                import pandas as pd
                all_dfs = []
                for r in result:
                    cols = [c for c in ['geo_id', 'vorlage_id',
                                        '_field', '_value'] if c in r.columns]
                    all_dfs.append(r[cols])
                result = pd.concat(all_dfs, ignore_index=True)

        if result.empty:
            return pl.DataFrame()

        df = pl.from_pandas(result)

        try:
            # Flatten structure properly so fields end with _ja_prozent or _stimmbeteiligung
            # separator argument is available in newer polars versions
            pivoted = df.pivot(
                values="_value",
                index="geo_id",
                on=["vorlage_id", "_field"],
                separator="_"
            )
        except TypeError:
            # Fallback if separator is not supported
            pivoted = df.pivot(
                values="_value",
                index="geo_id",
                on=["vorlage_id", "_field"]
            )

            # Format column names properly if they are dumped as structs/tuples
            new_cols = []
            for col in pivoted.columns:
                if getattr(col, '__iter__', False) and not isinstance(col, str):
                    try:
                        # Polars drops struct names like `{"6800","ja_prozent"}` as `{"6800","ja_prozent"}` instead of proper strings
                        val = str(col).replace('{', '').replace(
                            '}', '').replace('"', '').split(',')
                        new_cols.append(f"{val[0].strip()}_{val[1].strip()}")
                    except:
                        new_cols.append(str(col))
                elif col.startswith('{') and col.endswith('}'):
                    val = col.replace('{', '').replace(
                        '}', '').replace('"', '').split(',')
                    new_cols.append(f"{val[0].strip()}_{val[1].strip()}")
                else:
                    new_cols.append(col)
            pivoted.columns = new_cols

        return pivoted.with_columns(
            pl.col("geo_id").cast(pl.Int32)
        ).sort("geo_id")
