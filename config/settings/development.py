"""Local development settings."""

from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "[::1]"])

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=["http://localhost:3000"])

# Browsable API is handy while developing; production stays JSON-only.
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]
REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [  # noqa: F405
    "rest_framework_simplejwt.authentication.JWTAuthentication",
    "rest_framework.authentication.SessionAuthentication",
]
REST_FRAMEWORK["DEFAULT_PARSER_CLASSES"] = [  # noqa: F405
    "rest_framework.parsers.JSONParser",
    "rest_framework.parsers.FormParser",
    "rest_framework.parsers.MultiPartParser",
]

ALERT_SCHEDULER_IN_PROCESS = env.bool("ALERT_SCHEDULER_IN_PROCESS", default=True)

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
