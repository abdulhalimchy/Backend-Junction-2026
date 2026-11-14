from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class MarketAnalysis(BaseModel):
    """
    One market report (sector + region) a user ran, kept as their search history.

    `data` holds the processed result: the four Cala sections (size, deals, trends, players) and the checked
    executive summary. A refresh re-asks Cala and overwrites `data` section by section, so the page keeps
    showing the previous answer until the new one arrives; `changes` says what the refresh changed.
    """

    class Status(models.TextChoices):
        LOADING = "loading", "Loading"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="market_analyses"
    )
    sector = models.CharField(max_length=120, help_text="Canonical market name the analysis ran for.")
    typed = models.CharField(max_length=120, blank=True, help_text="What the user typed, if different.")
    region = models.CharField(max_length=32)
    year = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LOADING)
    data = models.JSONField(default=dict, blank=True)
    changes = models.JSONField(default=list, blank=True, help_text="What the last refresh changed.")
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    runs = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "sector", "region"), name="unique_market_analysis_per_user"
            )
        ]

    def __str__(self):
        return f"{self.sector} · {self.region}"
