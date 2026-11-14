from django.apps import AppConfig


class EventsConfig(AppConfig):
    """Event spaces (e.g. Slush 2026) and the companies/investors attending them."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.events"
    label = "events"
    verbose_name = "Events"
