from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import BaseModel

DEAL_FIELDS = (
    "invested_amount",
    "currency",
    "entry_round",
    "invested_on",
    "ownership",
    "board_seat",
    "status",
    "notes",
)


class PortfolioCompany(BaseModel):
    """A company in an investor's portfolio.

    The profile (name, description, stage, ...) is filled in the same way as a founder's startup: from Cala
    and the company's website, each value with its sources, reviewed by the user. The deal terms (amount,
    ownership, board seat, ...) are the investor's own and private: never looked up, never shown to anyone
    else.
    """

    class Enrichment(models.TextChoices):
        NONE = "none", "Not requested"
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXITED = "exited", "Exited"
        WRITTEN_OFF = "written_off", "Written off"

    class Origin(models.TextChoices):
        MANUAL = "manual", "Added by the user"
        CALA = "cala", "Suggested from Cala"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="portfolio_companies"
    )

    # Profile, auto-filled
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

    # Deal terms, the investor's own
    invested_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="EUR", blank=True)
    entry_round = models.CharField(max_length=50, blank=True, help_text="The round they came in at.")
    invested_on = models.CharField(max_length=10, blank=True, help_text="YYYY, YYYY-MM or YYYY-MM-DD.")
    ownership = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Percent.",
    )
    board_seat = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.MANUAL)

    # Background lookup, as for startups
    is_draft = models.BooleanField(default=False, db_index=True)
    enrichment_status = models.CharField(max_length=16, choices=Enrichment.choices, default=Enrichment.NONE)
    enrichment_error = models.TextField(blank=True)
    enrichment_note = models.TextField(blank=True, help_text="What the lookup found, shown to the user.")
    suggestions = models.JSONField(
        default=dict, blank=True, help_text="field -> {value, origin, sources} from the background lookup."
    )
    enriched_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ("name", "created_at")
        verbose_name = "portfolio company"
        verbose_name_plural = "portfolio companies"
        indexes = [models.Index(fields=("user", "is_draft"))]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "cala_company_id"),
                condition=Q(is_draft=False) & ~Q(cala_company_id=""),
                name="unique_portfolio_company_per_user",
            )
        ]

    def __str__(self):
        return self.name or self.domain or str(self.pk)


class DismissedSuggestion(BaseModel):
    """A company Cala links to the investor's fund that the investor said is not theirs."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dismissed_portfolio_suggestions"
    )
    cala_company_id = models.CharField(max_length=128)
    name = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=("user", "cala_company_id"), name="unique_dismissed_suggestion")
        ]

    def __str__(self):
        return self.name or self.cala_company_id


class PortfolioAnalysis(BaseModel):
    """A stored Cala analysis for one portfolio company, refreshed on request.

    rounds:      the company's recent funding rounds and money raised, to show what happened since the
                 investor came in.
    competitors: the companies Cala records as its competitors (or like it), with their latest rounds.
                 Saving one also refreshes the company's `MonitoredCompetitor` rows for the alert scan.
    investors:   investors in companies like it, scored as candidates to lead its next round.
    """

    class Kind(models.TextChoices):
        ROUNDS = "rounds", "Funding since you invested"
        COMPETITORS = "competitors", "Competitors"
        INVESTORS = "investors", "Next-round investors"

    class Status(models.TextChoices):
        LOADING = "loading", "Loading"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(PortfolioCompany, on_delete=models.CASCADE, related_name="analyses")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.LOADING)
    data = models.JSONField(default=dict, blank=True, help_text="The last completed result.")
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True, help_text="When `data` was computed.")

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=("company", "kind"), name="unique_portfolio_analysis_kind")
        ]

    def __str__(self):
        return f"{self.company} · {self.kind}"


class MonitoredCompetitor(BaseModel):
    """A competitor of a portfolio company, from its last competitors analysis. The alert scan checks these
    and tells the investor when one raises a round."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="monitored_competitors"
    )
    company = models.ForeignKey(PortfolioCompany, on_delete=models.CASCADE, related_name="competitors")
    cala_id = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=("company", "cala_id"), name="unique_monitored_competitor")
        ]

    def __str__(self):
        return f"{self.name or self.cala_id} (vs {self.company})"
