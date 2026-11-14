"""Write-side logic for deck checks."""

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from . import pipeline
from .models import DeckCheck

logger = logging.getLogger(__name__)

INTERRUPTED = "The check was interrupted before it finished (the server restarted). Retry it."


@transaction.atomic
def create_deck_check(
    *, user, file, cala_company_id: str = "", company_name: str = "", startup=None
) -> DeckCheck:
    deck_check = DeckCheck.objects.create(
        user=user,
        startup=startup,
        file=file,
        file_name=file.name[:255],
        file_size=file.size,
        cala_company_id=cala_company_id,
        company_name=company_name,
    )
    transaction.on_commit(lambda: enqueue(deck_check.pk))
    return deck_check


def enqueue(deck_id) -> None:
    """Run the check off the request. A thread is enough for one process; swap for a task queue to scale."""
    if settings.DECK_CHECK_ASYNC:
        threading.Thread(
            target=pipeline.run_deck_check, args=(deck_id,), daemon=True, name=f"deck-check-{deck_id}"
        ).start()
    else:
        pipeline.run_deck_check(deck_id)


def fail_stale_deck_checks(queryset) -> int:
    """Mark checks that stopped updating as failed, so the page stops waiting and offers Retry.

    The check runs in a thread inside the web process; when that process restarts, the thread is gone and
    the row would stay "processing" forever. Done on read rather than at startup, so it is safe with several
    workers.
    """
    now = timezone.now()
    cutoff = now - timedelta(minutes=settings.DECK_CHECK_STALE_MINUTES)
    n = queryset.filter(
        status__in=(DeckCheck.Status.PENDING, DeckCheck.Status.PROCESSING), updated_at__lt=cutoff
    ).update(status=DeckCheck.Status.FAILED, error=INTERRUPTED, checked_at=now, updated_at=now)
    if n:
        logger.warning("Marked %d interrupted deck check(s) as failed", n)
    return n


def retry_deck_check(deck_check: DeckCheck) -> DeckCheck:
    if deck_check.status != DeckCheck.Status.FAILED:
        raise ValidationError("Only a failed check can be retried.")
    deck_check.status = DeckCheck.Status.PENDING
    deck_check.error = ""
    deck_check.save(update_fields=["status", "error", "updated_at"])
    transaction.on_commit(lambda: enqueue(deck_check.pk))
    return deck_check


def delete_deck_check(deck_check: DeckCheck) -> None:
    storage, name = deck_check.file.storage, deck_check.file.name
    deck_check.delete()
    # Remove the file only once the row deletion is committed.
    transaction.on_commit(lambda: storage.delete(name) if name else None)
