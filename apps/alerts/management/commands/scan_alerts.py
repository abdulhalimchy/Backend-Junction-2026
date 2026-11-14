from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.alerts import nightly, scan
from apps.alerts.models import ScanRun
from apps.core.cala import CalaError


class Command(BaseCommand):
    help = "Check watched Cala entities for changes and create alerts for everyone watching them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--nightly",
            action="store_true",
            help="Run the nightly scan only if it is due (safe to call from cron as often as you like).",
        )
        parser.add_argument("--user", help="Only scan the entities this user (email) watches.")
        parser.add_argument("--entity", help="Only scan this Cala id (company or investor).")
        parser.add_argument(
            "--type", choices=["company", "investor"], default="company", help="With --entity."
        )
        parser.add_argument("--backfill-days", type=int, help="Override ALERT_BACKFILL_DAYS for this run.")

    def handle(self, *args, **opts):
        if opts["nightly"]:
            record = nightly.run_if_due()
            if record is None:
                self.stdout.write(f"Not due; next nightly scan at {nightly.next_slot():%Y-%m-%d %H:%M %Z}.")
                return
            if record.status != ScanRun.Status.OK:
                raise CommandError(f"Nightly scan failed: {record.errors}")
            self._report(record.entities, record.alerts_created, record.skipped, record.errors)
            return

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
            _, result = scan.run(pairs, trigger=ScanRun.Trigger.COMMAND, backfill_days=opts["backfill_days"])
        except CalaError as exc:
            raise CommandError(str(exc)) from exc
        self._report(result.entities, result.alerts_created, len(result.skipped), result.errors)

    def _report(self, entities, created, skipped, errors):
        self.stdout.write(
            self.style.SUCCESS(f"Scanned {entities}, created {created} alerts ({skipped} demo ids skipped).")
        )
        for cala_id, error in errors.items():
            self.stderr.write(f"  {cala_id}: {error}")
