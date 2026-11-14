from rest_framework import serializers

from apps.alerts.models import Alert, ScanRun
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
            "reason",
            "href",
            "entity_type",
            "cala_id",
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


class ScanResultSerializer(serializers.Serializer):
    entities = serializers.IntegerField(help_text="Cala entities checked.")
    alerts_created = serializers.IntegerField()
    skipped = serializers.IntegerField(help_text="Watched ids that are demo fixtures, not Cala entities.")
    errors = serializers.DictField(
        child=serializers.CharField(), help_text="Cala id -> error, per failed entity."
    )


class ScanRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScanRun
        fields = ("trigger", "status", "started_at", "finished_at", "entities", "alerts_created", "skipped")


class ScheduleSerializer(serializers.Serializer):
    time = serializers.CharField(help_text="Local time of the nightly scan, HH:MM.")
    timezone = serializers.CharField()
    next_run_at = serializers.DateTimeField()
    last_nightly = ScanRunSerializer(allow_null=True)
    last_manual = ScanRunSerializer(allow_null=True, help_text='Your last "Check for changes".')
