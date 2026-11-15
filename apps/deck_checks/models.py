import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models

from apps.core.models import BaseModel


def deck_upload_path(instance, filename):
    # Never reuse the client's filename on disk; it's kept in `file_name` instead.
    return f"deck_checks/{instance.user_id}/{uuid.uuid4().hex}.pdf"


class DeckCheck(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deck_checks")
    file = models.FileField(upload_to=deck_upload_path, validators=[FileExtensionValidator(["pdf"])])
    file_name = models.CharField(max_length=255, help_text="Original filename as uploaded.")
    file_size = models.PositiveIntegerField()
    cala_company_id = models.CharField(max_length=128, blank=True, help_text="Company the deck is about.")
    startup = models.ForeignKey(
        "startups.Startup",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="deck_checks",
        help_text="The founder's startup this deck is for. Empty for the demo company and investor uploads.",
    )
    company_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Used to find the company in Cala; taken from the deck if empty.",
    )
    pages = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    error = models.TextField(blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    # What makes a re-check of the same PDF repeatable (see pipeline._run).
    file_sha256 = models.CharField(max_length=64, blank=True, help_text="SHA-256 of the uploaded PDF.")
    reading = models.JSONField(
        null=True,
        blank=True,
        help_text="The claims the model read from the deck, reused when the same file is checked again.",
    )
    evidence_hash = models.CharField(
        max_length=64, blank=True, help_text="Fingerprint of the record evidence the verdicts were judged on."
    )

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=("user", "created_at")),
            models.Index(fields=("user", "file_sha256"), name="deck_user_sha_idx"),
        ]

    def __str__(self):
        return self.file_name


class DeckClaim(BaseModel):
    class Category(models.TextChoices):
        FUNDING = "funding", "Funding"
        TEAM = "team", "Team"
        TRACTION = "traction", "Traction"
        MARKET = "market", "Market"
        COMPANY = "company", "Company"

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        DIFFERS = "differs", "Differs"
        # Unconfirmed claims are "can't verify", never "false".
        CANT_VERIFY = "cant_verify", "Can't verify"

    class Basis(models.TextChoices):
        RECORD = "record", "Cala record"
        WEB = "web", "Web sources"

    deck_check = models.ForeignKey(DeckCheck, on_delete=models.CASCADE, related_name="claims")
    slide = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    claim = models.TextField()
    category = models.CharField(max_length=16, choices=Category.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    on_record = models.TextField(blank=True, help_text="What Cala holds, when it confirms or differs.")
    sources = models.JSONField(default=list, blank=True)
    basis = models.CharField(
        max_length=8,
        choices=Basis.choices,
        blank=True,
        help_text="Where a confirmed or differs verdict came from: Cala's record or searched web sources.",
    )

    class Meta(BaseModel.Meta):
        ordering = ("slide", "created_at")

    def __str__(self):
        return f"Slide {self.slide}: {self.claim[:50]}"
