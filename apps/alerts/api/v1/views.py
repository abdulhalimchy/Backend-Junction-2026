from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.alerts import nightly, scan
from apps.alerts.models import Alert, ScanRun
from apps.core.cala import CalaError

from .serializers import (
    AlertSerializer,
    MarkedReadSerializer,
    ScanResultSerializer,
    ScheduleSerializer,
    UnreadCountSerializer,
)


@extend_schema(tags=["alerts"])
class AlertViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """The signed-in user's alerts. Alerts are created by the backend, never by clients."""

    serializer_class = AlertSerializer
    throttle_scope = None  # set per action (scan); DRF only accepts action overrides for existing attributes

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
            if cala_id := params.get("cala_id"):
                qs = qs.filter(cala_id=cala_id)
        return qs

    @extend_schema(
        parameters=[
            OpenApiParameter("unread", bool, required=False),
            OpenApiParameter("kind", enum=Alert.Kind.values, required=False),
            OpenApiParameter("event", str, required=False, description="Event slug, e.g. slush-2026."),
            OpenApiParameter("cala_id", str, required=False, description="Alerts about this watched entity."),
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

    @extend_schema(request=None, responses=ScanResultSerializer)
    @action(
        detail=False,
        methods=["post"],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="alert_scan",
    )
    def scan(self, request):
        """Check the companies and investors you watch for changes now. Runs inline; costs Cala calls."""
        try:
            pairs = scan.targets(request.user)
            _, result = scan.run(pairs, trigger=ScanRun.Trigger.MANUAL, user_id=request.user.pk)
        except CalaError as exc:
            return Response(
                {"error": {"status_code": 503, "code": "cala_unavailable", "message": str(exc)}},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(result.as_dict())

    @extend_schema(responses=ScheduleSerializer)
    @action(detail=False, methods=["get"])
    def schedule(self, request):
        """When the watch list is checked: the nightly schedule, the last nightly run, your last check."""
        runs = ScanRun.objects.exclude(status=ScanRun.Status.RUNNING)
        data = ScheduleSerializer(
            {
                "time": settings.ALERT_SCAN_TIME,
                "timezone": settings.ALERT_SCAN_TIMEZONE,
                "next_run_at": nightly.next_slot(),
                "last_nightly": runs.filter(trigger=ScanRun.Trigger.NIGHTLY).first(),
                "last_manual": runs.filter(trigger=ScanRun.Trigger.MANUAL, user=request.user).first(),
            }
        ).data
        return Response(data)
