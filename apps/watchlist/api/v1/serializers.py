from rest_framework import serializers

from apps.watchlist.models import WatchItem


class WatchItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = WatchItem
        fields = ("id", "entity_type", "cala_id", "name", "tag", "note", "origin", "created_at", "updated_at")
        read_only_fields = ("id", "origin", "created_at", "updated_at")
        # Uniqueness per user is handled in the view (adding twice returns the existing item).
        validators = []

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
