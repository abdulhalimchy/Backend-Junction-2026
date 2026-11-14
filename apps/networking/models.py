from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class MetEntry(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="met_entries")
    cala_person_id = models.CharField(max_length=128, help_text="The person, as identified in Cala.")
    person_name = models.CharField(max_length=200, blank=True, help_text="Display name cached at save time.")
    met_on = models.DateField()
    where = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    event = models.ForeignKey(
        "events.Event", on_delete=models.SET_NULL, null=True, blank=True, related_name="met_entries"
    )

    class Meta(BaseModel.Meta):
        ordering = ("-met_on", "-created_at")
        verbose_name = "met entry"
        verbose_name_plural = "met entries"
        indexes = [models.Index(fields=("user", "met_on"))]

    def __str__(self):
        return f"{self.person_name or self.cala_person_id} ({self.met_on})"
