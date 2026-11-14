from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets

from apps.networking.models import MetEntry

from .serializers import MetEntrySerializer


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("event", str, required=False, description="Event slug."),
            OpenApiParameter("person", str, required=False, description="Cala person id."),
            OpenApiParameter("q", str, required=False, description="Search name, place and note."),
        ]
    )
)
@extend_schema(tags=["networking"])
class MetEntryViewSet(viewsets.ModelViewSet):
    """CRUD on the signed-in user's met list. Entries are private to their owner."""

    serializer_class = MetEntrySerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation, no real user
            return MetEntry.objects.none()
        qs = MetEntry.objects.filter(user=self.request.user).select_related("event")
        if self.action == "list":
            params = self.request.query_params
            if event := params.get("event"):
                qs = qs.filter(event__slug=event)
            if person := params.get("person"):
                qs = qs.filter(cala_person_id=person)
            if q := params.get("q"):
                qs = qs.filter(Q(person_name__icontains=q) | Q(where__icontains=q) | Q(note__icontains=q))
        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
