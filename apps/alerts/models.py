from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel


class AlertQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(user=user)

    def unread(self):
        return self.filter(read_at__isnull=True)

    def mark_read(self):
        return self.unread().update(read_at=timezone.now())


class Alert(BaseModel):
    class Kind(models.TextChoices):
        COMPETITOR_ROUND = "competitor_round", "Competitor raised a round"
        FUNDING_ROUND = "funding_round", "Raised a round"
        PORTFOLIO_COMPETITOR = "portfolio_competitor", "Portfolio competitor"
        INVESTOR_ACTIVITY = "investor_activity", "Investor activity"
        SLUSH_ATTENDEE = "slush_attendee", "Slush attendee"
        TEAM_CHANGE = "team_change", "Team size changed"
        PROFILE = "profile", "Profile"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alerts")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    reason = models.CharField(
        max_length=255, blank=True, help_text='Why this user sees it, e.g. "One of your competitors".'
    )
    href = models.CharField(max_length=512, blank=True, help_text="In-app path the alert links to.")
    event = models.ForeignKey(
        "events.Event", on_delete=models.SET_NULL, null=True, blank=True, related_name="alerts"
    )
    occurred_on = models.DateField(help_text="Date of the underlying fact, not of the alert.")
    read_at = models.DateTimeField(null=True, blank=True)
    sources = models.JSONField(default=list, blank=True)
    dedupe_key = models.CharField(
        max_length=255,
        blank=True,
        help_text="Stable id of the underlying change; one alert per user and key.",
    )

    objects = AlertQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        ordering = ("-occurred_on", "-created_at")
        indexes = [models.Index(fields=("user", "read_at"))]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "dedupe_key"),
                condition=~models.Q(dedupe_key=""),
                name="unique_alert_per_change",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def is_read(self):
        return self.read_at is not None


class EntitySnapshot(BaseModel):
    """What the alert scan last saw for one Cala entity, so the next scan can tell what changed."""

    entity_type = models.CharField(max_length=16)
    cala_id = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=255, blank=True)
    facts = models.JSONField(default=dict, blank=True)
    checked_at = models.DateTimeField()

    class Meta(BaseModel.Meta):
        verbose_name = "entity snapshot"
        verbose_name_plural = "entity snapshots"

    def __str__(self):
        return f"{self.name or self.cala_id} @ {self.checked_at:%Y-%m-%d %H:%M}"
