from rest_framework import serializers

from apps.events.models import Event
from apps.networking.models import MetEntry


class MetEntrySerializer(serializers.ModelSerializer):
    event = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Event.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        help_text="Event slug, e.g. slush-2026.",
    )

    class Meta:
        model = MetEntry
        fields = (
            "id",
            "cala_person_id",
            "person_name",
            "met_on",
            "where",
            "note",
            "event",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
