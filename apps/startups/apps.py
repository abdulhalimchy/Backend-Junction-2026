from django.apps import AppConfig


class StartupsConfig(AppConfig):
    """The founder's own startups: added by domain (auto-filled from Cala and the website) or by hand."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.startups"
    label = "startups"
    verbose_name = "Startups"
