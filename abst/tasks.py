from celery import shared_task
from django.utils import timezone
import datetime
from .models import Abstimmungstag, Vorlage
from .store import fetch_results_eidg, store_results, store_vorlagen, fetch_results_kantonal
from .predict import prepare_predict_data, predict_and_store


@shared_task
def fetch_active_votes():
    # Find currently active votes: Date is today
    today = datetime.date.today()
    tags = Abstimmungstag.objects.filter(date=today)

    for tag in tags:
        try:
            results, vorlagen = fetch_results_eidg(tag.url_eidg)
            store_results(results)
            store_vorlagen(vorlagen, tag)
        except Exception as e:
            print(f"Error fetching eidg for tag {tag.id}: {e}")

        if tag.url_kantonal:
            try:
                results, vorlagen_kantonal = fetch_results_kantonal(
                    tag.url_kantonal)
                store_vorlagen(vorlagen_kantonal, tag)
                store_results(results)
            except Exception as e:
                print(f"Error fetching kantonal for tag {tag.id}: {e}")

        # Trigger prediction task for each unfinished vorlage
        unfinished_vorlagen = Vorlage.objects.filter(tag=tag, finished=False)
        for v in unfinished_vorlagen:
            predict_results_task.delay(v.vorlagen_id)


@shared_task
def predict_results_task(vorlagen_id: int):
    # Only run if there are more than 10 final results
    ja_values, bet_values, mask, geo_ids = prepare_predict_data(vorlagen_id)
    known_results = sum(1 for m in mask if not m)

    if known_results > 10:
        predict_and_store(vorlagen_id)
    else:
        print(
            f"Not enough known results (only {known_results}) for vorlage {vorlagen_id} to perform prediction.")
