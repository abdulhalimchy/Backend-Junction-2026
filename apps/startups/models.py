from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

# Profile fields that can be auto-filled. Each may carry sources in `Startup.sources[field]`.
PROFILE_FIELDS = ("name", "description", "founded", "headquarters", "employees", "stage", "industry")


class Startup(BaseModel):
    """A startup the user runs or tracks as a founder.

    Adding by domain creates a draft and looks the company up in the background (Cala first, then the
    company's own website read by an LLM). The results land in `suggestions`; the user reviews them and
    saves, which turns the draft into a real startup. Startups added by hand skip the lookup.
    """

    class Enrichment(models.TextChoices):
        NONE = "none", "Not requested"
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="startups")
    name = models.CharField(max_length=200, blank=True)
    domain = models.CharField(max_length=255, blank=True, help_text="Bare host, e.g. northlight.ai.")
    description = models.TextField(blank=True)
    founded = models.CharField(max_length=10, blank=True, help_text="YYYY, YYYY-MM or YYYY-MM-DD.")
    headquarters = models.CharField(max_length=255, blank=True)
    employees = models.CharField(max_length=50, blank=True, help_text="Range, e.g. 11-50.")
    stage = models.CharField(max_length=50, blank=True)
    industry = models.CharField(max_length=200, blank=True)
    cala_company_id = models.CharField(max_length=128, blank=True)
    sources = models.JSONField(
        default=dict, blank=True, help_text="field -> list of sources, kept only for unedited suggestions."
    )

    is_draft = models.BooleanField(default=False, db_index=True)
    enrichment_status = models.CharField(max_length=16, choices=Enrichment.choices, default=Enrichment.NONE)
    enrichment_error = models.TextField(blank=True)
    enrichment_note = models.TextField(blank=True, help_text="What the lookup found, shown to the user.")
    suggestions = models.JSONField(
        default=dict, blank=True, help_text="field -> {value, origin, sources} from the background lookup."
    )
    enriched_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("created_at",)
        indexes = [models.Index(fields=("user", "is_draft"))]

    def __str__(self):
        return self.name or self.domain or str(self.pk)


class StartupAnalysis(BaseModel):
    """A stored Cala analysis for one tab of a startup (competitors or find-investors).

    Runs only when the user asks for it, and again on refresh. While a refresh runs, the previous `data`
    stays so the page can keep showing it.
    """

    class Kind(models.TextChoices):
        COMPETITORS = "competitors", "Competitors"
        INVESTORS = "investors", "Find investors"

    class Status(models.TextChoices):
        LOADING = "loading", "Loading"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    startup = models.ForeignKey(Startup, on_delete=models.CASCADE, related_name="analyses")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LOADING)
    data = models.JSONField(default=dict, blank=True, help_text="The last completed result.")
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True, help_text="When `data` was computed.")

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=("startup", "kind"), name="unique_startup_analysis_kind")
        ]

    def __str__(self):
        return f"{self.startup} · {self.kind}"
