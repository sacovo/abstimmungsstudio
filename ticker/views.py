import datetime

from django.http import Http404
from django.shortcuts import render

from abst.models import Vorlage
from ticker.models import TickerPost
from ticker.state import active_vorlagen


def _vote_date(date_str: str | None) -> datetime.date | None:
    if not date_str:
        return None
    try:
        return datetime.date.fromisoformat(date_str)
    except ValueError:
        raise Http404("Ungültiges Datum")


def _published(vorlage_ids):
    return (
        TickerPost.objects.filter(
            status=TickerPost.Status.PUBLISHED, vorlage__vorlagen_id__in=vorlage_ids
        )
        .select_related("vorlage")
        .order_by("-created_at")
    )


def ticker_view(request, date=None):
    """All Vorlagen of a vote day, each with its own ticker."""
    vorlagen = list(
        active_vorlagen(_vote_date(date)).order_by("region", "vorlagen_id")
    )
    if not vorlagen:
        raise Http404("Keine Abstimmung gefunden")

    posts = _published([v.vorlagen_id for v in vorlagen])
    by_vorlage: dict[int, list] = {}
    for post in posts:
        by_vorlage.setdefault(post.vorlage_id, []).append(post)

    entries = [
        {
            "vorlage": vorlage,
            "posts": by_vorlage.get(vorlage.pk, []),
            "latest": (by_vorlage.get(vorlage.pk) or [None])[0],
        }
        for vorlage in vorlagen
    ]
    # Votes that have something to say come first.
    entries.sort(key=lambda e: (not e["posts"], e["vorlage"].region or ""))

    return render(
        request,
        "ticker/ticker.html",
        {
            "tag": vorlagen[0].tag,
            "entries": entries,
            "post_count": len(posts),
        },
    )


def vorlage_ticker_view(request, vorlage_id):
    """One vote, full history."""
    try:
        vorlage = Vorlage.objects.select_related("tag").get(vorlagen_id=vorlage_id)
    except Vorlage.DoesNotExist:
        raise Http404("Vorlage nicht gefunden")

    return render(
        request,
        "ticker/vorlage.html",
        {
            "vorlage": vorlage,
            "tag": vorlage.tag,
            "posts": vorlage.ticker_posts.filter(
                status=TickerPost.Status.PUBLISHED
            ),
        },
    )
