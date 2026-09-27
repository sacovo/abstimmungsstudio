from django.urls import path

from . import views

urlpatterns = [
    path("ticker/", views.ticker_view, name="ticker"),
    path("ticker/<str:date>/", views.ticker_view, name="ticker_date"),
    path(
        "ticker/vorlage/<int:vorlage_id>/",
        views.vorlage_ticker_view,
        name="ticker_vorlage",
    ),
]
