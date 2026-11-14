"""Write-side logic for startups."""

import threading

from django.conf import settings
from django.db import transaction

from . import enrichment
from .models import PROFILE_FIELDS, Startup


@transaction.atomic
def start_lookup(*, user, domain: str) -> Startup:
    """A draft startup for `domain`; the lookup runs in the background and fills `suggestions`."""
    startup = Startup.objects.create(
        user=user, domain=domain, is_draft=True, enrichment_status=Startup.Enrichment.PENDING
    )
    transaction.on_commit(lambda: enqueue(startup.pk))
    return startup


def enqueue(startup_id) -> None:
    """Run the lookup off the request. A thread is enough for one process; swap for a task queue to scale."""
    if settings.DECK_CHECK_ASYNC:
        threading.Thread(
            target=enrichment.run_enrichment, args=(startup_id,), daemon=True, name=f"startup-{startup_id}"
        ).start()
    else:
        enrichment.run_enrichment(startup_id)


def save_startup(startup: Startup, data: dict) -> Startup:
    """Apply edits. A value kept exactly as suggested keeps its sources; an edited value loses them (it is
    now the user's word, not the source's)."""
    sources = dict(startup.sources or {})
    for field in PROFILE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        suggestion = (startup.suggestions or {}).get(field)
        if suggestion and suggestion.get("value") == value and suggestion.get("sources"):
            sources[field] = suggestion["sources"]
        elif value != getattr(startup, field):
            sources.pop(field, None)
        if not value:
            sources.pop(field, None)
    for key, value in data.items():
        setattr(startup, key, value)
    startup.sources = sources
    startup.save()
    return startup


@transaction.atomic
def delete_startup(startup: Startup) -> None:
    """Delete a startup with its analyses and deck checks, including the uploaded PDFs."""
    from apps.deck_checks.services import delete_deck_check

    for deck in startup.deck_checks.all():
        delete_deck_check(deck)
    startup.delete()
