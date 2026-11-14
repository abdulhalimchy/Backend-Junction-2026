from django.urls import path

from . import views
from .cala import CalaGatewayView

app_name = "core"

urlpatterns = [
    path("health/", views.HealthCheckView.as_view(), name="health"),
    path("cala/<path:path>", CalaGatewayView.as_view(), name="cala"),
]
