from rest_framework import serializers

from apps.alerts.scan import UUID
from apps.watchlist.models import WatchItem


class WatchItemSerializer(serializers.ModelSerializer):
    unread_alerts = serializers.IntegerField(read_only=True, default=0, help_text="Your unread alerts on it.")
    last_checked_at = serializers.DateTimeField(
        read_only=True, allow_null=True, default=None, help_text="When the alert scan last checked it."
    )
    in_cala = serializers.SerializerMethodField(help_text="False for demo entries the alert scan skips.")

    class Meta:
        model = WatchItem
        fields = (
            "id",
            "entity_type",
            "cala_id",
            "name",
            "tag",
            "note",
            "origin",
            "unread_alerts",
            "last_checked_at",
            "in_cala",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "origin", "created_at", "updated_at")
        # Uniqueness per user is handled in the view (adding twice returns the existing item).
        validators = []

    def get_in_cala(self, obj) -> bool:
        return bool(UUID.match(obj.cala_id))

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:
            # The entity an item points at never changes; remove and re-add instead.
            fields["entity_type"].read_only = True
            fields["cala_id"].read_only = True
        return fields


class WatchSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    by_type = serializers.DictField(child=serializers.IntegerField())
    by_tag = serializers.DictField(child=serializers.IntegerField())
