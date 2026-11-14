from django.apps import AppConfig


class AlertsConfig(AppConfig):
    """Per-user notifications: competitor rounds, investor activity, Slush attendees, ..."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.alerts"
    label = "alerts"
    verbose_name = "Alerts"
