import datetime
import io
from logging import getLogger

import numpy as np
import polars as pl
from django.core.files.base import ContentFile
from sklearn.decomposition import TruncatedSVD

from abst.geo import get_geo_id_list
from abst.models import Abstimmungstag, Gemeinde, Kanton, Vorlage, Zaehlkreis

from .schema import Result
from .store import (
    GemeindeResult,
    get_abst_results,
    get_stimmberechtigte,
    get_vorlagen_table,
)

logger = getLogger(__name__)

_predict_cache = {}
_prepare_data_cache = {}
_projection_cache = {}


def prepare_predict_data(
    abst_id: int,
) -> tuple[list[float], list[float], list[bool], list[int]]:
    if abst_id not in _prepare_data_cache:
        vorlage = Vorlage.objects.get(vorlagen_id=abst_id)
        results = get_abst_results(abst_id)
        geo_ids = get_geo_id_list(vorlage.tag.stand)

        df_geo = pl.DataFrame({"geo_id": geo_ids})

        if results is None or len(results) == 0:
            _prepare_data_cache[abst_id] = (
                [0.0] * len(geo_ids),
                [0.0] * len(geo_ids),
                [True] * len(geo_ids),
                geo_ids,
            )
        else:
            df_results = pl.DataFrame(results).unique(subset=["geo_id"])
            df = df_geo.join(df_results, on="geo_id", how="left").sort("geo_id")

            ja_values = df["ja_prozent"].fill_null(0.0).to_list()
            beteiligung_values = df["stimmbeteiligung"].fill_null(0.0).to_list()
            mask = (~df["status"].fill_null("missing").eq("final")).to_list()

            _prepare_data_cache[abst_id] = (ja_values, beteiligung_values, mask, geo_ids)

    return _prepare_data_cache[abst_id]


def predict_missing_results(
    projection, results: list[float], mask: list[bool], alpha: float = 50.0
) -> list[float]:
    """Predicts the missing results based on the available data and a boolean mask."""
    proj_array = np.array(projection)
    results_array = np.array(results)
    mask_array = np.array(mask)

    basis_known = proj_array[~mask_array]
    y_known = results_array[~mask_array]

    if len(y_known) == 0:
        return np.zeros(len(results)).tolist()

    # Use Ridge Regression (L2 regularization) to prevent coefficient instability (multicollinearity)
    # when the number of known observations is close to the number of components (21 features).
    XTX = basis_known.T @ basis_known
    XTy = basis_known.T @ y_known
    A = XTX + alpha * np.eye(XTX.shape[0])
    coeffs = np.linalg.solve(A, XTy)
    
    y_pred = proj_array @ coeffs

    # Only replace missing values and clip to valid percentage range [0.0, 100.0]
    y_final = results_array.copy()
    y_final[mask_array] = np.clip(y_pred[mask_array], 0.0, 100.0)

    return y_final.tolist()


def create_models(abstimmungstag: Abstimmungstag, n: int = 100):
    """Creates the models for the given GeoStand

    For every geo_id in the geo_id_list get the result for the last 100 vorlagen (yes and beteiligung), then
    create a sub-matrix factorization for all the geo_ids and vorlagen, and store the two resulting matrices as the projection and projection_bet of the GeoStand.

    """
    stand = abstimmungstag.stand
    geo_ids = get_geo_id_list(stand)
    df_geo = pl.DataFrame({"geo_id": geo_ids})

    latest_vorlagen = Vorlage.objects.filter(kantonal=False, finished=True).order_by(
        "-tag__date"
    )[:n]
    if not latest_vorlagen.exists():
        return

    vorlagen_ids = list(latest_vorlagen.values_list("vorlagen_id", flat=True))

    df_abst = get_vorlagen_table(vorlagen_ids)
    if df_abst.is_empty():
        return

    df = df_geo.join(df_abst, on="geo_id", how="left").sort("geo_id")

    ja_cols = [col for col in df.columns if "ja_prozent" in col]
    bet_cols = [col for col in df.columns if "stimmbeteiligung" in col]

    def get_projection(cols, n_comp=20):
        if not cols:
            return np.ones((len(geo_ids), 1))
        X = df.select(cols).to_numpy()
        col_means = np.nanmean(X, axis=0)
        X_filled = np.where(np.isnan(X), col_means, X)
        svd = TruncatedSVD(n_components=min(n_comp, len(cols)), random_state=42)
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
    tag.projection.save(f"ja_proj_{tag.pk}.npy", ContentFile(ja_bytes.read()))

    bet_bytes = io.BytesIO()
    np.save(bet_bytes, U_S_bet)
    bet_bytes.seek(0)
    if tag.projection_bet:
        tag.projection_bet.delete(save=False)
    tag.projection_bet.save(f"bet_proj_{tag.pk}.npy", ContentFile(bet_bytes.read()))

    tag.save()


def predict_results(
    abst_id: int, known_geo_ids: list[int] | None = None
) -> list[GemeindeResult] | None:
    vorlage = Vorlage.objects.get(vorlagen_id=abst_id)
    if vorlage.finished and not known_geo_ids:
        # No need to predict if the vote is already finished and no known_geo_ids are provided
        return None

    if (
        vorlage.tag.projection is None
        or not vorlage.tag.projection.name
        or vorlage.tag.projection_bet is None
        or not vorlage.tag.projection_bet.name
    ):
        return None

    tag_id = vorlage.tag_id
    if tag_id not in _projection_cache:
        try:
            proj_ja = np.load(vorlage.tag.projection.open("rb"))
            proj_bet = np.load(vorlage.tag.projection_bet.open("rb"))
            _projection_cache[tag_id] = (proj_ja, proj_bet)
        except Exception as e:
            print(f"Error loading projections: {e}")
            return None

    projection_ja, projection_bet = _projection_cache[tag_id]

    ja_values, bet_values, mask, geo_ids = prepare_predict_data(abst_id)

    if known_geo_ids is not None:
        known_set = set(known_geo_ids)
        mask = [gid not in known_set for g_id, gid in enumerate(geo_ids)]

    if not any(mask) or all(mask):
        return None

    y_ja_pred = predict_missing_results(projection_ja, ja_values, mask)
    y_bet_pred = predict_missing_results(projection_bet, bet_values, mask)

    timestamp = datetime.datetime.now().timestamp()

    cache_key = vorlage.tag.stand_id
    if cache_key not in _predict_cache:
        g_dict = {
            g.geo_id: g.kanton_id for g in Gemeinde.objects.filter(stand=vorlage.tag.stand)
        }
        z_dict = {
            z.geo_id: z.gemeinde.kanton_id
            for z in Zaehlkreis.objects.filter(gemeinde__stand=vorlage.tag.stand)
        }
        df_stimmberechtigte = get_stimmberechtigte()
        s_dict = dict(
            zip(
                df_stimmberechtigte["geo_id"].to_list(),
                df_stimmberechtigte["anzahl_stimmberechtigte"].to_list(),
            )
        )
        _predict_cache[cache_key] = (g_dict, z_dict, s_dict)

    gemeinden, zaehlkreise, stimm_dict = _predict_cache[cache_key]

    if vorlage.kantonal:
        used_geo_ids = get_geo_id_list(
            vorlage.tag.stand,
            kanton_id=Kanton.objects.get(short=vorlage.region).kanton_id,
        )
    else:
        used_geo_ids = get_geo_id_list(vorlage.tag.stand)

    results = []
    for i, geo_id in enumerate(geo_ids):
        if not mask[i]:
            continue
        if geo_id not in used_geo_ids:
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
            ),
        )
        results.append(res)

    return results


def get_cached_historical_data(tag) -> pl.DataFrame:
    from django.core.cache import cache
    cache_key = f"hist_records:{tag.date.isoformat()}"
    cached_bytes = cache.get(cache_key)
    if cached_bytes is not None:
        try:
            return pl.read_ipc(io.BytesIO(cached_bytes))
        except Exception as e:
            print(f"Error reading cached IPC data: {e}")
        
    # Fallback if cache is empty or corrupt
    historical_vorlagen = Vorlage.objects.filter(
        kantonal=False,
        finished=True,
        tag__date__lt=tag.date
    ).order_by("-tag__date")[:50]
    hist_ids = list(historical_vorlagen.values_list("vorlagen_id", flat=True))
    if not hist_ids:
        return pl.DataFrame()
        
    df_hist = get_vorlagen_table(hist_ids)
    if df_hist.is_empty():
        return df_hist
        
    # Store in cache
    try:
        bio = io.BytesIO()
        df_hist.write_ipc(bio)
        cache.set(cache_key, bio.getvalue(), timeout=86400 * 7)
        
        # Update registry
        registry = cache.get("hist_records_registry", [])
        if cache_key not in registry:
            registry.append(cache_key)
            cache.set("hist_records_registry", registry)
    except Exception as e:
        print(f"Error caching historical data: {e}")
        
    return df_hist


def get_confidence_percentiles(tag, mask, geo_ids, stimm_weights, weight_mask):
    tag_id = tag.id
    if tag_id not in _projection_cache:
        proj_ja = np.load(tag.projection.open("rb"))
        proj_bet = np.load(tag.projection_bet.open("rb"))
        _projection_cache[tag_id] = (proj_ja, proj_bet)
    projection_ja, projection_bet = _projection_cache[tag_id]
    
    # Slice projections to match the passed geo_ids
    full_geo_ids = get_geo_id_list(tag.stand)
    geo_id_to_idx = {gid: idx for idx, gid in enumerate(full_geo_ids)}
    indices = [geo_id_to_idx[gid] for gid in geo_ids]
    projection_ja = projection_ja[indices, :]
    projection_bet = projection_bet[indices, :]
    
    df_hist = get_cached_historical_data(tag)
    if df_hist.is_empty():
        return None
        
    # Extract historical IDs from columns
    hist_ids = []
    for col in df_hist.columns:
        if "ja_prozent" in col:
            try:
                # col is like '{"6290","ja_prozent"}'
                vid = int(col.split(",")[0].replace('{"', '').replace('"', ''))
                hist_ids.append(vid)
            except Exception:
                pass
                
    ja_cols = []
    bet_cols = []
    valid_vids = []
    for vid in hist_ids:
        ja_col = f'{{"{vid}","ja_prozent"}}'
        bet_col = f'{{"{vid}","stimmbeteiligung"}}'
        if ja_col in df_hist.columns and bet_col in df_hist.columns:
            ja_cols.append(ja_col)
            bet_cols.append(bet_col)
            valid_vids.append(vid)
            
    if not valid_vids:
        return None
        
    df_geo = pl.DataFrame({"geo_id": geo_ids})
    df_hist_aligned = df_geo.join(df_hist, on="geo_id", how="left").sort("geo_id")
    
    X_ja_all = df_hist_aligned.select(ja_cols).fill_null(0.0).to_numpy()
    X_bet_all = df_hist_aligned.select(bet_cols).fill_null(0.0).to_numpy()
    
    known_mask = ~np.array(mask, dtype=bool)
    
    alpha = 50.0
    basis_known_ja = projection_ja[known_mask]
    basis_known_bet = projection_bet[known_mask]
    
    y_known_ja = X_ja_all[known_mask, :]
    y_known_bet = X_bet_all[known_mask, :]
    
    XTX_ja = basis_known_ja.T @ basis_known_ja
    A_ja = XTX_ja + alpha * np.eye(XTX_ja.shape[0])
    coeffs_ja = np.linalg.solve(A_ja, basis_known_ja.T @ y_known_ja)
    
    XTX_bet = basis_known_bet.T @ basis_known_bet
    A_bet = XTX_bet + alpha * np.eye(XTX_bet.shape[0])
    coeffs_bet = np.linalg.solve(A_bet, basis_known_bet.T @ y_known_bet)
    
    y_pred_ja = np.clip(projection_ja @ coeffs_ja, 0.0, 100.0)
    y_pred_bet = np.clip(projection_bet @ coeffs_bet, 0.0, 100.0)
    
    y_pred_ja[known_mask, :] = y_known_ja
    y_pred_bet[known_mask, :] = y_known_bet
    
    act_voters = stimm_weights[:, np.newaxis] * (X_bet_all / 100.0)
    act_yes_votes = act_voters * (X_ja_all / 100.0)
    
    pred_voters = stimm_weights[:, np.newaxis] * (y_pred_bet / 100.0)
    pred_yes_votes = pred_voters * (y_pred_ja / 100.0)
    
    act_voters_sum = np.sum(act_voters[weight_mask, :], axis=0)
    act_yes_votes_sum = np.sum(act_yes_votes[weight_mask, :], axis=0)
    
    pred_voters_sum = np.sum(pred_voters[weight_mask, :], axis=0)
    pred_yes_votes_sum = np.sum(pred_yes_votes[weight_mask, :], axis=0)
    
    act_yes_nat = (act_yes_votes_sum / act_voters_sum * 100.0)
    pred_yes_nat = (pred_yes_votes_sum / pred_voters_sum * 100.0)
    
    errors = np.abs(act_yes_nat - pred_yes_nat)
    
    p_50, p_90 = np.percentile(errors, [50, 90])
    mae = np.mean(errors)
    
    return {
        "p_10": -p_90,
        "p_25": -p_50,
        "p_75": p_50,
        "p_90": p_90,
        "mae": mae
    }


def predict_and_store(abst_id: int):
    results = predict_results(abst_id)
    if not results:
        return

    from .store import store_results, store_national_summary
    store_results(results)

    try:
        vorlage = Vorlage.objects.get(vorlagen_id=abst_id)
        tag = vorlage.tag

        ja_values, bet_values, mask, geo_ids = prepare_predict_data(abst_id)

        # If it is a cantonal vote, restrict all calculation inputs to only the canton's municipalities
        if vorlage.kantonal:
            canton_geo_ids = set(get_geo_id_list(
                tag.stand,
                kanton_id=Kanton.objects.get(short=vorlage.region).kanton_id,
            ))
            filter_indices = [i for i, gid in enumerate(geo_ids) if gid in canton_geo_ids]
            ja_values = [ja_values[i] for i in filter_indices]
            bet_values = [bet_values[i] for i in filter_indices]
            mask = [mask[i] for i in filter_indices]
            geo_ids = [geo_ids[i] for i in filter_indices]
        known_mask = ~np.array(mask, dtype=bool)

        pred_ja_dict = {r.geo_id: r.result.ja_prozent for r in results if r.result}
        pred_bet_dict = {r.geo_id: r.result.stimmbeteiligung for r in results if r.result}

        y_ja_pred = []
        y_bet_pred = []
        for i, gid in enumerate(geo_ids):
            if mask[i]:
                y_ja_pred.append(pred_ja_dict.get(gid, 0.0))
                y_bet_pred.append(pred_bet_dict.get(gid, 0.0))
            else:
                y_ja_pred.append(ja_values[i])
                y_bet_pred.append(bet_values[i])

        df_stimm = get_stimmberechtigte()
        stimm_dict = dict(zip(df_stimm["geo_id"].to_list(), df_stimm["anzahl_stimmberechtigte"].to_list()))
        stimm_weights = np.array([stimm_dict.get(gid, 0) for gid in geo_ids])

        zk_parents = set(Gemeinde.objects.filter(stand=tag.stand).exclude(zaehlkreis=None).values_list("geo_id", flat=True))
        weight_mask = np.array([gid not in zk_parents for gid in geo_ids], dtype=bool)

        # Counted
        counted_voters = stimm_weights * (np.array(bet_values) / 100.0)
        counted_yes_votes = counted_voters * (np.array(ja_values) / 100.0)

        counted_voters_sum = np.sum(counted_voters[weight_mask & known_mask])
        counted_yes_votes_sum = np.sum(counted_yes_votes[weight_mask & known_mask])
        stimm_weights_known_sum = np.sum(stimm_weights[weight_mask & known_mask])

        counted_yes_nat = (counted_yes_votes_sum / counted_voters_sum * 100.0) if counted_voters_sum > 0 else 0.0
        counted_bet_nat = (counted_voters_sum / stimm_weights_known_sum * 100.0) if stimm_weights_known_sum > 0 else 0.0

        # Projected
        projected_voters = stimm_weights * (np.array(y_bet_pred) / 100.0)
        projected_yes_votes = projected_voters * (np.array(y_ja_pred) / 100.0)

        projected_voters_sum = np.sum(projected_voters[weight_mask])
        projected_yes_votes_sum = np.sum(projected_yes_votes[weight_mask])
        stimm_weights_total_sum = np.sum(stimm_weights[weight_mask])

        projected_yes_nat = (projected_yes_votes_sum / projected_voters_sum * 100.0) if projected_voters_sum > 0 else 0.0
        projected_bet_nat = (projected_voters_sum / stimm_weights_total_sum * 100.0) if stimm_weights_total_sum > 0 else 0.0

        # Confidence
        ci_10, ci_25, ci_75, ci_90, mae = projected_yes_nat, projected_yes_nat, projected_yes_nat, projected_yes_nat, 0.0
        try:
            percentiles = get_confidence_percentiles(tag, mask, geo_ids, stimm_weights, weight_mask)
            if percentiles:
                ci_10 = projected_yes_nat + percentiles["p_10"]
                ci_25 = projected_yes_nat + percentiles["p_25"]
                ci_75 = projected_yes_nat + percentiles["p_75"]
                ci_90 = projected_yes_nat + percentiles["p_90"]
                mae = percentiles["mae"]
        except Exception as e:
            print(f"Error calculating confidence percentiles: {e}")

        timestamp = datetime.datetime.now().timestamp()
        store_national_summary(
            vorlage_id=abst_id,
            timestamp=timestamp,
            counted_yes=counted_yes_nat,
            counted_bet=counted_bet_nat,
            projected_yes=projected_yes_nat,
            projected_bet=projected_bet_nat,
            ci_10=ci_10,
            ci_25=ci_25,
            ci_75=ci_75,
            ci_90=ci_90,
            mae=mae
        )
    except Exception as e:
        print(f"Error storing national prediction summary: {e}")
