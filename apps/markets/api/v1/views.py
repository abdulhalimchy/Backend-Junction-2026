from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.cala import CalaClient, CalaError, is_configured
from apps.markets import analysis, company, guard, summary
from apps.markets.llm import LLMError
from apps.markets.models import MarketAnalysis
from apps.markets.planner import complete_plan

from .serializers import (
    FindCompanySerializer,
    GuardRequestSerializer,
    GuardResponseSerializer,
    MarketAnalysisListSerializer,
    MarketAnalysisSerializer,
    OpenAnalysisSerializer,
    OpenCompanySerializer,
    SummaryRequestSerializer,
)


class LLMUnavailable(APIException):
    status_code = 503
    default_code = "llm_unavailable"
    default_detail = "The language model is unavailable."


@extend_schema(tags=["markets"], request=GuardRequestSerializer, responses={200: GuardResponseSerializer})
class MarketGuardView(APIView):
    """Is this text a market, a company or neither? Run before spending Cala credit on a market analysis."""

    def post(self, request):
        s = GuardRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            return Response(guard.check_market_text(s.validated_data["text"], s.validated_data["known"]))
        except LLMError as exc:
            raise LLMUnavailable(str(exc)) from exc


@extend_schema(tags=["markets"], request=SummaryRequestSerializer, responses={200: dict})
class MarketSummaryView(APIView):
    """
    Executive summary written only from the numbered Cala facts sent. Every claim is checked against the facts
    it cites; claims that fail are returned under `dropped` with the reason, not as fact.
    """

    def post(self, request):
        s = SummaryRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        try:
            return Response(summary.summarise(d["market"], d["region"], d["year"], d["facts"]))
        except LLMError as exc:
            raise LLMUnavailable(str(exc)) from exc


def _analyses(request):
    return MarketAnalysis.objects.filter(user=request.user)


def _need_cala():
    if not is_configured():
        raise CalaUnavailable()


class CalaUnavailable(APIException):
    status_code = 503
    default_code = "cala_unavailable"
    default_detail = "Cala is not configured (CALA_API_KEY)."


@extend_schema(tags=["markets"])
class MarketAnalysisListView(APIView):
    """
    GET: the user's market analysis history, most recently opened first.
    POST {text, force}: open the analysis of free text. The LLM reads it first (is it a market? which market
    and place? which Cala requests?); text that is not a market gets suggestions unless force is set.
    A market already in the history is returned as stored, spending no Cala credit; a new one starts in the
    background (poll the detail while status is "loading", POST .../cancel/ to stop it).
    DELETE: clear the whole history.
    """

    @extend_schema(responses={200: MarketAnalysisListSerializer(many=True)})
    def get(self, request):
        return Response(MarketAnalysisListSerializer(_analyses(request), many=True).data)

    @extend_schema(request=OpenAnalysisSerializer, responses={200: dict})
    def post(self, request):
        s = OpenAnalysisSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        text = " ".join(s.validated_data["text"].split())
        verdict = analysis.plan_text(request.user, text)
        plan = verdict.get("plan")
        if verdict["kind"] != "market" or not plan:
            if not s.validated_data["force"]:
                return Response({"verdict": _public(verdict), "analysis": None})
            plan = complete_plan({}, text, timezone.now().year)
        _need_cala()
        result = analysis.open_analysis(request.user, plan, typed=text)
        return Response({"verdict": _public(verdict), "analysis": MarketAnalysisSerializer(result).data})

    def delete(self, request):
        deleted, _ = _analyses(request).delete()
        return Response({"deleted": deleted})


@extend_schema(tags=["markets"])
class MarketAnalysisDetailView(APIView):
    """GET: one stored analysis. DELETE: remove it from the history."""

    @extend_schema(responses={200: MarketAnalysisSerializer})
    def get(self, request, pk):
        return Response(MarketAnalysisSerializer(get_object_or_404(_analyses(request), pk=pk)).data)

    def delete(self, request, pk):
        get_object_or_404(_analyses(request), pk=pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _public(verdict: dict) -> dict:
    """The verdict as the frontend shows it (canonical: the market name the analysis is stored under)."""
    plan = verdict.get("plan") or {}
    return {
        "kind": verdict["kind"],
        "reason": verdict.get("reason", ""),
        "suggestions": verdict.get("suggestions", []),
        "canonical": plan.get("market", ""),
    }


@extend_schema(tags=["markets"], request=None, responses={200: dict})
class MarketAnalysisCancelView(APIView):
    """
    Stop a running analysis. It is rolled back to the last finished result (what was stored before this run);
    a first run has nothing to go back to and is removed from the history. Cala requests already sent finish
    in the background and their answers are discarded.
    """

    def post(self, request, pk):
        result = analysis.cancel(get_object_or_404(_analyses(request), pk=pk))
        return Response(
            {"deleted": result is None, "analysis": MarketAnalysisSerializer(result).data if result else None}
        )


@extend_schema(tags=["markets"], request=None, responses={202: MarketAnalysisSerializer})
class MarketAnalysisRefreshView(APIView):
    """Re-ask Cala and rewrite the summary. The stored sections are replaced as new answers arrive."""

    def post(self, request, pk):
        _need_cala()
        result = analysis.start(get_object_or_404(_analyses(request), pk=pk))
        return Response(MarketAnalysisSerializer(result).data, status=status.HTTP_202_ACCEPTED)


class CalaFailed(APIException):
    status_code = 502
    default_code = "upstream"
    default_detail = "The data source could not answer."


def _cala_failed(exc: CalaError) -> APIException:
    status_by_kind = {"missing_key": 503, "no_credit": 402, "rate_limited": 429, "invalid_request": 422}
    err = CalaFailed(str(exc), code=exc.kind)
    err.status_code = status_by_kind.get(exc.kind, 502)
    return err


@extend_schema(tags=["markets"], request=FindCompanySerializer, responses={200: dict})
class FindCompanyView(APIView):
    """
    Free text to company records. The LLM reads the text first (which company, what it does, any place for
    competitors); text that is not a company gets suggestions and spends no Cala credit unless force is set.
    Otherwise the name goes to Cala's entity search, and the matches are returned for the user to pick one.
    """

    def post(self, request):
        s = FindCompanySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        text = " ".join(s.validated_data["text"].split())
        read = analysis.read_company_text(text)
        verdict = {k: read[k] for k in ("kind", "reason", "suggestions", "name", "hint", "place")}
        if read["kind"] != "company" and not s.validated_data["force"]:
            return Response({"verdict": verdict, "candidates": []})
        _need_cala()
        try:
            candidates = company.find_candidates(CalaClient(), read["name"] or text)
        except CalaError as exc:
            raise _cala_failed(exc) from exc
        return Response({"verdict": verdict, "candidates": candidates})


@extend_schema(tags=["markets"], request=OpenCompanySerializer, responses={200: MarketAnalysisSerializer})
class OpenCompanyView(APIView):
    """
    Open the analysis of a picked company: stored in the history like a market report, started if it never
    ran (poll the detail while status is "loading"; refresh, cancel and delete work the same way).
    """

    def post(self, request):
        s = OpenCompanySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        _need_cala()
        if not d["name"]:
            # Old links carry only the entity id: read the name and description from its record.
            try:
                e = CalaClient().retrieve_entity(d["entity_id"])
            except CalaError as exc:
                raise _cala_failed(exc) from exc
            d["name"] = str(e.get("name") or "")[:200]
            desc = (e.get("properties") or {}).get("description")
            d["description"] = str((desc or {}).get("value") if isinstance(desc, dict) else desc or "")[:500]
        read = analysis.read_company_text(d["text"]) if d["text"] else company.place_fields({}, d["name"])
        plan = company.company_plan(read, d["entity_id"], d["name"], d["description"], timezone.now().year)
        result = analysis.open_company(request.user, plan, typed=d["text"])
        return Response(MarketAnalysisSerializer(result).data)
