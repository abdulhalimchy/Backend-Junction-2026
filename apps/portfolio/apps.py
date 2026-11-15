from django.apps import AppConfig


class PortfolioConfig(AppConfig):
    """An investor's portfolio: suggested from Cala, looked up by name or domain, or added by hand."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.portfolio"
    label = "portfolio"
    verbose_name = "Portfolio"
