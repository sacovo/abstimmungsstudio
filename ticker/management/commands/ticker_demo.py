"""Drive the local demo scenario.

Writes generated snapshots straight into TickerState and runs the detector, so
the API, the CLI and the public page behave exactly as they would on a real vote
day -- without InfluxDB, projection matrices or an actual election.
"""

import datetime

from django.core.management.base import BaseCommand, CommandError

from ticker import demo
from ticker.models import TickerState
from ticker.service import sync_events
from ticker.state import active_vorlagen


class Command(BaseCommand):
    help = "Demo-Szenario steuern (lokal, ohne InfluxDB)"

    def add_arguments(self, parser):
        parser.add_argument("--step", type=int, help=f"0 bis {demo.MAX_STEP}")
        parser.add_argument("--next", action="store_true", help="Einen Schritt weiter")
        parser.add_argument("--reset", action="store_true", help="Zurueck auf Schritt 0")
        parser.add_argument("--date", help="Abstimmungstag YYYY-MM-DD")
        parser.add_argument("--vorlage", type=int, action="append")
        parser.add_argument(
            "--seed",
            action="store_true",
            help="Demo-Abstimmungstag mit Demo-Vorlagen anlegen",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            step = demo.set_step(0)
        elif options["next"]:
            step = demo.set_step(demo.current_step() + 1)
        elif options["step"] is not None:
            step = demo.set_step(options["step"])
        else:
            step = demo.current_step()

        date = None
        if options["date"]:
            try:
                date = datetime.date.fromisoformat(options["date"])
            except ValueError:
                raise CommandError("--date braucht YYYY-MM-DD")

        if options["seed"]:
            seeded = demo.seed_vorlagen(date or datetime.date.today())
            self.stdout.write(f"{len(seeded)} Demo-Vorlagen angelegt")

        vorlagen = active_vorlagen(date)
        if options["vorlage"]:
            vorlagen = vorlagen.filter(vorlagen_id__in=options["vorlage"])

        vorlagen = list(vorlagen)
        if not vorlagen:
            raise CommandError("Keine Vorlagen gefunden -- zuerst Metadaten importieren")

        config = demo.STEPS[step]
        self.stdout.write(
            f"Demo-Schritt {step}/{demo.MAX_STEP}: {config['share']}% ausgezaehlt, "
            f"{config['cities']} grosse Gemeinden definitiv"
        )

        for vorlage in vorlagen:
            snapshot = demo.build_snapshot(vorlage)
            TickerState.objects.update_or_create(
                vorlage=vorlage,
                defaults={"snapshot": snapshot, "source": "demo"},
            )
            created = sync_events(vorlage, snapshot)
            if created:
                self.stdout.write(
                    f"  {vorlage.vorlagen_id} {vorlage.region}: "
                    f"{len(created)} neue Events "
                    + ", ".join(e.key for e in created)
                )

        self.stdout.write("Fertig. Jetzt: ticker status (bzw. manage.py shell)")
