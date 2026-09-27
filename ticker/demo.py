"""A scripted stand-in for the Influx-backed snapshot source.

Local development has no production InfluxDB, but the ticker, its CLIs, the
detector and the public page are all just functions of a snapshot dict. So for
testing we generate a plausible counting progression instead of querying.

The progression is deterministic per Vorlage (seeded on vorlagen_id), so
stepping forward and back gives reproducible output.

    ticker demo --step 2      advance the scenario
    ticker status --demo      read it
"""

import datetime
import random
from pathlib import Path

from django.conf import settings

from abst.models import Abstimmungstag, Gemeinde, GeoStand, Kanton, Vorlage

STEP_FILE = Path(settings.BASE_DIR) / ".ticker_demo_step"
MAX_STEP = 5

KANTON_SHORTS = [
    "ZH", "BE", "LU", "UR", "SZ", "OW", "NW", "GL", "ZG", "FR", "SO", "BS",
    "BL", "SH", "AR", "AI", "SG", "GR", "AG", "TG", "TI", "VD", "VS", "NE",
    "GE", "JU",
]

# share of electorate counted, and how many large places have reported
STEPS = {
    0: {"share": 0.0, "cities": 0, "spread": None},
    1: {"share": 12.0, "cities": 0, "spread": 3.2},
    2: {"share": 34.0, "cities": 1, "spread": 1.8},
    3: {"share": 58.0, "cities": 2, "spread": 0.9},
    4: {"share": 86.0, "cities": 4, "spread": 0.4},
    5: {"share": 100.0, "cities": 6, "spread": 0.1},
}


def current_step() -> int:
    try:
        return max(0, min(MAX_STEP, int(STEP_FILE.read_text().strip())))
    except (OSError, ValueError):
        return 0


def set_step(step: int) -> int:
    step = max(0, min(MAX_STEP, step))
    STEP_FILE.write_text(str(step))
    return step


def _rng(vorlage: Vorlage, salt: str = "") -> random.Random:
    return random.Random(f"{vorlage.vorlagen_id}{salt}")


def _target_yes(vorlage: Vorlage) -> float:
    """The "true" result this scenario converges on."""
    return round(_rng(vorlage, "target").uniform(28.0, 68.0), 1)


def _places(vorlage: Vorlage, count: int) -> list[dict]:
    """Largest Gemeinden in scope, from the local geo data if it is there."""
    gemeinden = Gemeinde.objects.filter(stand=vorlage.tag.stand)
    if vorlage.kantonal:
        kanton = Kanton.objects.filter(short=vorlage.region).first()
        if kanton is not None:
            gemeinden = gemeinden.filter(kanton_id=kanton.kanton_id)

    names = list(gemeinden.values_list("geo_id", "name")[:400])
    rng = _rng(vorlage, "places")
    if not names:
        names = [(9000 + i, f"Demo-Gemeinde {i + 1}") for i in range(12)]

    rng.shuffle(names)
    picked = names[:12]

    out = []
    for idx, (geo_id, name) in enumerate(picked):
        out.append(
            {
                "geo_id": geo_id,
                "name": name,
                "voters": int(rng.uniform(0.04, 0.16) * 1_000_000) // (idx + 1),
            }
        )
    out.sort(key=lambda p: -p["voters"])
    return out[:count] if count else out


def build_snapshot(vorlage: Vorlage) -> dict:
    step = current_step()
    config = STEPS[step]
    rng = _rng(vorlage, f"step{step}")

    target = _target_yes(vorlage)
    places = _places(vorlage, 0)
    total_voters = sum(p["voters"] for p in places) * 3

    snapshot = {
        "vorlage_id": vorlage.vorlagen_id,
        "name": vorlage.name[:70],
        "region": vorlage.region,
        "doppeltes_mehr": vorlage.doppeltes_mehr,
        "finished": step >= MAX_STEP,
        "angenommen": step >= MAX_STEP and target > 50.0,
        "ja_staende": 0.0,
        "nein_staende": 0.0,
        "gemeinden_final": int(len(places) * config["share"] / 100.0 * 20),
        "gemeinden_total": len(places) * 20,
        "total_voters": total_voters,
        "counted_share": config["share"],
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
        "demo_step": step,
    }

    if config["spread"] is None:
        return snapshot

    # The projection starts off target and converges as more is counted.
    drift = rng.uniform(-1, 1) * config["spread"] * 1.5
    projected = round(target + drift, 2)
    spread = config["spread"]

    snapshot.update(
        {
            "counted_yes": round(projected + rng.uniform(-1, 1) * spread, 2),
            "projected_yes": projected,
            "ci_10": round(projected - spread, 2),
            "ci_90": round(projected + spread, 2),
            "mae": round(spread / 2, 2),
            "projected_bet": round(rng.uniform(38.0, 52.0), 1),
        }
    )

    reported = places[: config["cities"]]
    snapshot["large"] = [
        {
            **place,
            "status": "final" if place in reported else "predicted",
            "yes": round(projected + rng.uniform(-9, 9), 1),
            "bet": round(rng.uniform(38.0, 55.0), 1),
            "residual": round(rng.uniform(-8, 8), 1) if place in reported else None,
        }
        for place in places
    ]

    if config["cities"] >= 2:
        snapshot["outliers"] = [
            {
                "geo_id": place["geo_id"],
                "name": place["name"],
                "kanton": vorlage.region,
                "yes": round(projected + 9.0, 1),
                "expected": round(projected, 1),
                "residual": 9.0,
                "voters": place["voters"],
            }
            for place in reported[:1]
        ]

    if vorlage.doppeltes_mehr:
        ja_staende = sum(
            1 for _ in range(23) if rng.random() < (projected / 100.0)
        )
        snapshot["ja_staende"] = float(ja_staende)
        snapshot["nein_staende"] = float(23 - ja_staende)
        snapshot["kantone"] = [
            {
                "kanton": short,
                "status": "final" if idx < config["share"] / 100.0 * 26 else "predicted",
                "yes": round(projected + rng.uniform(-14, 14), 1),
            }
            for idx, short in enumerate(KANTON_SHORTS)
        ]

    return snapshot


# A handful of Vorlagen shaped like a real Sunday: two federal with doppeltes
# Mehr, some cantonal, and a Stichfrage pair.
DEMO_VORLAGEN = [
    (990001, "CH", True, "Volksinitiative «Demo-Initiative für bessere Ticker»"),
    (990002, "CH", True, "Änderung des Demo-Gesetzes (Referendum)"),
    (990003, "ZH", False, "Kantonale Volksinitiative «Demo-Wohnraum»"),
    (990004, "BE", False, "Demo-Kredit für den Ausbau der Teststrecke"),
    (990005, "LU", False, "A. Volksinitiative «Demo gegen Demo»"),
    (990006, "LU", False, "B. Gegenentwurf des Kantonsrats"),
    (990007, "LU", False, "C. Stichfrage"),
]


def seed_vorlagen(date: datetime.date) -> list[Vorlage]:
    """Create a demo vote day so local development needs no real election."""
    stand, _ = GeoStand.objects.get_or_create(
        date=date, defaults={"url": "https://example.invalid/demo-geostand"}
    )
    tag, _ = Abstimmungstag.objects.get_or_create(
        date=date,
        defaults={"name": f"Demo-Abstimmungssonntag {date}", "stand": stand},
    )

    vorlagen = []
    for vorlagen_id, region, doppeltes_mehr, name in DEMO_VORLAGEN:
        vorlage, _ = Vorlage.objects.get_or_create(
            vorlagen_id=vorlagen_id,
            defaults={
                "name": name,
                "region": region,
                "kantonal": region != "CH",
                "doppeltes_mehr": doppeltes_mehr,
                "tag": tag,
            },
        )
        vorlagen.append(vorlage)
    return vorlagen
