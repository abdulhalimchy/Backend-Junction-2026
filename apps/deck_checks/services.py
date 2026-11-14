"""Write-side logic for deck checks."""

import threading

from django.conf import settings
from django.db import transaction
from rest_framework.exceptions import ValidationError

from . import pipeline
from .models import DeckCheck


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
