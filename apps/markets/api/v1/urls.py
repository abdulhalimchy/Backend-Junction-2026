from django.urls import path

from . import views

app_name = "markets"

urlpatterns = [
    path("guard/", views.MarketGuardView.as_view(), name="guard"),
    path("summary/", views.MarketSummaryView.as_view(), name="summary"),
]
