from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile, User


@receiver(post_save, sender=User, dispatch_uid="accounts.create_profile")
def create_profile_for_new_user(sender, instance, created, raw=False, **kwargs):
    """Every user has exactly one profile, however the user was created
    (API, admin, createsuperuser, fixtures excluded via `raw`)."""
    if created and not raw:
        Profile.objects.get_or_create(user=instance)
