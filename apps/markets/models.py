from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class MarketAnalysis(BaseModel):
    """
    One report a user ran, kept as their search history: a market (sector + place) or one company (kind
    "company": entity_id is the Cala company, sector its name, region the place its competitors are from).

    `data` holds the processed result: the four Cala sections (size, deals, trends, players) and the checked
    executive summary; `plan` holds the Cala requests written from the typed text. A refresh re-asks Cala
    and overwrites `data` section by section, so the page keeps showing the previous answer until the new one
    arrives; `changes` says what the refresh changed. `backup` is what Stop rolls back to.
    """

    class Kind(models.TextChoices):
        MARKET = "market", "Market"
        COMPANY = "company", "Company"

    class Status(models.TextChoices):
        LOADING = "loading", "Loading"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="market_analyses"
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.MARKET)
    entity_id = models.CharField(
        max_length=64, blank=True, help_text="For a company analysis: the company's Cala entity id."
    )
    sector = models.CharField(max_length=120, help_text="Canonical market name the analysis ran for.")
    typed = models.CharField(max_length=300, blank=True, help_text="What the user typed, if different.")
    region = models.CharField(
        max_length=80, help_text="Place the analysis covers, e.g. 'Finland', 'Europe', 'Worldwide'."
    )
    title = models.CharField(max_length=120, blank=True)
    plan = models.JSONField(
        default=dict, blank=True, help_text="The Cala requests the LLM planned from the typed text."
    )
    backup = models.JSONField(
        default=dict,
        blank=True,
        help_text="The stored result before the current run, restored if the user stops the run.",
    )
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
                fields=("user", "kind", "sector", "region", "entity_id"),
                name="unique_analysis_per_user",
            )
        ]

    def __str__(self):
        return f"{self.sector} · {self.region}"
