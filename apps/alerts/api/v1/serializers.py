from rest_framework import serializers

from apps.alerts.models import Alert
from apps.core.serializers import SourceSerializer


class AlertSerializer(serializers.ModelSerializer):
    event = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    is_read = serializers.BooleanField(read_only=True)
    sources = SourceSerializer(many=True, read_only=True)

    class Meta:
        model = Alert
        fields = (
            "id",
            "kind",
            "title",
            "body",
            "href",
            "event",
            "occurred_on",
            "is_read",
            "read_at",
            "sources",
        )


class UnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField()


class MarkedReadSerializer(serializers.Serializer):
    marked_read = serializers.IntegerField()
