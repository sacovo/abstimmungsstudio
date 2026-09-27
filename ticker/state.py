"""Build a compact snapshot of where a Vorlage currently stands.

Everything the ticker reasons about comes from here. The snapshot is small on
purpose: it is stored with every post (so the next post can be a delta) and it
is what gets shown to the agent, so it has to stay readable.
"""

import datetime

from abst.models import Gemeinde, Kanton, Vorlage
from abst.store import (
    get_abst_results,
    get_abst_result_kantone,
    get_national_timeline,
    get_residuals_data,
)

# A Gemeinde counts as "large" for its scope if it holds at least this share of
# the electorate, or if it is among the N biggest. Derived per vote rather than
# hardcoded, so cantonal votes get their own relevant places (Allschwil in BL,
# not Zürich).
LARGE_SHARE = 0.02
LARGE_TOP_N = 8

# An outlier needs both a meaningful deviation and enough weight to matter.
OUTLIER_RESIDUAL_PP = 4.0
OUTLIER_MIN_VOTERS = 3000
OUTLIER_LIMIT = 5


def _round(value, digits=2):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def short_name(name: str, limit: int = 70) -> str:
    """Vorlage titles are long enough to drown the CLI output."""
    name = name.strip()
    if len(name) <= limit:
        return name
    return name[: limit - 1].rstrip() + "…"


def _gemeinde_names(vorlage: Vorlage) -> dict[int, str]:
    gemeinden = Gemeinde.objects.filter(stand=vorlage.tag.stand)
    if vorlage.kantonal:
        kanton = Kanton.objects.filter(short=vorlage.region).first()
        if kanton is not None:
            gemeinden = gemeinden.filter(kanton_id=kanton.kanton_id)
    return dict(gemeinden.values_list("geo_id", "name"))


def _kanton_shorts() -> dict[int, str]:
    return dict(Kanton.objects.values_list("kanton_id", "short"))


def _counting(results) -> dict:
    """How much of the vote is actually counted, by Gemeinde and by electorate."""
    rows = results.to_dicts()
    total_voters = sum(r.get("anzahl_stimmberechtigte") or 0 for r in rows)
    final = [r for r in rows if r.get("status") == "final"]
    final_voters = sum(r.get("anzahl_stimmberechtigte") or 0 for r in final)

    return {
        "gemeinden_final": len(final),
        "gemeinden_total": len(rows),
        "total_voters": total_voters,
        "counted_share": _round(
            100.0 * final_voters / total_voters if total_voters else 0.0, 1
        ),
    }


def _large_gemeinden(results, names: dict[int, str], residuals: dict) -> list[dict]:
    rows = [r for r in results.to_dicts() if (r.get("anzahl_stimmberechtigte") or 0) > 0]
    if not rows:
        return []

    total = sum(r["anzahl_stimmberechtigte"] for r in rows)
    rows.sort(key=lambda r: r["anzahl_stimmberechtigte"], reverse=True)

    selected = []
    for idx, row in enumerate(rows):
        share = row["anzahl_stimmberechtigte"] / total if total else 0
        if idx < LARGE_TOP_N or share >= LARGE_SHARE:
            selected.append(row)
        else:
            break

    out = []
    for row in selected:
        geo_id = row["geo_id"]
        residual = residuals.get(geo_id)
        out.append(
            {
                "geo_id": geo_id,
                "name": names.get(geo_id, str(geo_id)),
                "status": row.get("status"),
                "yes": _round(row.get("ja_prozent"), 1),
                "bet": _round(row.get("stimmbeteiligung"), 1),
                "voters": row["anzahl_stimmberechtigte"],
                "residual": _round(residual["residual_ja"], 1) if residual else None,
            }
        )
    return out


def _outliers(residual_rows: list[dict]) -> list[dict]:
    candidates = [
        r
        for r in residual_rows
        if (r.get("anzahl_stimmberechtigte") or 0) >= OUTLIER_MIN_VOTERS
        and r.get("residual_ja") is not None
        and abs(r["residual_ja"]) >= OUTLIER_RESIDUAL_PP
    ]
    candidates.sort(key=lambda r: abs(r["residual_ja"]), reverse=True)

    return [
        {
            "geo_id": r["geo_id"],
            "name": r.get("name") or str(r["geo_id"]),
            "kanton": r.get("kanton"),
            "yes": _round(r.get("actual_ja"), 1),
            "expected": _round(r.get("predicted_ja"), 1),
            "residual": _round(r["residual_ja"], 1),
            "voters": r.get("anzahl_stimmberechtigte"),
        }
        for r in candidates[:OUTLIER_LIMIT]
    ]


def _kantone(vorlage: Vorlage, shorts: dict[int, str]) -> list[dict]:
    if vorlage.kantonal:
        return []

    df = get_abst_result_kantone(vorlage.vorlagen_id)
    if df is None or df.is_empty():
        return []

    out = []
    for row in df.to_dicts():
        try:
            kanton_id = int(row["kanton"])
        except (TypeError, ValueError):
            continue
        ja = row.get("ja_stimmen") or 0
        nein = row.get("nein_stimmen") or 0
        total = ja + nein
        out.append(
            {
                "kanton": shorts.get(kanton_id, str(kanton_id)),
                "status": row.get("status"),
                "yes": _round(100.0 * ja / total if total else None, 1),
            }
        )
    out.sort(key=lambda r: r["kanton"])
    return out


def build_snapshot(vorlage: Vorlage) -> dict:
    """Current state of one Vorlage. Safe to call on a vote with no results."""

    snapshot = {
        "vorlage_id": vorlage.vorlagen_id,
        "name": short_name(vorlage.name),
        "region": vorlage.region,
        "doppeltes_mehr": vorlage.doppeltes_mehr,
        "finished": vorlage.finished,
        "angenommen": vorlage.angenommen,
        "ja_staende": vorlage.ja_staende,
        "nein_staende": vorlage.nein_staende,
        "gemeinden_final": 0,
        "gemeinden_total": 0,
        "total_voters": 0,
        "counted_share": 0.0,
        "counted_yes": None,
        "projected_yes": None,
        "ci_10": None,
        "ci_90": None,
        "mae": None,
        "projected_bet": None,
        "kantone": [],
        "large": [],
        "outliers": [],
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }

    results = get_abst_results(vorlage.vorlagen_id)
    if results is None or results.is_empty():
        return snapshot

    snapshot.update(_counting(results))

    residual_rows = get_residuals_data(vorlage.vorlagen_id) or []
    residuals = {r["geo_id"]: r for r in residual_rows}

    names = _gemeinde_names(vorlage)
    snapshot["large"] = _large_gemeinden(results, names, residuals)
    snapshot["outliers"] = _outliers(residual_rows)
    snapshot["kantone"] = _kantone(vorlage, _kanton_shorts())

    timeline = get_national_timeline(vorlage.vorlagen_id) or []
    if timeline:
        latest = timeline[-1]
        snapshot.update(
            {
                "counted_yes": _round(latest.get("counted_yes_prozent")),
                "projected_yes": _round(latest.get("projected_yes_prozent")),
                "ci_10": _round(latest.get("ci_10")),
                "ci_90": _round(latest.get("ci_90")),
                "mae": _round(latest.get("mae")),
                "projected_bet": _round(latest.get("projected_stimmbeteiligung"), 1),
            }
        )

    return snapshot


def active_vorlagen(date: datetime.date | None = None):
    """Vorlagen of a given vote day, newest vote day by default."""
    if date is None:
        today = datetime.date.today()
        if Vorlage.objects.filter(tag__date=today).exists():
            date = today
        else:
            latest = Vorlage.objects.order_by("-tag__date").first()
            if latest is None:
                return Vorlage.objects.none()
            date = latest.tag.date

    return Vorlage.objects.filter(tag__date=date).select_related("tag__stand")
