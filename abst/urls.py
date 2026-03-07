from django.urls import path
from . import views

app_name = "abst"

urlpatterns = [
    path("", views.index_view, name="index"),
    path("<int:vorlage_id>/map/", views.vorlage_map_view, name="vorlage_map"),
    path("<int:vorlage_id>/table/", views.vorlage_table_view, name="vorlage_table"),
    path("proxy-geodata/", views.proxy_geodata_view, name="proxy_geodata"),
]
