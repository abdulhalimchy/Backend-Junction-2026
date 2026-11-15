from rest_framework.routers import SimpleRouter

from . import views

app_name = "portfolio"

router = SimpleRouter()
router.register("", views.PortfolioCompanyViewSet, basename="portfolio-company")

urlpatterns = router.urls
