from rest_framework.routers import SimpleRouter

from . import views

app_name = "startups"

router = SimpleRouter()
router.register("", views.StartupViewSet, basename="startup")

urlpatterns = router.urls
