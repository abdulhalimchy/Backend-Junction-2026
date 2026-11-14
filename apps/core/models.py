"""Abstract base models. Concrete apps inherit from these; core owns no tables."""

import uuid

from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Non-guessable primary key, safe to expose in URLs and API payloads."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel):
    """Default base for domain models: UUID pk + created/updated timestamps."""

    class Meta:
        abstract = True
        ordering = ("-created_at",)
