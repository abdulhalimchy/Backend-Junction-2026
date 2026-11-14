from rest_framework.routers import SimpleRouter

from . import views

app_name = "deck_checks"

router = SimpleRouter()
router.register("", views.DeckCheckViewSet, basename="deck-check")

urlpatterns = router.urls
