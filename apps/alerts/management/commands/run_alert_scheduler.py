from django.core.management.base import BaseCommand

from apps.alerts import nightly


class Command(BaseCommand):
    help = "Run the nightly alert scan at ALERT_SCAN_TIME (ALERT_SCAN_TIMEZONE). Runs until stopped."

    def add_arguments(self, parser):
        parser.add_argument(
            "--poll", type=float, default=nightly.POLL_SECONDS, help="Seconds between due checks."
        )

    def handle(self, *args, **opts):
        when = f"{nightly.next_slot():%Y-%m-%d %H:%M %Z}"
        self.stdout.write(f"Alert scheduler running; next nightly scan at {when}.")
        try:
            nightly.serve(poll=opts["poll"])
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")
