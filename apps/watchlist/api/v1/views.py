from django.db.models import Count, IntegerField, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.alerts import scan
from apps.alerts.models import Alert, EntitySnapshot
from apps.watchlist.models import WatchItem

from .serializers import WatchItemSerializer, WatchSummarySerializer


def with_alert_state(qs):
    """Each item's unread alerts (for its owner) and when the alert scan last checked the entity."""
    unread = (
        Alert.objects.filter(user=OuterRef("user"), cala_id=OuterRef("cala_id"), read_at__isnull=True)
        .order_by()
        .values("cala_id")
        .annotate(n=Count("id"))
        .values("n")
    )
    checked = EntitySnapshot.objects.filter(cala_id=OuterRef("cala_id")).values("checked_at")[:1]
    return qs.annotate(
        unread_alerts=Coalesce(Subquery(unread, output_field=IntegerField()), 0),
        last_checked_at=Subquery(checked),
    )


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("type", enum=WatchItem.EntityType.values, required=False),
            OpenApiParameter("tag", str, required=False, description='A tag, or "none" for untagged items.'),
            OpenApiParameter("cala_id", str, required=False, description="Is this entity watched?"),
            OpenApiParameter("q", str, required=False, description="Search name and note."),
        ]
    ),
    create=extend_schema(description="Watch an entity. Adding one that is already watched returns it (200)."),
)
@extend_schema(tags=["watchlist"])
class WatchItemViewSet(viewsets.ModelViewSet):
    """The signed-in user's watch list. Items are private to their owner."""

    serializer_class = WatchItemSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation, no real user
            return WatchItem.objects.none()
        qs = with_alert_state(WatchItem.objects.for_user(self.request.user))
        if self.action == "list":
            params = self.request.query_params
            if entity_type := params.get("type"):
                qs = qs.filter(entity_type=entity_type)
            if tag := params.get("tag"):
                qs = qs.filter(tag="" if tag == "none" else tag)
            if cala_id := params.get("cala_id"):
                qs = qs.filter(cala_id=cala_id)
            if q := params.get("q"):
                qs = qs.filter(Q(name__icontains=q) | Q(note__icontains=q))
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        item, created = WatchItem.objects.get_or_create(
            user=request.user,
            entity_type=data["entity_type"],
            cala_id=data["cala_id"],
            defaults={k: v for k, v in data.items() if k in ("name", "tag", "note")},
        )
        if created:
            scan.scan_new_watch(item)
        item = with_alert_state(WatchItem.objects.filter(pk=item.pk)).get()
        return Response(
            self.get_serializer(item).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @extend_schema(responses=WatchSummarySerializer)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Counts for the "Watching N companies and investors" panel."""
        qs = WatchItem.objects.for_user(request.user)
        by_type = dict(qs.values_list("entity_type").annotate(n=Count("id")).order_by())
        by_tag = dict(qs.exclude(tag="").values_list("tag").annotate(n=Count("id")).order_by())
        return Response({"total": sum(by_type.values()), "by_type": by_type, "by_tag": by_tag})
