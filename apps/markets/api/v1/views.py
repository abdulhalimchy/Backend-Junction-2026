from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.markets import guard, summary
from apps.markets.llm import LLMError

from .serializers import GuardRequestSerializer, GuardResponseSerializer, SummaryRequestSerializer


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
