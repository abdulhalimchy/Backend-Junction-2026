"""
Root URL configuration.

Every app exposes its API from `apps/<app>/api/v<N>/urls.py`. Each version is
mounted under its own namespace, which DRF's NamespaceVersioning reads to set
`request.version`:

    /api/v1/accounts/...   -> apps.accounts.api.v1.urls
    /api/v1/health/        -> apps.core.api.v1.urls

To ship v2, add `api_v2_patterns`, mount it at `api/v2/` with namespace "v2",
and add "v2" to REST_FRAMEWORK["ALLOWED_VERSIONS"].
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

api_v1_patterns = [
    path("", include("apps.core.api.v1.urls")),
    path("accounts/", include("apps.accounts.api.v1.urls")),
    path("events/", include("apps.events.api.v1.urls")),
    path("alerts/", include("apps.alerts.api.v1.urls")),
    path("networking/", include("apps.networking.api.v1.urls")),
    path("deck-checks/", include("apps.deck_checks.api.v1.urls")),
    path("startups/", include("apps.startups.api.v1.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include((api_v1_patterns, "v1"), namespace="v1")),
]

if settings.API_DOCS_ENABLED:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    ]

if settings.DEBUG:
    # Development only. In production, uploads (e.g. pitch decks) must live in
    # private storage and be served through signed URLs, never publicly.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
