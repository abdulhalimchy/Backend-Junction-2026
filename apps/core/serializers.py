"""Serializers shared across apps."""

from rest_framework import serializers


class SourceSerializer(serializers.Serializer):
    """One Cala source, as attached to every sourced fact.

    Stored as plain dicts inside JSONFields (`sources = [...]`), so this
    serializer both validates writes and documents the shape in the schema.
    """

    RANK_CHOICES = ("FILING", "PRIMARY", "PRESS", "AGGREGATOR")
    PRECISION_CHOICES = ("year", "month", "day")

    name = serializers.CharField(max_length=255)
    document = serializers.CharField(allow_blank=True, max_length=2048, help_text='"" when Cala derived it.')
    date = serializers.CharField(max_length=10, help_text="ISO date, at the precision on record.")
    date_precision = serializers.ChoiceField(
        choices=PRECISION_CHOICES, allow_null=True, required=False, default=None
    )
    source_rank = serializers.ChoiceField(
        choices=RANK_CHOICES, allow_null=True, help_text="null = derived by Cala itself."
    )
