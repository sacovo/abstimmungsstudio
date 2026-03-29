from django.core.management.base import BaseCommand

from abst.models import Abstimmungstag
from abst.store import (
    WAHLEN_META_URL,
    WAHLEN_RESULTATE_URL,
    fetch_and_store_wahlen_results,
    import_wahlen_metadata,
)


class Command(BaseCommand):
    help = "Importiert Wahlen 2023 Parteien-Metadaten und Resultate."

    def add_arguments(self, parser):
        parser.add_argument("--meta-url", default=WAHLEN_META_URL)
        parser.add_argument("--result-url", default=WAHLEN_RESULTATE_URL)

    def handle(self, *args, **options):
        latest_tag = Abstimmungstag.objects.order_by("-date").first()

        imported_meta = import_wahlen_metadata(
            json_url=options["meta_url"],
            tag=latest_tag,
        )
        imported_results = fetch_and_store_wahlen_results(
            json_url=options["result_url"],
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import abgeschlossen: {imported_meta} Parteien, {imported_results} Resultat-Zeilen."
            )
        )
