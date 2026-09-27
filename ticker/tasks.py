"""Server-side refresh: build snapshots and detect events when results land.

Hooked into the existing pipeline in ``abst.tasks`` so it runs exactly when
there is something new, instead of on a timer.
"""

from celery import shared_task

from abst.models import Vorlage

from .service import sync_events
from .state import build_snapshot, active_vorlagen
from .models import TickerState


def refresh(vorlage: Vorlage) -> TickerState:
    """Rebuild the cached snapshot for one Vorlage and detect new events."""
    snapshot = build_snapshot(vorlage)

    state, _ = TickerState.objects.update_or_create(
        vorlage=vorlage,
        defaults={"snapshot": snapshot, "source": "live"},
    )
    sync_events(vorlage, snapshot)
    return state


@shared_task
def refresh_ticker_state(vorlagen_id: int):
    vorlage = Vorlage.objects.filter(vorlagen_id=vorlagen_id).first()
    if vorlage is None:
        return
    refresh(vorlage)


@shared_task
def refresh_active_ticker_states():
    """Safety net: catches state changes that arrive without new results,
    most importantly a Vorlage flipping to ``finished``.

    Finished Vorlagen are skipped -- their snapshot will not change again, and
    building one is not cheap.
    """
    for vorlage in active_vorlagen().filter(finished=False):
        try:
            refresh(vorlage)
        except Exception as exc:  # one broken vote must not stop the rest
            print(f"Ticker refresh failed for {vorlage.vorlagen_id}: {exc}")
