import datetime
import traceback

from celery import shared_task

from .models import Abstimmungstag
from .predict import create_models, predict_and_store, prepare_predict_data
from .store import (
    fetch_and_store_eidg,
    fetch_and_store_kantonal,
    import_abst_kantonal_meta,
    import_abst_meta,
    update_vorlage,
)


def process_tag(tag):
    new_results_per_vorlage = {}

    try:
        new_results_per_vorlage = fetch_and_store_eidg(tag)
    except Exception as e:
        traceback.print_exc()
        print(f"Error fetching eidg for tag {tag.id}: {e}")

    if tag.url_kantonal:
        try:
            new_results_per_vorlage.update(fetch_and_store_kantonal(tag))
        except Exception as e:
            print(f"Error fetching kantonal for tag {tag.id}: {e}")

    # Trigger prediction for any vorlage that got new results
    for vorlage_id, new_results in new_results_per_vorlage.items():
        if (
            new_results >= 1
        ):  # Only trigger prediction if there are more than 10 new results
            print(
                f"Triggering prediction for vorlage {vorlage_id} with {new_results} new results"
            )
            predict_results_task.delay(vorlage_id)


@shared_task
def update_metadata():
    new = import_abst_meta()
    import_abst_kantonal_meta()

    for tag in new:
        create_models(tag)
        process_tag(tag)


@shared_task
def fetch_active_votes():
    # Find currently active votes: Date is today
    today = datetime.date.today()
    tags = Abstimmungstag.objects.filter(date=today)

    for tag in tags:
        process_tag(tag)


@shared_task
def predict_results_task(vorlagen_id: int):
    # Only run if there are more than 10 final results
    ja_values, bet_values, mask, geo_ids = prepare_predict_data(vorlagen_id)
    known_results = sum(1 for m in mask if not m)

    if known_results > 2:
        predict_and_store(vorlagen_id)
        update_vorlage(vorlagen_id)
    else:
        print(
            f"Not enough known results (only {known_results}) for vorlage {vorlagen_id} to perform prediction."
        )


@shared_task
def cache_historical_votes_task():
    import io
    import polars as pl
    from django.core.cache import cache
    from .models import Vorlage
    from .store import get_vorlagen_table

    today = datetime.date.today()
    tags = Abstimmungstag.objects.filter(date=today)
    if not tags.exists():
        print("Today is not a voting day. No historical votes cached.")
        return

    # Clear all other cached days from the registry
    registry = cache.get("hist_records_registry", [])
    for old_key in registry:
        cache.delete(old_key)
    cache.set("hist_records_registry", [])

    # Cache past votes for today's tags
    new_registry = []
    for tag in tags:
        cache_key = f"hist_records:{tag.date.isoformat()}"
        print(f"Caching historical votes for tag date {tag.date}...")

        historical_vorlagen = Vorlage.objects.filter(
            kantonal=False,
            finished=True,
            tag__date__lt=tag.date
        ).order_by("-tag__date")[:50]
        hist_ids = list(historical_vorlagen.values_list("vorlagen_id", flat=True))
        if hist_ids:
            df_hist = get_vorlagen_table(hist_ids)
            if not df_hist.is_empty():
                bio = io.BytesIO()
                df_hist.write_ipc(bio)
                cache.set(cache_key, bio.getvalue(), timeout=86400 * 7)
                new_registry.append(cache_key)
                print(f"Successfully cached {len(hist_ids)} historical votes under key {cache_key}.")

    cache.set("hist_records_registry", new_registry)


@shared_task
def ensure_projection_matrix_task():
    """Daily task to ensure the newest Abstimmungstag has a projection matrix."""
    tag = Abstimmungstag.objects.order_by("-date").first()
    if not tag:
        print("No Abstimmungstag found in database.")
        return

    has_proj = bool(tag.projection) and bool(tag.projection_bet)
    if not has_proj:
        print(f"Newest Abstimmungstag {tag.id} ({tag.date}) is missing projection matrices. Generating...")
        try:
            create_models(tag)
            print(f"Successfully generated projection matrices for Abstimmungstag {tag.id} ({tag.date}).")
        except Exception as e:
            print(f"Error generating projection matrices for tag {tag.id}: {e}")
    else:
        print(f"Newest Abstimmungstag {tag.id} ({tag.date}) already has projection matrices.")
