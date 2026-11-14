from rest_framework.routers import SimpleRouter

from . import views

app_name = "alerts"

router = SimpleRouter()
router.register("", views.AlertViewSet, basename="alert")

urlpatterns = router.urls
