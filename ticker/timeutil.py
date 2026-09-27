"""Display times for the ticker.

A liveticker is read as "what happened at 12:40", so its timestamps must be
Swiss local time no matter what TIME_ZONE the server runs in.
"""

from zoneinfo import ZoneInfo

from django.conf import settings

DEFAULT_TZ = "Europe/Zurich"


def display_tz() -> ZoneInfo:
    return ZoneInfo(getattr(settings, "TICKER_TIME_ZONE", DEFAULT_TZ))


def to_display(dt):
    if dt is None:
        return None
    return dt.astimezone(display_tz())


def iso(dt) -> str | None:
    local = to_display(dt)
    return local.isoformat(timespec="seconds") if local else None


def hhmm(dt) -> str:
    local = to_display(dt)
    return local.strftime("%H:%M") if local else ""
