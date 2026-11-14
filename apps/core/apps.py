from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Shared building blocks (base models, pagination, errors) used by every app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    label = "core"
