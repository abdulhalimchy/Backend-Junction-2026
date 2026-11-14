from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from apps.core.cala import CalaError, is_configured
from apps.startups import analysis, enrichment, services
from apps.startups.models import Startup, StartupAnalysis

from .serializers import (
    CalaCandidateSerializer,
    StartupAnalysisSerializer,
    StartupLookupSerializer,
    StartupSearchSerializer,
    StartupSerializer,
)

KIND_PARAM = OpenApiParameter(
    "kind", OpenApiTypes.STR, OpenApiParameter.PATH, enum=list(StartupAnalysis.Kind.values)
)


@extend_schema(tags=["startups"])
class StartupViewSet(viewsets.ModelViewSet):
    """The signed-in founder's startups. Private to their owner.

    Add one by hand with POST, or with POST lookup/ by domain or by a Cala company picked from GET search/
    (a name search): that returns a draft whose `suggestions`
    fill in while `enrichment_status` moves to completed. PATCH the draft with the reviewed values and
    `is_draft: false` to save it, or DELETE it to cancel.

    Competitors and find-investors analyses never run on their own: POST analyses/<kind>/ starts one (or a
    refresh), GET reads the stored result.
    """

    serializer_class = StartupSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation, no real user
            return Startup.objects.none()
        qs = Startup.objects.filter(user=self.request.user)
        return qs.filter(is_draft=False) if self.action == "list" else qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, is_draft=False)

    def perform_destroy(self, instance):
        services.delete_startup(instance)

    def perform_update(self, serializer):
        services.save_startup(serializer.instance, serializer.validated_data)

    @extend_schema(request=StartupLookupSerializer, responses={201: StartupSerializer})
    @action(detail=False, methods=["post"])
    def lookup(self, request):
        """Start a startup from its domain or a picked Cala company. Poll the draft until `enrichment_status`
        is completed."""
        serializer = StartupLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        startup = services.start_lookup(user=request.user, domain=data["domain"], cala_id=data["cala_id"])
        return Response(StartupSerializer(startup).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("q", OpenApiTypes.STR, OpenApiParameter.QUERY, required=True)],
        responses={200: CalaCandidateSerializer(many=True)},
    )
    @action(detail=False, methods=["get"])
    def search(self, request):
        """Companies in Cala named like `q`, to pick from when adding a startup by name."""
        params = StartupSearchSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        if not is_configured():
            return Response({"error": {"message": "Cala is not configured (CALA_API_KEY)."}}, status=503)
        try:
            results = enrichment.search_cala(params.validated_data["q"])
        except CalaError as exc:
            return Response({"error": {"message": f"Cala search failed: {exc}"}}, status=502)
        return Response({"results": CalaCandidateSerializer(results, many=True).data})

    @extend_schema(
        parameters=[KIND_PARAM],
        request=None,
        responses={200: StartupAnalysisSerializer, 202: StartupAnalysisSerializer},
    )
    @action(detail=True, methods=["get", "post"], url_path=r"analyses/(?P<kind>[a-z]+)")
    def analyses(self, request, pk=None, kind=None):
        """GET: the stored analysis ({"status": "none"} if never run). POST: run it now, or refresh it.

        A run happens in the background: poll GET while `status` is "loading". The previous result stays in
        `data` until the new one is ready.
        """
        if kind not in StartupAnalysis.Kind.values:
            raise NotFound("Unknown analysis.")
        startup = self.get_object()
        if request.method == "POST":
            if not is_configured():
                return Response({"error": {"message": "Cala is not configured (CALA_API_KEY)."}}, status=503)
            result = analysis.start(startup, kind)
            return Response(StartupAnalysisSerializer(result).data, status=status.HTTP_202_ACCEPTED)
        result = analysis.get(startup, kind)
        if result is None:
            return Response({"kind": kind, "status": "none", "data": {}, "error": "", "finished_at": None})
        return Response(StartupAnalysisSerializer(result).data)
