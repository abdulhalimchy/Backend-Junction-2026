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
        PORTFOLIO_COMPETITOR = "portfolio_competitor", "Portfolio competitor"
        INVESTOR_ACTIVITY = "investor_activity", "Investor activity"
        SLUSH_ATTENDEE = "slush_attendee", "Slush attendee"
        PROFILE = "profile", "Profile"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alerts")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    href = models.CharField(max_length=512, blank=True, help_text="In-app path the alert links to.")
    event = models.ForeignKey(
        "events.Event", on_delete=models.SET_NULL, null=True, blank=True, related_name="alerts"
    )
    occurred_on = models.DateField(help_text="Date of the underlying fact, not of the alert.")
    read_at = models.DateTimeField(null=True, blank=True)
    sources = models.JSONField(default=list, blank=True)

    objects = AlertQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        ordering = ("-occurred_on", "-created_at")
        indexes = [models.Index(fields=("user", "read_at"))]

    def __str__(self):
        return self.title

    @property
    def is_read(self):
        return self.read_at is not None
