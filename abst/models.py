from typing import Any

from django.db import models

# Create your models here.


class GeoStand(models.Model):
    url = models.URLField()
    date = models.DateField()

    document = models.FileField(upload_to="geostands/", blank=True, null=True)

    def __str__(self) -> str:
        return f"GeoStand {self.date}"


class Gemeinde(models.Model):
    name = models.CharField(max_length=255)
    geo_id = models.IntegerField()

    kanton = models.CharField(max_length=255)
    kanton_id = models.IntegerField()

    stand = models.ForeignKey(
        GeoStand, on_delete=models.CASCADE
    )

    def __str__(self):
        return self.name

    class Meta:
        unique_together = ("geo_id", "stand")
        ordering = ["geo_id"]


class Zaehlkreis(models.Model):
    name = models.CharField(max_length=255)
    geo_id = models.IntegerField()
    gemeinde = models.ForeignKey(Gemeinde, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["geo_id"]


class Abstimmungstag(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=1024)

    url_eidg = models.URLField(blank=True, null=True)
    url_kantonal = models.URLField(blank=True, null=True)

    stand = models.ForeignKey(
        GeoStand, on_delete=models.CASCADE
    )

    projection = models.FileField(
        upload_to="projections/", blank=True, null=True
    )
    projection_bet = models.FileField(
        upload_to="projections/", blank=True, null=True
    )

    def __str__(self):
        return f"{self.name} ({self.date})"


class Vorlage(models.Model):
    name = models.CharField(max_length=1024)
    vorlagen_id = models.IntegerField(unique=True)

    finished = models.BooleanField(default=False)
    doppeltes_mehr = models.BooleanField(default=False)

    angenommen = models.BooleanField(default=False)

    ja_staende = models.FloatField(default=0)
    nein_staende = models.FloatField(default=0)

    result = models.JSONField(blank=True, null=True)

    kantonal = models.BooleanField(default=False)

    region = models.CharField(max_length=255, blank=True, null=True)

    related = models.ManyToManyField("self", blank=True)
    tag = models.ForeignKey(
        Abstimmungstag, on_delete=models.CASCADE, related_name="vorlagen")

    def __str__(self):
        return self.name


class Kanton(models.Model):
    name = models.CharField(max_length=255)
    short = models.CharField(max_length=2)
    kanton_id = models.IntegerField(unique=True)
    lang_code = models.CharField(max_length=2, default="de")

    def __str__(self):
        return self.name
