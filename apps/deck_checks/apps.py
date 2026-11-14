from django.apps import AppConfig


class DeckChecksConfig(AppConfig):
    """Pitch-deck uploads and the per-claim verification results against Cala."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.deck_checks"
    label = "deck_checks"
    verbose_name = "Deck checks"
