"""Rebuild cached snapshots by hand (normally Celery does this)."""

from django.core.management.base import BaseCommand

from ticker.state import active_vorlagen
from ticker.tasks import refresh


class Command(BaseCommand):
    help = "Snapshots und Events neu berechnen"

    def add_arguments(self, parser):
        parser.add_argument("--vorlage", type=int, action="append")
        parser.add_argument("--date")

    def handle(self, *args, **options):
        vorlagen = active_vorlagen()
        if options["date"]:
            import datetime

            vorlagen = active_vorlagen(datetime.date.fromisoformat(options["date"]))
        if options["vorlage"]:
            vorlagen = vorlagen.filter(vorlagen_id__in=options["vorlage"])

        for vorlage in vorlagen:
            state = refresh(vorlage)
            yes = state.snapshot.get("projected_yes")
            self.stdout.write(
                f"{vorlage.vorlagen_id} {vorlage.region}: "
                + (f"{yes}% Ja" if yes is not None else "keine Hochrechnung")
            )
