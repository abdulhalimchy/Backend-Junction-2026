from rest_framework.routers import SimpleRouter

from . import views

app_name = "watchlist"

router = SimpleRouter()
router.register("", views.WatchItemViewSet, basename="watch")

urlpatterns = router.urls
