"""Settings used by the test suite (pytest)."""

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-only-insecure-secret-key-not-for-real-use"  # noqa: S105
ALLOWED_HOSTS = ["testserver", "localhost"]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Fast hashing keeps the suite quick; never use this outside tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Throttling makes auth tests flaky; disable it here.
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = []  # noqa: F405

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

DECK_CHECK_ASYNC = False
CALA_API_KEY = ""
ANTHROPIC_API_KEY = ""
GEMINI_API_KEY = ""
