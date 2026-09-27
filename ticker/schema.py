from ninja import Schema


class EventOut(Schema):
    key: str
    kind: str
    severity: int
    summary: str
    payload: dict
    status: str
    detected_at: str | None
    age_minutes: int | None


class PostOut(Schema):
    id: int
    vorlage_id: int
    created_at: str
    status: str
    author: str
    text: str
    edited: bool
    event_key: str | None


class NoteOut(Schema):
    id: int
    vorlage_id: int | None
    created_at: str
    resolved: bool
    text: str


class StatusOut(Schema):
    vorlage_id: int
    region: str | None
    name: str
    updated_at: str | None
    stale_minutes: int | None
    snapshot: dict
    pending: list[EventOut]
    post_count: int
    last_post_minutes: int | None


class BriefOut(Schema):
    vorlage_id: int
    region: str | None
    name: str
    updated_at: str | None
    snapshot: dict
    pending: list[EventOut]
    posts: list[PostOut]
    notes: list[NoteOut]


class PostIn(Schema):
    text: str
    event_key: str | None = None
    author: str = "agent"
    force: bool = False


class PostPatchIn(Schema):
    text: str | None = None
    status: str | None = None
    by: str = "agent"


class DismissIn(Schema):
    keys: list[str]
    reason: str = ""


class NoteIn(Schema):
    text: str
    vorlage_id: int | None = None
    author: str = "agent"
