from django.urls import path

from . import views

app_name = "markets"

urlpatterns = [
    path("guard/", views.MarketGuardView.as_view(), name="guard"),
    path("summary/", views.MarketSummaryView.as_view(), name="summary"),
    path("companies/find/", views.FindCompanyView.as_view(), name="company-find"),
    path("companies/open/", views.OpenCompanyView.as_view(), name="company-open"),
    path("analyses/", views.MarketAnalysisListView.as_view(), name="analyses"),
    path("analyses/<uuid:pk>/", views.MarketAnalysisDetailView.as_view(), name="analysis"),
    path("analyses/<uuid:pk>/cancel/", views.MarketAnalysisCancelView.as_view(), name="analysis-cancel"),
    path("analyses/<uuid:pk>/refresh/", views.MarketAnalysisRefreshView.as_view(), name="analysis-refresh"),
]
