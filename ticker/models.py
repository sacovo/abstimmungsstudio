from django.db import models

from abst.models import Vorlage


class TickerEvent(models.Model):
    """A candidate reason to write a ticker post.

    Events are detected by deterministic code (``ticker.events``), not by a
    model. They start as ``pending``; the agent then either turns one into a
    post or dismisses it with a reason. Persisting them is what stops the same
    observation from being surfaced on every poll.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Offen"
        POSTED = "posted", "Gepostet"
        DISMISSED = "dismissed", "Verworfen"

    vorlage = models.ForeignKey(
        Vorlage, on_delete=models.CASCADE, related_name="ticker_events"
    )

    key = models.CharField(max_length=255)
    kind = models.CharField(max_length=64)
    severity = models.IntegerField(default=1)

    summary = models.CharField(max_length=512)
    payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    dismiss_reason = models.CharField(max_length=512, blank=True, default="")

    detected_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"[{self.kind}] {self.summary}"

    class Meta:
        unique_together = ("vorlage", "key")
        ordering = ["-severity", "-detected_at"]


class TickerPost(models.Model):
    """A published ticker entry. Moderated after the fact via the admin."""

    class Status(models.TextChoices):
        PUBLISHED = "published", "Publiziert"
        RETRACTED = "retracted", "Zurückgezogen"

    vorlage = models.ForeignKey(
        Vorlage, on_delete=models.CASCADE, related_name="ticker_posts"
    )

    text = models.TextField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PUBLISHED
    )

    author = models.CharField(max_length=64, default="agent")
    event = models.ForeignKey(
        TickerEvent,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="posts",
    )

    # State of the vote at the moment of writing. Kept so that deltas for the
    # next post are computable, and so the day can be debugged afterwards.
    snapshot = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(blank=True, null=True)
    edited_by = models.CharField(max_length=64, blank=True, default="")

    def __str__(self):
        return f"{self.created_at:%H:%M} {self.text[:60]}"

    @property
    def projected_yes(self):
        return self.snapshot.get("projected_yes")

    class Meta:
        ordering = ["-created_at"]


class TickerNote(models.Model):
    """Scratchpad for the agent.

    A ticker session runs as a loop of short-lived invocations, so anything it
    wants to remember between polls ("waiting for Winterthur", "Stichfrage
    still unclear") has to live somewhere. Notes are never shown publicly.
    """

    vorlage = models.ForeignKey(
        Vorlage,
        on_delete=models.CASCADE,
        related_name="ticker_notes",
        blank=True,
        null=True,
    )

    text = models.TextField()
    author = models.CharField(max_length=64, default="agent")
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        scope = self.vorlage.vorlagen_id if self.vorlage else "global"
        return f"[{scope}] {self.text[:60]}"

    class Meta:
        ordering = ["-created_at"]


class TickerState(models.Model):
    """Cached snapshot per Vorlage.

    The snapshot is expensive to build (several Influx queries plus a ridge fit
    for the residuals), and the agent polls from another machine. So it is built
    server-side whenever new results land and only read from here — the API
    never touches Influx on the request path.
    """

    vorlage = models.OneToOneField(
        Vorlage, on_delete=models.CASCADE, related_name="ticker_state"
    )
    snapshot = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    source = models.CharField(max_length=16, default="live")

    def __str__(self):
        return f"State {self.vorlage.vorlagen_id} ({self.updated_at:%H:%M:%S})"
