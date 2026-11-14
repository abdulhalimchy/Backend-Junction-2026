from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.events.models import EventAttendance, SideEventRSVP
from apps.events.selectors import events_with_stats, side_event_rsvp_summary

from .serializers import (
    EventAttendanceSerializer,
    EventSerializer,
    SideEventRSVPCreateSerializer,
    SideEventRSVPSummarySerializer,
)


@extend_schema(tags=["events"])
class EventViewSet(viewsets.ReadOnlyModelViewSet):
    """Event spaces. Read-only over the API; managed via admin or Cala sync jobs."""

    serializer_class = EventSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return events_with_stats()

    @extend_schema(
        parameters=[
            OpenApiParameter("type", enum=EventAttendance.EntityType.values, required=False),
            OpenApiParameter("q", str, required=False, description="Name contains (case-insensitive)."),
        ],
        responses=EventAttendanceSerializer(many=True),
    )
    @action(detail=True, methods=["get"])
    def attendees(self, request, slug=None):
        event = get_object_or_404(self.get_queryset(), slug=slug)
        qs = event.attendances.all()
        if entity_type := request.query_params.get("type"):
            qs = qs.filter(entity_type=entity_type)
        if q := request.query_params.get("q"):
            qs = qs.filter(name__icontains=q)

        page = self.paginate_queryset(qs)
        serializer = EventAttendanceSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(
        methods=["get"],
        responses=SideEventRSVPSummarySerializer,
        description="Side events you are going to, and how many Warmline users are going to each.",
    )
    @extend_schema(
        methods=["post"],
        request=SideEventRSVPCreateSerializer,
        responses=SideEventRSVPSummarySerializer,
        description="Mark yourself as going. Repeating it is a no-op (200).",
    )
    @action(detail=True, methods=["get", "post"], url_path="rsvps")
    def rsvps(self, request, slug=None):
        event = get_object_or_404(self.get_queryset(), slug=slug)
        code = status.HTTP_200_OK
        if request.method == "POST":
            data = SideEventRSVPCreateSerializer(data=request.data)
            data.is_valid(raise_exception=True)
            _, created = SideEventRSVP.objects.get_or_create(
                user=request.user, event=event, side_event_id=data.validated_data["side_event_id"]
            )
            code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(side_event_rsvp_summary(event, request.user), status=code)

    @extend_schema(responses={204: None}, description="Remove your RSVP. Missing ones are a no-op.")
    @action(detail=True, methods=["delete"], url_path=r"rsvps/(?P<side_event_id>[-a-zA-Z0-9_]+)")
    def rsvp_delete(self, request, slug=None, side_event_id=None):
        event = get_object_or_404(self.get_queryset(), slug=slug)
        SideEventRSVP.objects.filter(user=request.user, event=event, side_event_id=side_event_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
