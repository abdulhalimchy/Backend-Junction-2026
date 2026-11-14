from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Users, authentication and user profiles."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"

    def ready(self):
        from . import signals  # noqa: F401
