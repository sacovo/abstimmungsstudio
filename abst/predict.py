import io
import datetime
import time
import numpy as np
from django.conf import settings
from sklearn.decomposition import TruncatedSVD
from django.core.files.base import ContentFile
from abst.geo import get_geo_id_list
import polars as pl
from abst.models import GeoStand, Kanton, Vorlage, Abstimmungstag, Gemeinde, Zaehlkreis
from influxdb_client.client.write_api import SYNCHRONOUS

from .store import GemeindeResult, get_abst_results, get_influx_client, get_vorlagen_table, get_stimmberechtigte
from .schema import Result


def prepare_predict_data(abst_id: int) -> tuple[list[float], list[float], list[bool], list[int]]:
    vorlage = Vorlage.objects.get(vorlagen_id=abst_id)
    results = get_abst_results(abst_id)

    if vorlage.kantonal:
        kanton = Kanton.objects.get(short=vorlage.region)
        geo_ids = get_geo_id_list(
            vorlage.tag.stand, kanton_id=kanton.kanton_id)
    else:
        geo_ids = get_geo_id_list(vorlage.tag.stand)

    df_geo = pl.DataFrame({"geo_id": geo_ids})

    df_results = pl.DataFrame(results).unique(subset=["geo_id"])
    df = df_geo.join(df_results, on="geo_id", how="left").sort("geo_id")

    ja_values = df["ja_prozent"].fill_null(0.0).to_list()
    beteiligung_values = df["stimmbeteiligung"].fill_null(0.0).to_list()
    mask = df["ja_prozent"].is_null().to_list()

    return ja_values, beteiligung_values, mask, geo_ids


def predict_missing_results(projection, results: list[float], mask: list[bool]) -> list[float]:
    """Predicts the missing results based on the available data and a boolean mask."""
    proj_array = np.array(projection)
    results_array = np.array(results)
    mask_array = np.array(mask)

    basis_known = proj_array[~mask_array]
    y_known = results_array[~mask_array]

    if len(y_known) == 0:
        return np.zeros(len(results)).tolist()

    coeffs, _, _, _ = np.linalg.lstsq(basis_known, y_known, rcond=None)
    y_pred = proj_array @ coeffs

    # Only replace missing values
    y_final = results_array.copy()
    y_final[mask_array] = y_pred[mask_array]

    return y_final.tolist()


def create_models(abstimmungstag: Abstimmungstag):
    """Creates the models for the given GeoStand

    For every geo_id in the geo_id_list get the result for the last 100 vorlagen (yes and beteiligung), then
    create a sub-matrix factorization for all the geo_ids and vorlagen, and store the two resulting matrices as the projection and projection_bet of the GeoStand.

    """
    stand = abstimmungstag.stand
    geo_ids = get_geo_id_list(stand)
    df_geo = pl.DataFrame({"geo_id": geo_ids})

    latest_vorlagen = Vorlage.objects.filter(
        kantonal=False, finished=True).order_by("-vorlagen_id")[:15]
    if not latest_vorlagen.exists():
        return

    vorlagen_ids = list(latest_vorlagen.values_list("vorlagen_id", flat=True))

    df_abst = get_vorlagen_table(vorlagen_ids)
    if df_abst.is_empty():
        return

    df = df_geo.join(df_abst, on="geo_id", how="left").sort("geo_id")

    ja_cols = [col for col in df.columns if col.endswith("_ja_prozent")]
    bet_cols = [col for col in df.columns if col.endswith("_stimmbeteiligung")]

    def get_projection(cols, n_comp=20):
        if not cols:
            return np.ones((len(geo_ids), 1))
        X = df.select(cols).to_numpy()
        col_means = np.nanmean(X, axis=0)
        X_filled = np.where(np.isnan(X), col_means, X)
        svd = TruncatedSVD(n_components=min(
            n_comp, len(cols)), random_state=42)
        # Pad with constant 1 feature to catch global mean differences
        U_S = svd.fit_transform(X_filled)
        return np.hstack((np.ones((len(geo_ids), 1)), U_S))

    U_S_ja = get_projection(ja_cols)
    U_S_bet = get_projection(bet_cols)

    tag = abstimmungstag

    ja_bytes = io.BytesIO()
    np.save(ja_bytes, U_S_ja)
    ja_bytes.seek(0)
    if tag.projection:
        tag.projection.delete(save=False)
    tag.projection.save(
        f"ja_proj_{tag.pk}.npy", ContentFile(ja_bytes.read()))

    bet_bytes = io.BytesIO()
    np.save(bet_bytes, U_S_bet)
    bet_bytes.seek(0)
    if tag.projection_bet:
        tag.projection_bet.delete(save=False)
    tag.projection_bet.save(
        f"bet_proj_{tag.pk}.npy", ContentFile(bet_bytes.read()))

    tag.save()


def predict_results(abst_id: int, known_geo_ids: list[int] | None = None) -> list[GemeindeResult] | None:
    vorlage = Vorlage.objects.get(vorlagen_id=abst_id)
    if vorlage.finished and not known_geo_ids:
        # No need to predict if the vote is already finished and no known_geo_ids are provided
        return None

    if vorlage.tag.projection is None or not vorlage.tag.projection.name or vorlage.tag.projection_bet is None or not vorlage.tag.projection_bet.name:
        return None

    t0 = time.time()
    try:
        projection_ja = np.load(vorlage.tag.projection.open("rb"))
        projection_bet = np.load(vorlage.tag.projection_bet.open("rb"))
    except Exception:
        print("Error loading projections")
        return None

    ja_values, bet_values, mask, geo_ids = prepare_predict_data(abst_id)

    if known_geo_ids is not None:
        known_set = set(known_geo_ids)
        mask = [gid not in known_set for g_id, gid in enumerate(geo_ids)]

    if not any(mask) or all(mask):
        return None

    y_ja_pred = predict_missing_results(projection_ja, ja_values, mask)
    y_bet_pred = predict_missing_results(projection_bet, bet_values, mask)

    timestamp = datetime.datetime.now().timestamp()

    gemeinden = {g.geo_id: g.kanton_id for g in Gemeinde.objects.filter(
        stand=vorlage.tag.stand)}
    zaehlkreise = {z.geo_id: z.gemeinde.kanton_id for z in Zaehlkreis.objects.filter(
        gemeinde__stand=vorlage.tag.stand)}

    df_stimmberechtigte = get_stimmberechtigte()
    stimm_dict = dict(zip(df_stimmberechtigte["geo_id"].to_list(
    ), df_stimmberechtigte["anzahl_stimmberechtigte"].to_list()))

    results = []
    for i, geo_id in enumerate(geo_ids):
        if not mask[i]:
            continue

        kanton_id = gemeinden.get(geo_id) or zaehlkreise.get(geo_id) or 0

        anzahl = stimm_dict.get(geo_id, 0)
        ja_p = float(y_ja_pred[i])
        bet_p = float(y_bet_pred[i])

        gueltige_stimmen = int(round(anzahl * bet_p / 100.0))
        ja_stimmen = int(round(gueltige_stimmen * ja_p / 100.0))
        nein_stimmen = gueltige_stimmen - ja_stimmen

        res = GemeindeResult(
            timestamp=timestamp,
            geo_id=geo_id,
            vorlage_id=abst_id,
            geo_name="",
            kanton="",
            kanton_id=kanton_id,
            result=Result(
                final=False,
                ja_stimmen=ja_stimmen,
                nein_stimmen=nein_stimmen,
                anzahl_stimmberechtigte=int(anzahl),
                ja_prozent=ja_p,
                stimmbeteiligung=bet_p,
            )
        )
        results.append(res)

    print(f"Prediction time: {time.time() - t0}")
    return results


def predict_and_store(abst_id: int):
    results = predict_results(abst_id)
    if results:
        from .store import store_results
        store_results(results)
