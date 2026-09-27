from django import template

from ticker.timeutil import hhmm, iso

register = template.Library()


@register.filter(name="ticker_time")
def ticker_time(value):
    """Uhrzeit in Schweizer Zeit, unabhängig von TIME_ZONE."""
    return hhmm(value)


@register.filter(name="ticker_iso")
def ticker_iso(value):
    return iso(value) or ""
