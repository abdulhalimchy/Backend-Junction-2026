"""The nightly alert scan: every night at ALERT_SCAN_TIME (ALERT_SCAN_TIMEZONE), scan everything on any watch
list (`scan.targets()`) and fan the changes out as alerts.

A run is due when no nightly run has started since the most recent slot, so a server that was down at night
catches up as soon as it is back. A failed run (Cala down) is retried after RETRY_AFTER; a run that has been
"running" for longer than STALE_AFTER is presumed dead (the process stopped) and does not block the next one.

Three ways to drive it, all safe to combine because each checks `is_due` first:
- `python manage.py runserver` with ALERT_SCHEDULER_IN_PROCESS on (the default in development) runs `serve`
  in a background thread;
- `python manage.py run_alert_scheduler`, a long-running worker process (production);
- `python manage.py scan_alerts --nightly` from cron or a hosted scheduler, as often as you like.
"""

from __future__ import annotations

import datetime
import logging
import threading
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import scan
from .models import ScanRun

logger = logging.getLogger(__name__)

RETRY_AFTER = datetime.timedelta(hours=1)
STALE_AFTER = datetime.timedelta(hours=3)
POLL_SECONDS = 600


def zone() -> ZoneInfo:
    return ZoneInfo(settings.ALERT_SCAN_TIMEZONE)


def _slot_on(day: datetime.date) -> datetime.datetime:
    hour, minute = (int(p) for p in settings.ALERT_SCAN_TIME.split(":"))
    return datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=zone())


def last_slot(now: datetime.datetime | None = None) -> datetime.datetime:
    """The most recent scheduled time at or before `now`."""
    local = (now or timezone.now()).astimezone(zone())
    slot = _slot_on(local.date())
    return slot if slot <= local else _slot_on(local.date() - datetime.timedelta(days=1))


def next_slot(now: datetime.datetime | None = None) -> datetime.datetime:
    return _slot_on(last_slot(now).date() + datetime.timedelta(days=1))


def is_due(now: datetime.datetime | None = None) -> bool:
    now = now or timezone.now()
    runs = ScanRun.objects.filter(trigger=ScanRun.Trigger.NIGHTLY, started_at__gte=last_slot(now))
    if runs.filter(status=ScanRun.Status.OK).exists():
        return False
    if runs.filter(status=ScanRun.Status.RUNNING, started_at__gte=now - STALE_AFTER).exists():
        return False
    return not runs.filter(status=ScanRun.Status.FAILED, started_at__gte=now - RETRY_AFTER).exists()


def run_if_due(now: datetime.datetime | None = None) -> ScanRun | None:
    """Run the nightly scan if it is due. Returns the run (also when it failed), or None if not due."""
    if not is_due(now):
        return None
    pairs = scan.targets()
    logger.info("Nightly alert scan: %d entities", len(pairs))
    try:
        record, _ = scan.run(pairs, trigger=ScanRun.Trigger.NIGHTLY)
    except Exception:
        logger.exception("Nightly alert scan failed")
        return ScanRun.objects.filter(trigger=ScanRun.Trigger.NIGHTLY).first()
    logger.info(
        "Nightly alert scan: %d checked, %d new alerts, %d errors",
        record.entities,
        record.alerts_created,
        len(record.errors),
    )
    return record


def serve(stop: threading.Event | None = None, poll: float = POLL_SECONDS) -> None:
    """Check every `poll` seconds whether the nightly scan is due, until `stop` is set."""
    stop = stop or threading.Event()
    logger.info(
        "Alert scheduler: nightly at %s %s (next %s)",
        settings.ALERT_SCAN_TIME,
        settings.ALERT_SCAN_TIMEZONE,
        next_slot().isoformat(),
    )
    while not stop.is_set():
        close_old_connections()
        try:
            run_if_due()
        except Exception:  # never let the loop die (e.g. the database is briefly unavailable)
            logger.exception("Alert scheduler tick failed")
        finally:
            close_old_connections()
        stop.wait(poll)


_thread: threading.Thread | None = None


def start_in_process() -> None:
    """Start `serve` in a daemon thread, once per process."""
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=serve, daemon=True, name="alert-scheduler")
        _thread.start()
