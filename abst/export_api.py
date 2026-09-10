"""Snapshot export for presentations (do-slidevs).

A deck built by do-slidevs ends up as a static page on slides.d-o.li. It can
neither send a session cookie nor talk to this host across origins, so it
pulls everything it needs once at build time through this router and ships
the JSON next to the slides.

Authentication is a token from ``settings.EXPORT_TOKENS`` instead of the
session, because the caller is a build script and not a browser. With no
token configured the export stays switched off.

The shapes below are a contract with the Vue components on the other side
(``theme/components/VoteMap.vue`` and friends). Field names are the ones the
rest of this project already uses, so a value can be traced back to its
query. Add fields rather than renaming them.
"""

import json
from typing import Literal

import polars as pl
from django.conf import settings
from ninja import Router
from ninja.errors import HttpError
from ninja.security import APIKeyHeader

from abst.models import Gemeinde, GeoStand, Kanton, Vorlage
from abst.store import (
    get_abst_result_kantone,
    get_abst_result_total,
    get_abst_results,
    get_scatterplot_data,
)

SCHEMA = "abst-deck/1"

# Object names in the federal TopoJSON carry their own cut-off date
# ("K4voge_20250101_gf"). The deck should not have to guess at prefixes, so
# every layer is renamed to a fixed key and stripped down to the properties
# that are actually drawn or joined on.
#
# Matched in lower case: the 2025 dataset writes "K4voge_…", the 2026 one
# "k4voge_…". Matching letter for letter silently dropped the municipalities
# and cantons — the two layers the map is made of.
GEO_LAYERS = {
    "k4suis": ("land", ()),
    "k4kant": ("kantone", ("kantId", "kantName")),
    "k4voge": ("gemeinden", ("vogeId", "vogeName", "kantId")),
    "zaehlkreise": ("zaehlkreise", ("id", "name", "vogeId", "kantId")),
    "k4seen": ("seen", ("name",)),
}


class ExportToken(APIKeyHeader):
    param_name = "X-Abst-Token"

    def authenticate(self, request, key):
        tokens = getattr(settings, "EXPORT_TOKENS", [])
        if key and key in tokens:
            return key
        return None


router = Router(auth=ExportToken())


@router.get("vorlagen")
def export_vorlagen(
    request,
    date: str | None = None,
    region: str | None = None,
    name: str | None = None,
    limit: int = 50,
):
    """Short listing so a build script can turn a name into a vorlagen_id."""
    qs = Vorlage.objects.select_related("tag").all()
    if date:
        qs = qs.filter(tag__date=date)
    if region:
        qs = qs.filter(region=region)
    if name:
        qs = qs.filter(name__icontains=name)

    return [
        {
            "vorlagen_id": v.vorlagen_id,
            "name": v.name,
            "datum": v.tag.date.isoformat(),
            "region": v.region or "CH",
            "beendet": v.finished,
            "angenommen": v.angenommen,
            "geo_stand": v.tag.stand.date.isoformat(),
        }
        for v in qs.order_by("-tag__date", "vorlagen_id")[: max(1, min(limit, 500))]
    ]


@router.get("metriken")
def export_metriken(request, search: str | None = None):
    """The metrics available for the scatter axes, id and label.

    There are close to two hundred of them since the commune statistics
    arrived; nobody guesses those ids, so the build script can list them.
    """
    metriken = _metrik_liste()
    if search:
        needle = search.lower()
        metriken = [
            m for m in metriken if needle in m["name"].lower() or needle in m["id"].lower()
        ]
    return metriken


@router.get("geo/{stand_date}")
def export_geo(request, stand_date: str):
    """The TopoJSON of one GeoStand, with layers renamed and slimmed down.

    Several Vorlagen share a GeoStand, so the deck keeps this file once per
    date instead of once per Vorlage.
    """
    stand = GeoStand.objects.filter(date=stand_date).first()
    if stand is None:
        raise HttpError(404, f"Kein GeoStand zum Datum {stand_date}.")
    if not stand.document:
        raise HttpError(404, f"GeoStand {stand_date} hat keine Datei.")

    with stand.document.open("rb") as datei:
        return slim_topology(json.loads(datei.read()))


def slim_topology(topo: dict) -> dict:
    """Rename the layers to fixed keys and drop what is never drawn.

    Districts go entirely, and every remaining feature keeps only the
    properties the deck joins or labels on. The arcs are untouched — they
    carry the geometry and are shared between layers.
    """
    objects: dict[str, dict] = {}
    for key, layer in topo.get("objects", {}).items():
        prefix = key.split("_")[0].lower()
        if prefix not in GEO_LAYERS:
            continue
        target, fields = GEO_LAYERS[prefix]
        geometries = []
        for geometry in layer.get("geometries", []):
            props = geometry.get("properties") or {}
            slim = {k: v for k, v in geometry.items() if k != "properties"}
            if fields:
                slim["properties"] = {f: props.get(f) for f in fields}
            geometries.append(slim)
        # Zählkreise come as one layer per city; merge them into one.
        if target in objects:
            objects[target]["geometries"].extend(geometries)
        else:
            objects[target] = {"type": layer["type"], "geometries": geometries}

    return {
        "type": "Topology",
        # Coordinates are Swiss national grid, not lon/lat — the deck draws
        # them with a planar projection (d3.geoIdentity), not Mercator.
        "transform": topo["transform"],
        "arcs": topo["arcs"],
        "objects": objects,
    }


@router.get("{vorlage_id}")
def export_bundle(
    request,
    vorlage_id: int,
    scatter: bool = False,
    x_metric: str = "ja_prozent",
    y_metric: str = "stimmbeteiligung",
    size_metric: str = "anzahl_stimmberechtigte",
    wahlen_scope: Literal["partei", "parteigruppe", "lager"] = "partei",
    wahlen_option_id: int | None = None,
    wahlen_mode: Literal["current", "last", "diff"] = "current",
    abstimmung_vorlage_id: int | None = None,
    abstimmung_result_mode: Literal["ja_prozent", "stimmbeteiligung"] = "ja_prozent",
    behavior: bool = False,
    behavior_source: str | None = None,
    behavior_scope: Literal["partei", "parteigruppe", "lager"] = "partei",
    behavior_region: str | None = None,
):
    """Everything one slide deck needs about a single Vorlage, in one file."""
    try:
        vorlage = Vorlage.objects.select_related("tag__stand").get(
            vorlagen_id=vorlage_id
        )
    except Vorlage.DoesNotExist:
        raise HttpError(404, f"Vorlage {vorlage_id} nicht gefunden.")

    bundle = {
        "schema": SCHEMA,
        "vorlage": {
            "vorlagen_id": vorlage.vorlagen_id,
            "name": vorlage.name,
            "datum": vorlage.tag.date.isoformat(),
            "region": vorlage.region or "CH",
            "beendet": vorlage.finished,
            "angenommen": vorlage.angenommen,
            "doppeltes_mehr": vorlage.doppeltes_mehr,
            "ja_staende": vorlage.ja_staende,
            "nein_staende": vorlage.nein_staende,
        },
        "geo": {"stand": vorlage.tag.stand.date.isoformat()},
        "total": _total(vorlage_id),
        "kantone": _kantone(vorlage_id),
        "gemeinden": _gemeinden(vorlage),
        "streuung": None,
        "wanderung": None,
    }

    if scatter:
        bundle["streuung"] = _streuung(
            vorlage_id,
            x_metric=x_metric,
            y_metric=y_metric,
            size_metric=size_metric,
            wahlen_scope=wahlen_scope,
            wahlen_option_id=wahlen_option_id,
            wahlen_mode=wahlen_mode,
            abstimmung_vorlage_id=abstimmung_vorlage_id,
            abstimmung_result_mode=abstimmung_result_mode,
        )

    if behavior:
        bundle["wanderung"] = _wanderung(
            vorlage, behavior_source, behavior_scope, behavior_region
        )

    return bundle


def _total(vorlage_id: int) -> dict | None:
    df = get_abst_result_total(vorlage_id)
    if df is None or df.is_empty():
        return None

    # Prediction and final rows arrive separately; the deck shows one number,
    # so they are summed and the row marked as a prediction if any part is.
    rows = df.to_dicts()
    ja = sum(int(r["ja_stimmen"]) for r in rows)
    nein = sum(int(r["nein_stimmen"]) for r in rows)
    berechtigte = sum(int(r["anzahl_stimmberechtigte"]) for r in rows)
    abgegeben = ja + nein

    return {
        "status": "final"
        if all(r["status"] == "final" for r in rows)
        else "prediction",
        "ja_stimmen": ja,
        "nein_stimmen": nein,
        "anzahl_stimmberechtigte": berechtigte,
        "ja_prozent": round(ja / abgegeben * 100, 2) if abgegeben else 0.0,
        "stimmbeteiligung": round(abgegeben / berechtigte * 100, 2)
        if berechtigte
        else 0.0,
    }


def _kantone(vorlage_id: int) -> list[dict]:
    df = get_abst_result_kantone(vorlage_id)
    if df is None or df.is_empty():
        return []

    meta = {k.kanton_id: k for k in Kanton.objects.all()}
    zusammen: dict[int, dict] = {}

    for row in df.to_dicts():
        kanton_id = int(row["kanton"])
        eintrag = zusammen.setdefault(
            kanton_id,
            {
                "kanton_id": kanton_id,
                "kuerzel": meta[kanton_id].short if kanton_id in meta else str(kanton_id),
                "name": meta[kanton_id].name if kanton_id in meta else str(kanton_id),
                "standesstimmen": meta[kanton_id].stimmen if kanton_id in meta else 0,
                "status": "final",
                "ja_stimmen": 0,
                "nein_stimmen": 0,
                "anzahl_stimmberechtigte": 0,
            },
        )
        eintrag["ja_stimmen"] += int(row["ja_stimmen"])
        eintrag["nein_stimmen"] += int(row["nein_stimmen"])
        eintrag["anzahl_stimmberechtigte"] += int(row["anzahl_stimmberechtigte"])
        if row["status"] != "final":
            eintrag["status"] = "prediction"

    for eintrag in zusammen.values():
        _add_quoten(eintrag)

    return sorted(zusammen.values(), key=lambda e: e["kanton_id"])


def _gemeinden(vorlage: Vorlage) -> list[dict]:
    df = get_abst_results(vorlage.vorlagen_id)
    if df is None or df.is_empty():
        return []

    namen = {
        g["geo_id"]: g
        for g in Gemeinde.objects.filter(stand=vorlage.tag.stand).values(
            "geo_id", "name", "kanton", "kanton_id"
        )
    }

    gemeinden = []
    for row in df.to_dicts():
        geo_id = int(row["geo_id"])
        meta = namen.get(geo_id, {})
        gemeinden.append(
            {
                "geo_id": geo_id,
                "name": meta.get("name", str(geo_id)),
                "kanton": meta.get("kanton", ""),
                "kanton_id": meta.get("kanton_id", 0),
                "status": row["status"],
                "ja_stimmen": int(row["ja_stimmen"]),
                "nein_stimmen": int(row["nein_stimmen"]),
                "anzahl_stimmberechtigte": int(row["anzahl_stimmberechtigte"]),
                "ja_prozent": round(float(row["ja_prozent"]), 2),
                "stimmbeteiligung": round(float(row["stimmbeteiligung"]), 2),
            }
        )

    return gemeinden


def _add_quoten(eintrag: dict) -> None:
    abgegeben = eintrag["ja_stimmen"] + eintrag["nein_stimmen"]
    berechtigte = eintrag["anzahl_stimmberechtigte"]
    eintrag["ja_prozent"] = (
        round(eintrag["ja_stimmen"] / abgegeben * 100, 2) if abgegeben else 0.0
    )
    eintrag["stimmbeteiligung"] = (
        round(abgegeben / berechtigte * 100, 2) if berechtigte else 0.0
    )


def _metric_format(metric_id: str) -> str:
    """Which number format the deck should label an axis with.

    Guessing this on the deck side stopped working once the commune
    statistics arrived: most of them are counts, and "8'432 %" is not a
    rounding error, it is a wrong statement. The name of the format is one
    of the deck's own (see theme/composables/useChartTheme.ts).
    """
    if metric_id in ("ja_prozent", "stimmbeteiligung", "wahlen_result", "abstimmung_result"):
        return "percent"
    if "_pct" in metric_id:
        return "percent"
    if "rate_per_1000" in metric_id:
        return "dec"
    return "int"


def _metrik_liste() -> list[dict[str, str]]:
    from abst.api import _scatter_metrics

    return [
        {"id": m["id"], "name": m["name"], "format": _metric_format(m["id"])}
        for m in _scatter_metrics()
    ]


def _streuung(vorlage_id: int, **kwargs) -> dict:
    namen = {m["id"]: m["name"] for m in _metrik_liste()}

    try:
        df = get_scatterplot_data(vorlage_id=vorlage_id, **kwargs)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from exc

    if df.is_empty():
        punkte = []
    else:
        df = df.filter(
            pl.col("x_value").is_not_null()
            & pl.col("y_value").is_not_null()
            & pl.col("size_value").is_not_null()
        )
        # Nur die Zahlen: Name, Kanton und Status stehen schon bei den
        # Gemeinden, und das Deck verbindet beides über die geo_id.
        punkte = [
            {
                "geo_id": int(r["geo_id"]),
                "x": round(float(r["x_value"]), 3),
                "y": round(float(r["y_value"]), 3),
                "groesse": round(float(r["size_value"]), 3),
            }
            for r in df.to_dicts()
        ]

    def achse(metric_id: str) -> dict:
        return {
            "id": metric_id,
            "name": namen.get(metric_id, metric_id),
            "format": _metric_format(metric_id),
        }

    return {
        "x": achse(kwargs["x_metric"]),
        "y": achse(kwargs["y_metric"]),
        "groesse": achse(kwargs["size_metric"]),
        "punkte": punkte,
    }


def _wanderung(
    vorlage: Vorlage,
    source: str | None,
    scope: str,
    region: str | None,
) -> dict:
    """Voter transitions as a matrix, ready to be drawn as a flow diagram.

    ``source`` is one of the ids from ``/behavior/options``, so either
    "election_nrw2023" or "vote_<id>". Without one the most recent finished
    Vorlage before this one is used.
    """
    from abst.behavior import calculate_behavior, get_behavior_options

    optionen = get_behavior_options(vorlage.vorlagen_id)
    if not optionen:
        raise HttpError(400, "Keine Vergleichsvorlage für die Wanderung vorhanden.")

    gewaehlt = next((o for o in optionen if o["id"] == source), None) if source else None
    if source and gewaehlt is None:
        raise HttpError(400, f"Unbekannte Quelle «{source}» für die Wanderung.")
    if gewaehlt is None:
        gewaehlt = optionen[0]

    try:
        result = calculate_behavior(
            vorlage.vorlagen_id,
            gewaehlt["type"],
            gewaehlt.get("vote_id"),
            scope,
            region=region,
        )
    except Exception as exc:
        raise HttpError(400, f"Wanderung nicht berechenbar: {exc}") from exc

    return {
        "quelle": {"id": gewaehlt["id"], "name": gewaehlt["name"]},
        "ziel": vorlage.name,
        "region": region,
        "von": result["source_labels"],
        "nach": result["target_labels"],
        "matrix": [[round(float(v), 1) for v in zeile] for zeile in result["matrix"]],
        "total": result["total_votes"],
    }
