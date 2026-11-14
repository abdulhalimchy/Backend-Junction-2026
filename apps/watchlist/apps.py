from django.apps import AppConfig


class WatchlistConfig(AppConfig):
    """Companies, investors and people a user follows. The alert scan checks these for changes."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.watchlist"
    label = "watchlist"
    verbose_name = "Watch list"
