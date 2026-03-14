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

    if known_results > 5:
        predict_and_store(vorlagen_id)
        update_vorlage(vorlagen_id)
    else:
        print(
            f"Not enough known results (only {known_results}) for vorlage {vorlagen_id} to perform prediction."
        )
