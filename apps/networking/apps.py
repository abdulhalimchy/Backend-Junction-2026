from django.apps import AppConfig


class NetworkingConfig(AppConfig):
    """The user's private "met" list: people they met, where and when, with notes."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.networking"
    label = "networking"
    verbose_name = "Networking"
