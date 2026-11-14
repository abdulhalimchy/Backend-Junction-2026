"""Small server-side client for the Cala knowledge graph (https://docs.cala.ai).

Mirrors the frontend's `lib/cala/client.ts`. The API key is sent as `X-API-KEY` and never leaves the server.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from django.conf import settings

RANKS = {"FILING", "PRIMARY", "PRESS", "AGGREGATOR"}
# A knowledge query Cala has not seen before can take ~50s.
TIMEOUT = httpx.Timeout(90.0, connect=10.0)
RETRIES = 3
RETRY_STATUSES = {429, 502, 503, 504}
BACKOFF_SECONDS = 1.5


class CalaError(Exception):
    def __init__(self, message: str, status: int = 0, kind: str = "upstream"):
        super().__init__(message)
        self.status = status
        self.kind = kind  # missing_key | rate_limited | invalid_request | upstream


def is_configured() -> bool:
    return bool(settings.CALA_API_KEY)


_http: httpx.Client | None = None


def _client() -> httpx.Client:
    """
    One shared client per process: connections are reused instead of opened per request (httpx.Client is
    thread-safe). Connects over IPv4: httpx tries addresses one by one, so on networks where IPv6 is broken
    every request first waits out the connect timeout on each of Cala's IPv6 addresses (~30s per call).
    """
    global _http
    if _http is None:
        ipv4 = httpx.HTTPTransport(local_address="0.0.0.0")  # noqa: S104 - outgoing source address, not a listener
        _http = httpx.Client(timeout=TIMEOUT, transport=ipv4)
    return _http


class CalaClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key if api_key is not None else settings.CALA_API_KEY
        self.base_url = (base_url or settings.CALA_API_BASE_URL).rstrip("/")

    def _request(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        if not self.api_key:
            raise CalaError("CALA_API_KEY is not set", 401, "missing_key")
        # Retry transient failures (gateway errors, rate limits, dropped connections) with a short backoff.
        for attempt in range(RETRIES + 1):
            try:
                res = _client().request(
                    method,
                    f"{self.base_url}{path}",
                    headers={"X-API-KEY": self.api_key},
                    json=json,
                    params=params,
                )
            except httpx.TransportError as exc:
                if attempt == RETRIES:
                    raise CalaError(f"Could not reach Cala: {exc}", 0, "upstream") from exc
            else:
                if res.status_code not in RETRY_STATUSES or attempt == RETRIES:
                    break
            time.sleep(BACKOFF_SECONDS * (attempt + 1))
        if res.status_code == 429:
            raise CalaError("Cala rate limit exceeded", 429, "rate_limited")
        if res.status_code in (401, 403):
            raise CalaError("Cala rejected the API key", res.status_code, "missing_key")
        if res.status_code == 422:
            raise CalaError(f"Cala rejected the request: {res.text[:300]}", 422, "invalid_request")
        if res.status_code >= 400:
            raise CalaError(f"Cala returned {res.status_code}: {res.text[:300]}", res.status_code)
        return res.json()

    def search_entities(self, name: str, entity_types: list[str] | None = None, limit: int = 8) -> list[dict]:
        params: list[tuple[str, Any]] = [("name", name[:100]), ("limit", limit)]
        params += [("entity_types", t) for t in entity_types or []]
        return self._request("GET", "/v1/entities", params=params).get("entities", [])

    def retrieve_entity(self, entity_id: str, query: dict | None = None) -> dict:
        return self._request("POST", f"/v1/entities/{entity_id}", json=query or {})

    def knowledge_search(self, text: str) -> dict:
        """Search sourced documents. `context` excerpts carry their page URLs; `content` is a summary."""
        return self._request(
            "POST",
            "/v1/knowledge/search",
            json={"input": text, "explainability": False, "return_entities": False},
        )

    def knowledge_query(self, text: str) -> dict:
        return self._request("POST", "/v1/knowledge/query", json={"input": text, "return_entities": True})


def to_source(s: dict) -> dict:
    """Normalise a Cala source into the `Source` shape shared with the frontend."""
    doc = s.get("document")
    if isinstance(doc, dict):
        doc = doc.get("endpoint", "")
    rank = s.get("source_rank")
    return {
        "name": s.get("name") or "Source",
        "document": doc or "",
        "date": (s.get("date") or "")[:10],
        "date_precision": s.get("date_precision"),
        "source_rank": rank if rank in RANKS else None,
    }


def to_sources(items: list[dict] | None) -> list[dict]:
    return [to_source(s) for s in items or []]
