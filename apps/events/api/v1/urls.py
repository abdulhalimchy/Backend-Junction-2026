from rest_framework.routers import SimpleRouter

from . import views

app_name = "events"

router = SimpleRouter()
router.register("", views.EventViewSet, basename="event")

urlpatterns = router.urls
