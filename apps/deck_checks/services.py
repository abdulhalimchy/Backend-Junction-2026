"""Write-side logic for deck checks."""

from django.db import transaction

from .models import DeckCheck


@transaction.atomic
def create_deck_check(*, user, file, cala_company_id: str = "") -> DeckCheck:
    deck_check = DeckCheck.objects.create(
        user=user,
        file=file,
        file_name=file.name[:255],
        file_size=file.size,
        cala_company_id=cala_company_id,
    )
    # Extracting claims and verifying them against Cala is not built yet. When it is,
    # enqueue it here with transaction.on_commit(...) so it runs off the request.
    return deck_check


def delete_deck_check(deck_check: DeckCheck) -> None:
    storage, name = deck_check.file.storage, deck_check.file.name
    deck_check.delete()
    # Remove the file only once the row deletion is committed.
    transaction.on_commit(lambda: storage.delete(name) if name else None)
