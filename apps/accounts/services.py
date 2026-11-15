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


class GoogleSignInError(Exception):
    """The Google credential was missing, invalid, expired, for another app, or its email is unverified."""


class GoogleSignInNotConfiguredError(Exception):
    """GOOGLE_CLIENT_ID is not set, so no credential can be checked."""


def verify_google_credential(credential: str) -> dict:
    """Check a Google ID token (signature, expiry, issuer, audience) and return its claims."""
    from django.conf import settings
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    if not settings.GOOGLE_CLIENT_ID:
        raise GoogleSignInNotConfiguredError("Google sign-in is not configured (GOOGLE_CLIENT_ID).")
    try:
        claims = id_token.verify_oauth2_token(
            credential, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError as exc:  # bad signature, expired, wrong audience or issuer
        raise GoogleSignInError("Google sign-in failed. Try again.") from exc
    if not claims.get("email") or not claims.get("email_verified"):
        raise GoogleSignInError("Your Google account has no verified email address.")
    return claims


@transaction.atomic
def sign_in_with_google(*, claims: dict) -> tuple[User, bool]:
    """The user for a verified Google identity, matched on email, created without a password on first sign-in.

    Returns (user, created). An existing account keeps its password; it takes Google's name if it has none.
    """
    email = User.objects.normalize_email(claims["email"]).lower()
    name = (claims.get("name") or "").strip()[:150]
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = User.objects.create_user(email=email, password=None, full_name=name)  # unusable password
        return user, True
    if not user.is_active:
        raise GoogleSignInError("This account is disabled.")
    if name and not user.full_name:
        user.full_name = name
        user.save(update_fields=["full_name", "updated_at"])
    return user, False


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


# Account plumbing left out of the export: sign-in tokens, admin history, permissions.
_EXPORT_SKIP = {"token_blacklist", "admin", "auth"}
_USER_SKIP = {"password"}


def _row(obj) -> dict:
    """A model instance as plain JSON values: concrete fields only, files as their stored name."""
    from django.db.models import FileField

    out = {}
    for field in obj._meta.concrete_fields:
        if field.name in _USER_SKIP:
            continue
        value = getattr(obj, field.attname)  # a foreign key's attname is its id: "startup_id"
        out[field.attname] = (value.name or "") if isinstance(field, FileField) else value
    return out


def _children(obj, seen: set) -> dict:
    """Rows of the models that hang off `obj` and are deleted with it (one-to-many and one-to-one)."""
    from django.db.models import CASCADE

    out = {}
    for rel in obj._meta.related_objects:
        model = rel.related_model
        if model._meta.app_label in _EXPORT_SKIP or rel.on_delete is not CASCADE or model in seen:
            continue
        accessor = rel.get_accessor_name()
        if rel.one_to_one:
            child = getattr(obj, accessor, None)
            rows = [child] if child is not None else []
        else:
            rows = list(getattr(obj, accessor).all())
        if not rows:
            continue
        key = f"{model._meta.app_label}.{model._meta.model_name}"
        out[key] = [{**_row(r), **_children(r, seen | {model, type(obj)})} for r in rows]
    return out


def export_user_data(*, user: User) -> dict:
    """Everything stored about the user, for the right of access and data portability (GDPR art. 15 and 20).

    Walks every table deleted with the user (see delete_account), so a new user-owned model is included
    without changes here. Uploaded PDFs are listed by their stored name, not embedded.
    """
    return {
        "exported_at": timezone.now(),
        "account": _row(user),
        **_children(user, {User}),
    }


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
