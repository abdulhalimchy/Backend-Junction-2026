from rest_framework.routers import SimpleRouter

from . import views

app_name = "networking"

router = SimpleRouter()
router.register("met", views.MetEntryViewSet, basename="met")

urlpatterns = router.urls
