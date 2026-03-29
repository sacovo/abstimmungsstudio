from django.urls import path

from . import views

app_name = "abst"

urlpatterns = [
    path("vorlagen/", views.index_view, name="vorlagen"),
    path("wahlen/map/", views.wahlen_map_view, name="wahlen_map"),
    path("<int:vorlage_id>/map/", views.vorlage_map_view, name="vorlage_map"),
    path("<int:vorlage_id>/table/", views.vorlage_table_view, name="vorlage_table"),
    path(
        "<int:vorlage_id>/compare/<int:other_id>/",
        views.vorlage_compare_view,
        name="vorlage_compare",
    ),
    path("proxy-geodata/", views.proxy_geodata_view, name="proxy_geodata"),
    path("<str:date>/", views.abstimmungstag_view, name="abstimmungstag"),
    path("", views.abstimmungstag_view, name="index"),
]
