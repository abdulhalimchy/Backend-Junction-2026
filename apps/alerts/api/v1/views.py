from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.alerts.models import Alert

from .serializers import AlertSerializer, MarkedReadSerializer, UnreadCountSerializer


@extend_schema(tags=["alerts"])
class AlertViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The signed-in user's alerts. Alerts are created by the backend, never by clients."""

    serializer_class = AlertSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation, no real user
            return Alert.objects.none()
        qs = Alert.objects.for_user(self.request.user).select_related("event")
        if self.action == "list":
            params = self.request.query_params
            if params.get("unread") in ("1", "true"):
                qs = qs.unread()
            if kind := params.get("kind"):
                qs = qs.filter(kind=kind)
            if event := params.get("event"):
                qs = qs.filter(event__slug=event)
        return qs

    @extend_schema(
        parameters=[
            OpenApiParameter("unread", bool, required=False),
            OpenApiParameter("kind", enum=Alert.Kind.values, required=False),
            OpenApiParameter("event", str, required=False, description="Event slug, e.g. slush-2026."),
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=None, responses=AlertSerializer)
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        alert = self.get_object()
        if alert.read_at is None:
            alert.read_at = timezone.now()
            alert.save(update_fields=["read_at", "updated_at"])
        return Response(self.get_serializer(alert).data)

    @extend_schema(request=None, responses=MarkedReadSerializer)
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        count = Alert.objects.for_user(request.user).mark_read()
        return Response({"marked_read": count})

    @extend_schema(responses=UnreadCountSerializer)
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": Alert.objects.for_user(request.user).unread().count()})
