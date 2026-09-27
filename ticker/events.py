"""Deterministic detection of "is this worth a ticker post?".

Deliberately not a model's job. Running an LLM as the filter across ~25
Vorlagen every minute is slow, expensive and inconsistent; plain thresholds are
none of those things. The model only gets involved once something here fires.

Pure functions over snapshot dicts (see ``ticker.state``) so this is testable
without a database, Influx or a vote day.
"""

# Enough of the electorate counted before we say anything at all.
MIN_COUNTED_SHARE = 3.0

# A result counts as decided when the 10-90 band sits entirely on one side.
DECIDED_MIN_SHARE = 8.0

# Projection move (percentage points) since the last post that justifies one.
SHIFT_PP = 1.5

# A Gemeinde reporting is newsworthy from this share of the scope electorate.
CITY_MIN_SHARE = 3.0

SEVERITY_MUST = 3
SEVERITY_SHOULD = 2
SEVERITY_MAYBE = 1


def _event(key, kind, severity, summary, **payload):
    return {
        "key": key,
        "kind": kind,
        "severity": severity,
        "summary": summary,
        "payload": payload,
    }


def _side(yes):
    return "ja" if yes >= 50.0 else "nein"


def _decided(snapshot) -> bool:
    lo, hi = snapshot.get("ci_10"), snapshot.get("ci_90")
    if lo is None or hi is None:
        return False
    return (lo > 50.0 and hi > 50.0) or (lo < 50.0 and hi < 50.0)


def _staende_needed(snapshot) -> bool:
    """Federal votes with doppeltes Mehr can pass the people but fail the cantons."""
    if not snapshot.get("doppeltes_mehr"):
        return False
    ja, nein = snapshot.get("ja_staende") or 0, snapshot.get("nein_staende") or 0
    if ja + nein < 10:  # too early to read anything into it
        return False
    yes = snapshot.get("projected_yes")
    if yes is None:
        return False
    volksmehr_ja = yes > 50.0
    staendemehr_ja = ja > nein
    return volksmehr_ja != staendemehr_ja


def detect(snapshot: dict, previous: dict | None) -> list[dict]:
    """Candidate events, most important first.

    ``previous`` is the snapshot stored on the last published post for this
    Vorlage, or None if nothing has been posted yet. Keys are stable: the caller
    persists them and never surfaces the same key twice.
    """

    events: list[dict] = []
    yes = snapshot.get("projected_yes")
    share = snapshot.get("counted_share") or 0.0
    counted = snapshot.get("gemeinden_final") or 0

    if snapshot.get("finished"):
        events.append(
            _event(
                "final",
                "final",
                SEVERITY_MUST,
                f"Endresultat: {'Ja' if snapshot.get('angenommen') else 'Nein'}"
                + (f" bei {yes:.1f}% Ja" if yes is not None else ""),
                yes=yes,
                angenommen=snapshot.get("angenommen"),
            )
        )
        # Nothing else matters once it is over.
        return events

    if yes is None or share < MIN_COUNTED_SHARE:
        return events

    previous_yes = previous.get("projected_yes") if previous else None

    # A post written before counting starts (a curtain-raiser, say) carries a
    # snapshot without a projection. That must still count as "nothing said
    # about the result yet", or the first real projection goes unreported.
    if previous_yes is None:
        events.append(
            _event(
                "first_signal",
                "first_signal",
                SEVERITY_SHOULD,
                f"Erste Hochrechnung: {yes:.1f}% Ja bei {share:.0f}% ausgezählt",
                yes=yes,
                share=share,
            )
        )
    elif _side(previous_yes) != _side(yes):
        events.append(
            _event(
                f"flip:{_side(yes)}:{counted}",
                "flip",
                SEVERITY_MUST,
                f"Trendwende: Hochrechnung kippt auf {_side(yes).upper()} "
                f"({previous_yes:.1f}% → {yes:.1f}%)",
                yes=yes,
                previous_yes=previous_yes,
            )
        )
    elif abs(yes - previous_yes) >= SHIFT_PP:
        events.append(
            _event(
                f"shift:{counted}",
                "shift",
                SEVERITY_MAYBE,
                f"Hochrechnung verschiebt sich um "
                f"{yes - previous_yes:+.1f} Pp. auf {yes:.1f}% Ja",
                yes=yes,
                previous_yes=previous_yes,
            )
        )

    if share >= DECIDED_MIN_SHARE and _decided(snapshot):
        events.append(
            _event(
                "decided",
                "decided",
                SEVERITY_MUST,
                f"Entscheidung steht: {_side(yes).upper()} "
                f"({snapshot.get('ci_10')}–{snapshot.get('ci_90')}% Band)",
                yes=yes,
                ci_10=snapshot.get("ci_10"),
                ci_90=snapshot.get("ci_90"),
            )
        )

    if _staende_needed(snapshot):
        events.append(
            _event(
                "staende_split",
                "staende_split",
                SEVERITY_SHOULD,
                f"Volksmehr und Ständemehr laufen auseinander "
                f"({snapshot.get('ja_staende')} zu {snapshot.get('nein_staende')} Stände)",
                ja_staende=snapshot.get("ja_staende"),
                nein_staende=snapshot.get("nein_staende"),
            )
        )

    events.extend(_city_events(snapshot))
    events.extend(_outlier_events(snapshot))

    events.sort(key=lambda e: -e["severity"])
    return events


def _city_events(snapshot) -> list[dict]:
    total = snapshot.get("total_voters") or 0
    if not total:
        return []

    out = []
    for place in snapshot.get("large", []):
        if place.get("status") != "final":
            continue
        share = 100.0 * (place.get("voters") or 0) / total
        if share < CITY_MIN_SHARE:
            continue

        residual = place.get("residual")
        summary = f"{place['name']} ausgezählt: {place.get('yes')}% Ja"
        if residual is not None and abs(residual) >= 3.0:
            summary += f" ({residual:+.1f} Pp. gegenüber Erwartung)"

        out.append(
            _event(
                f"city:{place['geo_id']}",
                "city_in",
                SEVERITY_SHOULD,
                summary,
                name=place["name"],
                yes=place.get("yes"),
                residual=residual,
                share=round(share, 1),
            )
        )
    return out


def _outlier_events(snapshot) -> list[dict]:
    out = []
    for row in snapshot.get("outliers", []):
        direction = "über" if row["residual"] > 0 else "unter"
        out.append(
            _event(
                f"outlier:{row['geo_id']}",
                "outlier",
                SEVERITY_MAYBE,
                f"{row['name']} ({row.get('kanton')}) liegt {abs(row['residual']):.1f} Pp. "
                f"{direction} der Erwartung: {row.get('yes')}% statt {row.get('expected')}%",
                **row,
            )
        )
    return out
