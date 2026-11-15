from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.cala import CalaError, is_configured
from apps.portfolio import analysis, services
from apps.portfolio.models import PortfolioAnalysis, PortfolioCompany

from .serializers import (
    AnalysesRefreshSerializer,
    AnalysesStatusSerializer,
    DismissSerializer,
    FundSerializer,
    PortfolioAnalysisSerializer,
    PortfolioCompanySerializer,
    PortfolioLookupSerializer,
    SuggestionsSerializer,
)


def cala_failed(exc: CalaError) -> Response:
    code = 503 if exc.kind == "missing_key" else 502
    return Response({"error": {"message": f"Cala lookup failed: {exc}"}}, status=code)


@extend_schema(tags=["portfolio"])
class PortfolioCompanyViewSet(viewsets.ModelViewSet):
    """The signed-in investor's portfolio. Private to its owner, deal terms included.

    Three ways in:
    - Suggestions: link the fund (PUT fund/ with its Cala id) and GET suggestions/ lists the companies Cala
      says it invested in. POST lookup/ with `quick: true` adds one in one click; POST dismiss/ hides one.
    - Lookup: POST lookup/ with a domain or a Cala company (search with GET /startups/search/) returns a
      draft whose `suggestions` fill in; PATCH it with the reviewed values and `is_draft: false` to save.
    - By hand: POST with the values.

    Analyses (funding since you invested, competitors, next-round investors): GET analyses/ reads them all;
    POST analyses/refresh/ runs them in the background, for one company or the whole portfolio.
    """

    serializer_class = PortfolioCompanySerializer
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PortfolioCompany.objects.none()
        qs = PortfolioCompany.objects.filter(user=self.request.user)
        return qs.filter(is_draft=False) if self.action == "list" else qs

    def update(self, request, *args, **kwargs):
        if request.method == "PUT":  # PUT is only for fund/
            return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
        return super().update(request, *args, **kwargs)

    def perform_create(self, serializer):
        company = serializer.save(user=self.request.user, is_draft=False)
        services.first_rounds(company)

    def perform_update(self, serializer):
        services.save_company(serializer.instance, serializer.validated_data)

    def perform_destroy(self, instance):
        services.delete_company(instance)

    @extend_schema(request=PortfolioLookupSerializer, responses={201: PortfolioCompanySerializer})
    @action(detail=False, methods=["post"])
    def lookup(self, request):
        """Start a company from its domain or a Cala company. Poll it until `enrichment_status` is done."""
        serializer = PortfolioLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if (
            data["cala_id"]
            and PortfolioCompany.objects.filter(
                user=request.user, is_draft=False, cala_company_id=data["cala_id"]
            ).exists()
        ):
            return Response(
                {"error": {"message": "This company is already in your portfolio."}},
                status=status.HTTP_409_CONFLICT,
            )
        company = services.start_lookup(user=request.user, **data)
        return Response(PortfolioCompanySerializer(company).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: SuggestionsSerializer})
    @action(detail=False, methods=["get"])
    def suggestions(self, request):
        """Companies Cala links to the user's fund, not yet in the portfolio and not dismissed."""
        try:
            return Response(services.suggestions(request.user))
        except CalaError as exc:
            return cala_failed(exc)

    @extend_schema(request=FundSerializer, responses={200: SuggestionsSerializer})
    @action(detail=False, methods=["put"])
    def fund(self, request):
        """Link the user's fund (a Cala company id), or unlink it with null. Returns the new suggestions."""
        serializer = FundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cala_id = serializer.validated_data["cala_id"]
        services.link_fund(request.user, str(cala_id) if cala_id else "")
        try:
            return Response(services.suggestions(request.user))
        except CalaError as exc:
            return cala_failed(exc)

    @extend_schema(request=DismissSerializer, responses={204: None})
    @action(detail=False, methods=["post"])
    def dismiss(self, request):
        """Hide a suggestion: the company is not in the user's portfolio."""
        serializer = DismissSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.dismiss(request.user, str(data["cala_id"]), data["name"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={200: PortfolioAnalysisSerializer(many=True)})
    @action(detail=False, methods=["get"])
    def analyses(self, request):
        """Every stored analysis for the user's portfolio. To wait for runs, poll analyses/status/ instead:
        this response carries every result and is large."""
        analysis.expire_stale(request.user)
        qs = PortfolioAnalysis.objects.filter(company__user=request.user, company__is_draft=False)
        return Response(PortfolioAnalysisSerializer(qs, many=True).data)

    @extend_schema(responses={200: AnalysesStatusSerializer})
    @action(detail=False, methods=["get"], url_path="analyses/status")
    def analyses_status(self, request):
        """Cheap to poll: how many analyses and profile lookups are still running, and a `version` that
        changes whenever one finishes. Runs stuck past 5 minutes (server restarted) are marked failed."""
        return Response(analysis.status(request.user))

    @extend_schema(request=AnalysesRefreshSerializer, responses={202: PortfolioAnalysisSerializer(many=True)})
    @action(detail=False, methods=["post"], url_path="analyses/refresh")
    def refresh_analyses(self, request):
        """Run (or refresh) analyses: `kinds` (default all) for `company` (default every company). They run
        one after another in the background; a run already in progress is left alone."""
        serializer = AnalysesRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not is_configured():
            return Response({"error": {"message": "Cala is not configured (CALA_API_KEY)."}}, status=503)
        companies = PortfolioCompany.objects.filter(user=request.user, is_draft=False)
        if data["company"]:
            companies = companies.filter(pk=data["company"])
            if not companies.exists():
                return Response({"error": {"message": "Not in your portfolio."}}, status=404)
        started = analysis.start_many([(c, k) for c in companies for k in data["kinds"]])
        return Response(PortfolioAnalysisSerializer(started, many=True).data, status=status.HTTP_202_ACCEPTED)
