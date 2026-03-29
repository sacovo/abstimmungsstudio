from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("abst", "0012_kanton_stimmen"),
    ]

    operations = [
        migrations.CreateModel(
            name="Partei",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("partei_id", models.IntegerField(unique=True)),
                ("name", models.CharField(max_length=255)),
                ("kurzname", models.CharField(blank=True, default="", max_length=64)),
                ("parteigruppen_id", models.IntegerField(blank=True, null=True)),
                (
                    "parteigruppen_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "parteipolitische_lager_id",
                    models.IntegerField(blank=True, null=True),
                ),
                (
                    "parteipolitische_lager_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "tag",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="parteien",
                        to="abst.abstimmungstag",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
    ]
