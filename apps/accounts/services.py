"""Write-side business logic for accounts. Views stay thin and call these."""

from django.db import transaction
from django.utils import timezone
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
