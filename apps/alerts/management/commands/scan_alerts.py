from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.alerts import scan
from apps.core.cala import CalaError


class Command(BaseCommand):
    help = "Check watched Cala entities for changes and create alerts for everyone watching them."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Only scan the entities this user (email) watches.")
        parser.add_argument("--entity", help="Only scan this Cala id (company or investor).")
        parser.add_argument(
            "--type", choices=["company", "investor"], default="company", help="With --entity."
        )
        parser.add_argument("--backfill-days", type=int, help="Override ALERT_BACKFILL_DAYS for this run.")

    def handle(self, *args, **opts):
        if opts["entity"]:
            pairs = [(opts["type"], opts["entity"])]
        else:
            user = None
            if opts["user"]:
                user = User.objects.filter(email__iexact=opts["user"]).first()
                if user is None:
                    raise CommandError(f"No user with email {opts['user']}")
            pairs = scan.targets(user)

        self.stdout.write(f"Scanning {len(pairs)} entities...")
        try:
            result = scan.scan(pairs, backfill_days=opts["backfill_days"])
        except CalaError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned {result.entities}, created {result.alerts_created} alerts"
                f" ({len(result.skipped)} demo ids skipped)."
            )
        )
        for cala_id, error in result.errors.items():
            self.stderr.write(f"  {cala_id}: {error}")
