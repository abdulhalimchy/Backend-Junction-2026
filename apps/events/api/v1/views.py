from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action

from apps.events.models import EventAttendance
from apps.events.selectors import events_with_stats

from .serializers import EventAttendanceSerializer, EventSerializer


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
