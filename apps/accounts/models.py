from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel, UUIDModel

from .managers import UserManager


class User(UUIDModel, AbstractBaseUser, PermissionsMixin):
    """Custom user model. Defined up front so it never has to be swapped later."""

    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ("-date_joined",)
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self):
        return self.email

    def clean(self):
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email).lower()

    def get_full_name(self):
        return self.full_name or self.email

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email


class Profile(BaseModel):
    """Product-level data about a user, kept apart from authentication fields."""

    class Role(models.TextChoices):
        FOUNDER = "founder", "Founder"
        INVESTOR = "investor", "Investor"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=16, choices=Role.choices, blank=True)
    title = models.CharField(max_length=150, blank=True)
    goals = models.JSONField(default=list, blank=True)

    # Links to entities in Cala's knowledge graph (ids are owned by Cala).
    cala_person_id = models.CharField(max_length=128, blank=True)
    cala_company_id = models.CharField(max_length=128, blank=True)
    cala_investor_id = models.CharField(max_length=128, blank=True)

    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    # Privacy preferences
    show_in_event_directory = models.BooleanField(default=True)
    show_card_in_search = models.BooleanField(default=True)
    share_met_list = models.BooleanField(default=False)
    allow_alerts_email = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name = "profile"
        verbose_name_plural = "profiles"

    def __str__(self):
        return f"Profile<{self.user.email}>"

    @property
    def is_onboarded(self):
        return self.onboarding_completed_at is not None
