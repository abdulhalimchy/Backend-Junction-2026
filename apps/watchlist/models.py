from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class WatchItemQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(user=user)


class WatchItem(BaseModel):
    """One entity on a user's watch list. Private to its owner: nobody can see who watches them."""

    class EntityType(models.TextChoices):
        COMPANY = "company", "Company"
        INVESTOR = "investor", "Investor"
        PERSON = "person", "Person"

    class Tag(models.TextChoices):
        # Founders
        COMPETITOR = "competitor", "Competitor"
        TARGET_INVESTOR = "target_investor", "Target investor"
        PARTNER = "partner", "Partner"
        # Investors
        PIPELINE = "pipeline", "Pipeline"
        PORTFOLIO = "portfolio", "Portfolio"
        PASSED = "passed", "Passed"

    class Origin(models.TextChoices):
        MANUAL = "manual", "Added by the user"
        SUGGESTED = "suggested", "Suggested from the profile"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watch_items")
    entity_type = models.CharField(max_length=16, choices=EntityType.choices)
    cala_id = models.CharField(max_length=128, help_text="The entity, as identified in Cala.")
    name = models.CharField(max_length=255, blank=True, help_text="Display name cached at save time.")
    tag = models.CharField(max_length=32, choices=Tag.choices, blank=True)
    note = models.TextField(blank=True)
    origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.MANUAL)

    objects = WatchItemQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        verbose_name = "watch item"
        verbose_name_plural = "watch items"
        constraints = [
            models.UniqueConstraint(fields=("user", "entity_type", "cala_id"), name="unique_watch_per_user"),
        ]
        # The alert scan fans one entity out to all its watchers.
        indexes = [models.Index(fields=("cala_id",))]

    def __str__(self):
        return f"{self.name or self.cala_id} ({self.entity_type})"
