from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from unfold.admin import ModelAdmin
from unfold.decorators import action

from abst.geo import import_from_geojson
from abst.models import GeoStand, Gemeinde, Zaehlkreis, Abstimmungstag, Kanton, Vorlage
from abst.store import fetch_results_eidg, fetch_results_kantonal, store_results, store_vorlagen


@admin.register(GeoStand)
class GeoStandAdmin(ModelAdmin):
    list_display = ("date", "url")
    ordering = ("-date",)
    list_filter = ("date",)

    actions_detail = ["import_geojson", "create_kantone"]

    @action(
        description="Importiere Geodaten von GeoJSON",
    )
    def import_geojson(self, request, object_id):
        stand = self.get_object(request, object_id)
        if stand:
            import_from_geojson(stand)
        return redirect(reverse_lazy("admin:abst_geostand_change", args=[object_id]))

    @action(
        description="Kantone"
    )
    def create_kantone(self, request, object_id):
        stand = self.get_object(request, object_id)
        kantone = stand.gemeinde_set.values_list(
            "kanton", "kanton_id").distinct().order_by("kanton_id").iterator()
        for kanton in kantone:
            Kanton.objects.get_or_create(kanton_id=kanton[1], defaults={
                "name": kanton[0], "short": kanton[0][:2].upper()})

        return redirect(reverse_lazy("admin:abst_geostand_change", args=[object_id]))


@admin.register(Gemeinde)
class GemeindeAdmin(ModelAdmin):
    list_display = ("name", "geo_id", "kanton", "stand")
    ordering = ("geo_id",)
    list_filter = ("stand", "kanton")

    search_fields = ("name", "geo_id", "kanton")


@admin.register(Zaehlkreis)
class ZaehlkreisAdmin(ModelAdmin):
    list_display = ("name", "geo_id", "gemeinde")
    ordering = ("geo_id",)
    readonly_fields = ("name", "geo_id", "gemeinde")


@admin.register(Abstimmungstag)
class AbstimmungstagAdmin(ModelAdmin):
    list_display = ("date", "name", "stand")
    ordering = ("-date",)
    actions_detail = ["fetch_eidg", "fetch_kantonal"]

    @action(
        description="Eidgenössisch",
    )
    def fetch_eidg(self, request, object_id):
        obj = self.get_object(request, object_id)
        if not obj:
            return redirect(reverse_lazy("admin:abst_abstimmungstag_changelist"))
        gemeinden, vorlagen = fetch_results_eidg(obj.url_eidg)
        store_vorlagen(vorlagen, obj)
        store_results(gemeinden)

        return redirect(reverse_lazy("admin:abst_abstimmungstag_change", args=[object_id]))

    @action(
        description="Kantonal",
    )
    def fetch_kantonal(self, request, object_id):
        obj = self.get_object(request, object_id)
        if not obj or not obj.url_kantonal:
            return redirect(reverse_lazy("admin:abst_abstimmungstag_changelist"))
        gemeinden, vorlagen = fetch_results_kantonal(obj.url_kantonal)
        store_vorlagen(vorlagen, obj)
        store_results(gemeinden)

        return redirect(reverse_lazy("admin:abst_abstimmungstag_change", args=[object_id]))


@admin.register(Vorlage)
class VorlageAdmin(ModelAdmin):
    list_display = ("name", "vorlagen_id")
    ordering = ("vorlagen_id",)
    list_filter = ("tag", "region")


@admin.register(Kanton)
class KantonAdmin(ModelAdmin):
    list_display = ("name", "short", "lang_code")

    list_editable = ("short", "lang_code")
