import os
import sys

from django.apps import AppConfig
from django.conf import settings


class AlertsConfig(AppConfig):
    """Per-user notifications: competitor rounds, investor activity, Slush attendees, ..."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.alerts"
    label = "alerts"
    verbose_name = "Alerts"

    def ready(self):
        # The nightly scan in the dev server itself, so it runs without a separate worker. Only in the process
        # that serves requests (the autoreloader's child, or --noreload), never in other commands or tests.
        if not settings.ALERT_SCHEDULER_IN_PROCESS or "runserver" not in sys.argv:
            return
        if os.environ.get("RUN_MAIN") == "true" or "--noreload" in sys.argv:
            from . import nightly

            nightly.start_in_process()
