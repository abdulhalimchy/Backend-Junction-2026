from django.apps import AppConfig


class MarketsConfig(AppConfig):
    """LLM steps of a market analysis: the market-text guard and the fact-checked report summary."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.markets"
    label = "markets"
    verbose_name = "Markets"
