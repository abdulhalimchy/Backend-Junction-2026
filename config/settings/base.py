"""
Base settings shared by every environment.

Environment-specific modules (development, test, production) import from here
and override only what differs. All secrets and deploy-specific values come
from environment variables (see `.env.example`), never from source code.
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Load `.env` if present. Real environment variables always take precedence.
environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.events",
    "apps.alerts",
    "apps.networking",
    "apps.deck_checks",
    "apps.markets",
    "apps.watchlist",
    "apps.startups",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Driven entirely by DATABASE_URL so switching SQLite -> PostgreSQL is a
# config change, not a code change:
#   sqlite:///db.sqlite3
#   postgres://user:password@host:5432/dbname
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DATABASE_CONN_MAX_AGE", default=0)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Uploads
DECK_CHECK_MAX_UPLOAD_MB = env.int("DECK_CHECK_MAX_UPLOAD_MB", default=20)
# Files above this size are streamed to a temp file instead of held in memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    # URL namespaces ("v1", "v2", ...) select the version: /api/v1/...
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.NamespaceVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="60/minute"),
        "user": env("THROTTLE_USER", default="600/minute"),
        "auth": env("THROTTLE_AUTH", default="10/minute"),
        "alert_scan": env("THROTTLE_ALERT_SCAN", default="20/hour"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# OpenAPI schema + Swagger/Redoc at /api/docs/ and /api/redoc/.
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=DEBUG)

SPECTACULAR_SETTINGS = {
    "TITLE": "Warmline API",
    "DESCRIPTION": "Backend API for Warmline (Junction 2026).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    "ENUM_NAME_OVERRIDES": {
        "DeckCheckStatusEnum": "apps.deck_checks.models.DeckCheck.Status",
        "DeckClaimStatusEnum": "apps.deck_checks.models.DeckClaim.Status",
    },
}

# ---------------------------------------------------------------------------
# CORS (the Next.js frontend)
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Third-party services
# ---------------------------------------------------------------------------
# Cala knowledge graph. Sent as the X-API-KEY header; never expose it to clients.
CALA_API_BASE_URL = env("CALA_API_BASE_URL", default="https://api.cala.ai")
CALA_API_KEY = env("CALA_API_KEY", default="")

# Alert scan (apps/alerts/scan.py): funding rounds dated within this many days count as news, including the
# first time an entity is scanned. Older rounds are history, not alerts.
ALERT_BACKFILL_DAYS = env.int("ALERT_BACKFILL_DAYS", default=60)
# Nightly scan of every watched entity (apps/alerts/nightly.py), at this local time.
ALERT_SCAN_TIME = env("ALERT_SCAN_TIME", default="02:00")
ALERT_SCAN_TIMEZONE = env("ALERT_SCAN_TIMEZONE", default="Europe/Helsinki")
# Run the nightly scheduler inside `runserver`. Production runs `manage.py run_alert_scheduler` (one process)
# or `manage.py scan_alerts --nightly` from cron instead.
ALERT_SCHEDULER_IN_PROCESS = env.bool("ALERT_SCHEDULER_IN_PROCESS", default=False)
# Scan an entity as soon as someone starts watching it, in a background thread (ALERT_SCAN_ASYNC).
ALERT_SCAN_ON_WATCH = env.bool("ALERT_SCAN_ON_WATCH", default=True)
ALERT_SCAN_ASYNC = env.bool("ALERT_SCAN_ASYNC", default=True)

# LLM for deck checks: reads the deck (claim extraction) and compares claims with Cala evidence (verdicts).
# Provider "gemini" (default) or "anthropic". Leave DECK_CHECK_LLM_MODEL empty for the provider's default.
DECK_CHECK_LLM_PROVIDER = env("DECK_CHECK_LLM_PROVIDER", default="gemini")
DECK_CHECK_LLM_MODEL = env("DECK_CHECK_LLM_MODEL", default="")
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
# LLM for market analysis (guard and report summary); always Gemini, using GEMINI_API_KEY.
MARKET_LLM_MODEL = env("MARKET_LLM_MODEL", default="gemini-4-hydrogen")
# Run deck checks in a background thread after the upload returns. Set False to run inline (tests, debugging).
DECK_CHECK_ASYNC = env.bool("DECK_CHECK_ASYNC", default=True)
# Second pass: search Cala's sourced documents for each claim the structured record cannot settle.
DECK_CHECK_WEB_SEARCH = env.bool("DECK_CHECK_WEB_SEARCH", default=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = env("DJANGO_LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
