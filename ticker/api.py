"""The agent-facing API.

The ticker agent runs on a different machine, so everything it needs is here:
read the state, read pending events, publish, edit, retract, dismiss, take
notes. Token-authenticated — this router can write to a public page.

Read endpoints serve the cached ``TickerState`` and never hit Influx, so polling
is cheap.
"""

import datetime

from django.conf import settings
from ninja import Router
from ninja.errors import HttpError
from ninja.security import HttpBearer

from abst.models import Vorlage

from . import service
from .events import SEVERITY_MUST
from .models import TickerEvent, TickerNote, TickerPost, TickerState
from .schema import (
    BriefOut,
    DismissIn,
    NoteIn,
    NoteOut,
    PostIn,
    PostOut,
    PostPatchIn,
    StatusOut,
)
from .state import active_vorlagen, short_name
from .timeutil import iso

# Posting twice in quick succession needs a severity-3 event or an explicit
# force. A quiet ticker is the point.
COOLDOWN_MINUTES = 10


class TokenAuth(HttpBearer):
    def authenticate(self, request, token):
        expected = getattr(settings, "TICKER_API_TOKEN", "")
        if expected and token == expected:
            return token
        return None


router = Router(auth=TokenAuth(), tags=["ticker"])


def _get_vorlage(vorlagen_id: int) -> Vorlage:
    vorlage = Vorlage.objects.filter(vorlagen_id=vorlagen_id).first()
    if vorlage is None:
        raise HttpError(404, f"Keine Vorlage {vorlagen_id}")
    return vorlage


def _event_out(event: TickerEvent) -> dict:
    return {
        "key": event.key,
        "kind": event.kind,
        "severity": event.severity,
        "summary": event.summary,
        "payload": event.payload,
        "status": event.status,
        "detected_at": iso(event.detected_at),
        "age_minutes": service.minutes_since(event.detected_at),
    }


def _post_out(post: TickerPost) -> dict:
    return {
        "id": post.pk,
        "vorlage_id": post.vorlage.vorlagen_id,
        "created_at": iso(post.created_at),
        "status": post.status,
        "author": post.author,
        "text": post.text,
        "edited": post.edited_at is not None,
        "event_key": post.event.key if post.event else None,
    }


def _note_out(note: TickerNote) -> dict:
    return {
        "id": note.pk,
        "vorlage_id": note.vorlage.vorlagen_id if note.vorlage else None,
        "created_at": iso(note.created_at),
        "resolved": note.resolved,
        "text": note.text,
    }


def _snapshot_of(vorlage: Vorlage) -> tuple[dict, datetime.datetime | None]:
    state = TickerState.objects.filter(vorlage=vorlage).first()
    if state is None:
        return {}, None
    return state.snapshot, state.updated_at


@router.get("/status", response=list[StatusOut])
def get_status(request, date: str | None = None, open_only: bool = False):
    """Where every Vorlage of the day stands, plus what is unhandled."""
    vote_date = None
    if date:
        try:
            vote_date = datetime.date.fromisoformat(date)
        except ValueError:
            raise HttpError(400, "date braucht YYYY-MM-DD")

    out = []
    for vorlage in active_vorlagen(vote_date).order_by("region", "vorlagen_id"):
        snapshot, updated_at = _snapshot_of(vorlage)
        pending = list(service.pending_events(vorlage))

        if open_only and not pending:
            continue

        last = service.last_published_post(vorlage)
        out.append(
            {
                "vorlage_id": vorlage.vorlagen_id,
                "region": vorlage.region,
                "name": short_name(vorlage.name),
                "updated_at": iso(updated_at),
                "stale_minutes": service.minutes_since(updated_at),
                "snapshot": snapshot,
                "pending": [_event_out(e) for e in pending],
                "post_count": vorlage.ticker_posts.filter(
                    status=TickerPost.Status.PUBLISHED
                ).count(),
                "last_post_minutes": service.minutes_since(
                    last.created_at if last else None
                ),
            }
        )
    return out


@router.get("/vorlage/{vorlagen_id}", response=BriefOut)
def get_brief(request, vorlagen_id: int, posts: int = 5):
    """Everything needed to write one post about one Vorlage."""
    vorlage = _get_vorlage(vorlagen_id)
    snapshot, updated_at = _snapshot_of(vorlage)

    return {
        "vorlage_id": vorlage.vorlagen_id,
        "region": vorlage.region,
        "name": vorlage.name,
        "updated_at": iso(updated_at),
        "snapshot": snapshot,
        "pending": [_event_out(e) for e in service.pending_events(vorlage)],
        "posts": [_post_out(p) for p in vorlage.ticker_posts.all()[:posts]],
        "notes": [
            _note_out(n) for n in vorlage.ticker_notes.filter(resolved=False)
        ],
    }


@router.post("/vorlage/{vorlagen_id}/posts", response=PostOut)
def create_post(request, vorlagen_id: int, payload: PostIn):
    vorlage = _get_vorlage(vorlagen_id)

    text = payload.text.strip()
    if not text:
        raise HttpError(400, "text ist leer")

    event = None
    if payload.event_key:
        event = vorlage.ticker_events.filter(key=payload.event_key).first()
        if event is None:
            raise HttpError(404, f"Kein Event '{payload.event_key}'")

    urgent = event is not None and event.severity >= SEVERITY_MUST
    if not payload.force and not urgent:
        last = service.last_published_post(vorlage)
        waited = service.minutes_since(last.created_at if last else None)
        if waited is not None and waited < COOLDOWN_MINUTES:
            raise HttpError(
                429,
                f"Letzter Post vor {waited} Min (Cooldown {COOLDOWN_MINUTES} Min). "
                "force=true um trotzdem zu posten.",
            )

    snapshot, _ = _snapshot_of(vorlage)
    post = service.create_post(
        vorlage=vorlage,
        text=text,
        snapshot=snapshot,
        event=event,
        author=payload.author,
    )
    return _post_out(post)


@router.get("/posts", response=list[PostOut])
def list_posts(
    request, vorlage_id: int | None = None, limit: int = 20, include_retracted: bool = False
):
    posts = TickerPost.objects.select_related("vorlage")
    if vorlage_id:
        posts = posts.filter(vorlage__vorlagen_id=vorlage_id)
    if not include_retracted:
        posts = posts.filter(status=TickerPost.Status.PUBLISHED)
    return [_post_out(p) for p in posts[:limit]]


@router.patch("/posts/{post_id}", response=PostOut)
def patch_post(request, post_id: int, payload: PostPatchIn):
    """Reactive moderation: rewrite, retract or restore a published entry."""
    post = TickerPost.objects.select_related("vorlage").filter(pk=post_id).first()
    if post is None:
        raise HttpError(404, f"Kein Post {post_id}")

    if payload.text is None and payload.status is None:
        raise HttpError(400, "text oder status angeben")

    if payload.text is not None:
        service.edit_post(post, payload.text, by=payload.by)

    if payload.status is not None:
        if payload.status == TickerPost.Status.RETRACTED:
            service.retract_post(post, by=payload.by)
        elif payload.status == TickerPost.Status.PUBLISHED:
            service.restore_post(post, by=payload.by)
        else:
            raise HttpError(400, "status muss published oder retracted sein")

    return _post_out(post)


@router.post("/vorlage/{vorlagen_id}/dismiss", response=list[str])
def dismiss(request, vorlagen_id: int, payload: DismissIn):
    """Mark events as consciously not worth posting, so they stop resurfacing."""
    vorlage = _get_vorlage(vorlagen_id)

    dismissed = []
    for key in payload.keys:
        event = vorlage.ticker_events.filter(key=key).first()
        if event is None:
            raise HttpError(404, f"Kein Event '{key}'")
        service.dismiss_event(event, payload.reason)
        dismissed.append(key)
    return dismissed


@router.get("/notes", response=list[NoteOut])
def list_notes(request, vorlage_id: int | None = None, include_resolved: bool = False):
    notes = TickerNote.objects.select_related("vorlage")
    if vorlage_id:
        notes = notes.filter(vorlage__vorlagen_id=vorlage_id)
    if not include_resolved:
        notes = notes.filter(resolved=False)
    return [_note_out(n) for n in notes]


@router.post("/notes", response=NoteOut)
def create_note(request, payload: NoteIn):
    vorlage = _get_vorlage(payload.vorlage_id) if payload.vorlage_id else None
    note = service.add_note(payload.text, vorlage=vorlage, author=payload.author)
    return _note_out(note)


@router.patch("/notes/{note_id}", response=NoteOut)
def resolve_note(request, note_id: int):
    note = TickerNote.objects.select_related("vorlage").filter(pk=note_id).first()
    if note is None:
        raise HttpError(404, f"Keine Notiz {note_id}")
    note.resolved = True
    note.save(update_fields=["resolved"])
    return _note_out(note)
