"""Write-side business logic for accounts. Views stay thin and call these."""

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Profile, User


@transaction.atomic
def register_user(*, email: str, password: str, full_name: str = "", role: str = "") -> User:
    """Create a user (and, via signal, their profile). Input is validated by the caller."""
    user = User.objects.create_user(email=email, password=password, full_name=full_name)
    if role:
        user.profile.role = role
        user.profile.save(update_fields=["role", "updated_at"])
    return user


def issue_tokens(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


logger = logging.getLogger(__name__)


def delete_account(*, user: User) -> None:
    """Delete the user and everything they own.

    Every user-owned table (profile, startups and their analyses, deck checks and claims, portfolio, market
    analyses, watch list, alerts, scan runs, RSVPs) has a CASCADE foreign key to the user, so deleting the row
    removes them. Two things a cascade does not cover are handled here: the uploaded deck PDFs on disk, and
    the user's refresh tokens, which are blacklisted so no session can be refreshed afterwards.
    """
    from apps.deck_checks.models import DeckCheck  # local import: deck_checks depends on accounts

    files = [d.file for d in DeckCheck.objects.filter(user=user).exclude(file="")]
    with transaction.atomic():
        for token in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token)
        user.delete()

    # Files go only once the rows are gone for good; a failed delete leaves both in place.
    for f in files:
        try:
            f.storage.delete(f.name)
        except OSError:
            logger.warning("Could not delete deck file %s of a deleted account", f.name)


def change_password(*, user: User, new_password: str) -> None:
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])


def update_profile(*, profile: Profile, data: dict) -> Profile:
    onboarding_completed = data.pop("onboarding_completed", None)
    for field, value in data.items():
        setattr(profile, field, value)
    if onboarding_completed is True and profile.onboarding_completed_at is None:
        profile.onboarding_completed_at = timezone.now()
    elif onboarding_completed is False:
        profile.onboarding_completed_at = None
    profile.save()
    return profile
