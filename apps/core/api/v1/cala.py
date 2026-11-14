"""
Cala gateway for the Next.js frontend: the only way the frontend reaches Cala.

The frontend keeps its own shaping of Cala data, but every request goes out from here, so the Cala API key
lives only in the backend. Only the five read endpoints the app uses are forwarded, with the caller's body
and query unchanged. Cala's failures come back with their status and a `code` the frontend maps to its own
error kinds (missing_key, rate_limited, no_credit, invalid_request, upstream).
"""

from __future__ import annotations

import re

from django.db import transaction
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.cala import CalaClient, CalaError

ENTITY_ID = r"[A-Za-z0-9_.:-]{1,128}"
ROUTES = [
    ("GET", re.compile(r"v1/entities")),
    ("POST", re.compile(r"v1/knowledge/search")),
    ("POST", re.compile(r"v1/knowledge/query")),
    ("POST", re.compile(rf"v1/entities/{ENTITY_ID}")),
    ("GET", re.compile(rf"v1/entities/{ENTITY_ID}/introspection")),
]
STATUS_BY_KIND = {"missing_key": 503, "no_credit": 402, "rate_limited": 429, "invalid_request": 422}


def _allowed(method: str, path: str) -> bool:
    return any(m == method and pattern.fullmatch(path) for m, pattern in ROUTES)


def _error(status: int, code: str, message: str) -> Response:
    return Response(
        {"error": {"status_code": status, "code": code, "message": message, "details": None}}, status=status
    )


# No database work here, and a Cala call can take a minute: never hold a request transaction open for it.
@method_decorator(transaction.non_atomic_requests, name="dispatch")
@extend_schema(tags=["cala"], request=dict, responses={200: dict})
class CalaGatewayView(APIView):
    """Forwards one allowed Cala request, e.g. POST /api/v1/cala/v1/knowledge/search."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "cala"

    def get(self, request, path: str):
        return self._forward(request, "GET", path)

    def post(self, request, path: str):
        return self._forward(request, "POST", path)

    def _forward(self, request, method: str, path: str) -> Response:
        path = path.strip("/")
        if not _allowed(method, path):
            return _error(404, "not_found", f"{method} /{path} is not an allowed Cala request.")
        params = [(k, v) for k in request.query_params for v in request.query_params.getlist(k)]
        try:
            data = CalaClient().request(
                method, f"/{path}", json=request.data if method == "POST" else None, params=params or None
            )
        except CalaError as exc:
            status = STATUS_BY_KIND.get(exc.kind) or (exc.status if 400 <= exc.status < 600 else 502)
            if exc.kind == "upstream" and status < 500:
                status = 502  # Cala's own 4xx (e.g. 404) is still a failed upstream call for the frontend
            return _error(status, exc.kind, str(exc))
        return Response(data)
