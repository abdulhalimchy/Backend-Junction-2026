from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel


class Event(BaseModel):
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=200, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField()
    cala_event_id = models.CharField(max_length=128, blank=True, db_index=True)
    is_active = models.BooleanField(default=True)
    sources = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-starts_on",)
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__gte=models.F("starts_on")), name="event_ends_after_start"
            ),
        ]

    def __str__(self):
        return self.name


class EventAttendance(BaseModel):
    """A company or investor listed as attending an event (mirrored from Cala)."""

    class EntityType(models.TextChoices):
        COMPANY = "company", "Company"
        INVESTOR = "investor", "Investor"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attendances")
    entity_type = models.CharField(max_length=16, choices=EntityType.choices)
    cala_entity_id = models.CharField(max_length=128)
    name = models.CharField(max_length=200)
    joined_on = models.DateField(help_text="When the attendance relation first appeared.")
    sources = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("-joined_on", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "entity_type", "cala_entity_id"), name="unique_event_attendance"
            ),
        ]
        indexes = [models.Index(fields=("event", "entity_type", "joined_on"))]

    def __str__(self):
        return f"{self.name} @ {self.event}"
