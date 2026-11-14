from rest_framework import serializers

from apps.core.serializers import SourceSerializer
from apps.events.models import Event, EventAttendance


class EventSerializer(serializers.ModelSerializer):
    sources = SourceSerializer(many=True, read_only=True)
    attendee_companies = serializers.IntegerField(read_only=True)
    attendee_investors = serializers.IntegerField(read_only=True)
    new_this_week = serializers.IntegerField(read_only=True)

    class Meta:
        model = Event
        fields = (
            "id",
            "slug",
            "name",
            "city",
            "starts_on",
            "ends_on",
            "cala_event_id",
            "attendee_companies",
            "attendee_investors",
            "new_this_week",
            "sources",
        )


class EventAttendanceSerializer(serializers.ModelSerializer):
    sources = SourceSerializer(many=True, read_only=True)

    class Meta:
        model = EventAttendance
        fields = ("id", "entity_type", "cala_entity_id", "name", "joined_on", "sources")
