"""Persistence around the ticker: event bookkeeping, posting, moderation."""

from django.utils import timezone

from abst.models import Vorlage

from .events import detect
from .models import TickerEvent, TickerNote, TickerPost


def last_published_post(vorlage: Vorlage) -> TickerPost | None:
    return (
        vorlage.ticker_posts.filter(status=TickerPost.Status.PUBLISHED)
        .order_by("-created_at")
        .first()
    )


def last_published_snapshot(vorlage: Vorlage) -> dict | None:
    post = last_published_post(vorlage)
    return post.snapshot if post else None


def sync_events(vorlage: Vorlage, snapshot: dict) -> list[TickerEvent]:
    """Run the detector and persist anything not seen before.

    Returns the events created by this call. Existing keys are left untouched,
    which is what keeps a already-dismissed observation from coming back.
    """

    candidates = detect(snapshot, last_published_snapshot(vorlage))
    known = set(vorlage.ticker_events.values_list("key", flat=True))

    if snapshot.get("finished"):
        # Once the result is in, every earlier observation is moot. Retiring
        # them keeps the agent from writing about a trend that already ended.
        pending_events(vorlage).exclude(kind="final").update(
            status=TickerEvent.Status.DISMISSED,
            dismiss_reason="Vorlage abgeschlossen",
            resolved_at=timezone.now(),
        )

    created = []
    for candidate in candidates:
        if candidate["key"] in known:
            continue
        created.append(
            TickerEvent.objects.create(
                vorlage=vorlage,
                key=candidate["key"],
                kind=candidate["kind"],
                severity=candidate["severity"],
                summary=candidate["summary"],
                payload=candidate["payload"],
            )
        )
    return created


def pending_events(vorlage: Vorlage):
    return vorlage.ticker_events.filter(status=TickerEvent.Status.PENDING)


def create_post(
    vorlage: Vorlage,
    text: str,
    snapshot: dict,
    event: TickerEvent | None = None,
    author: str = "agent",
) -> TickerPost:
    post = TickerPost.objects.create(
        vorlage=vorlage,
        text=text.strip(),
        snapshot=snapshot,
        event=event,
        author=author,
    )

    if event is not None:
        event.status = TickerEvent.Status.POSTED
        event.resolved_at = timezone.now()
        event.save(update_fields=["status", "resolved_at"])

    return post


def edit_post(post: TickerPost, text: str, by: str = "agent") -> TickerPost:
    post.text = text.strip()
    post.edited_at = timezone.now()
    post.edited_by = by
    post.save(update_fields=["text", "edited_at", "edited_by"])
    return post


def retract_post(post: TickerPost, by: str = "agent") -> TickerPost:
    post.status = TickerPost.Status.RETRACTED
    post.edited_at = timezone.now()
    post.edited_by = by
    post.save(update_fields=["status", "edited_at", "edited_by"])
    return post


def restore_post(post: TickerPost, by: str = "agent") -> TickerPost:
    post.status = TickerPost.Status.PUBLISHED
    post.edited_at = timezone.now()
    post.edited_by = by
    post.save(update_fields=["status", "edited_at", "edited_by"])
    return post


def dismiss_event(event: TickerEvent, reason: str = "") -> TickerEvent:
    event.status = TickerEvent.Status.DISMISSED
    event.dismiss_reason = reason
    event.resolved_at = timezone.now()
    event.save(update_fields=["status", "dismiss_reason", "resolved_at"])
    return event


def add_note(text: str, vorlage: Vorlage | None = None, author: str = "agent"):
    return TickerNote.objects.create(text=text.strip(), vorlage=vorlage, author=author)


def minutes_since(dt) -> int | None:
    if dt is None:
        return None
    return int((timezone.now() - dt).total_seconds() // 60)
