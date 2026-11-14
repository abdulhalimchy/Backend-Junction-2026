import uuid

from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.deck_checks import services
from apps.deck_checks.models import DeckCheck

from .serializers import DeckCheckDetailSerializer, DeckCheckSerializer, DeckCheckUploadSerializer


@extend_schema(tags=["deck-checks"])
class DeckCheckViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Upload a pitch deck (PDF) and read back the verified claims. Private to the uploader."""

    # Uploads are multipart/form-data; the rest of the API stays JSON-only.
    parser_classes = [MultiPartParser, JSONParser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation, no real user
            return DeckCheck.objects.none()
        if self.action in ("list", "retrieve"):
            services.fail_stale_deck_checks(DeckCheck.objects.filter(user=self.request.user))
        qs = (
            DeckCheck.objects.filter(user=self.request.user)
            .filter(**self._startup_filter())
            .annotate(claim_count=Count("claims"))
            .order_by("-created_at")
        )
        if self.action == "retrieve":
            qs = qs.prefetch_related("claims")
        return qs

    def _startup_filter(self) -> dict:
        """?startup=<id> lists one startup's decks; ?startup=none those not tied to a startup (the demo
        company). Without the parameter, every deck is listed (the investor view)."""
        if self.action != "list" or "startup" not in self.request.query_params:
            return {}
        value = self.request.query_params["startup"]
        if value in ("", "none"):
            return {"startup__isnull": True}
        try:
            return {"startup_id": uuid.UUID(value)}
        except ValueError as exc:
            raise ValidationError({"startup": "Not a valid startup id."}) from exc

    def get_serializer_class(self):
        if self.action == "create":
            return DeckCheckUploadSerializer
        if self.action == "retrieve":
            return DeckCheckDetailSerializer
        return DeckCheckSerializer

    @extend_schema(
        request={"multipart/form-data": DeckCheckUploadSerializer}, responses={201: DeckCheckSerializer}
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deck_check = services.create_deck_check(user=request.user, **serializer.validated_data)
        return Response(DeckCheckSerializer(deck_check).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={202: DeckCheckSerializer})
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Run a failed check again."""
        deck_check = services.retry_deck_check(self.get_object())
        return Response(DeckCheckSerializer(deck_check).data, status=status.HTTP_202_ACCEPTED)

    def perform_destroy(self, instance):
        services.delete_deck_check(instance)
