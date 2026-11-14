"""Per-claim search: for claims Cala's structured record cannot settle, find sourced web excerpts about them.

Only Cala's `context` excerpts are used, each tied to the page it came from. The model-written `content`
summary is never evidence.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from apps.core.cala import CalaClient, CalaError

from .evidence import Evidence

logger = logging.getLogger(__name__)

MAX_EXCERPTS = 5
MAX_EXCERPT_CHARS = 1500
WORKERS = 6


def host(url: str) -> str:
    return re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/")[0].removeprefix("www.")


def _excerpts(res: dict, prefix: str) -> list[Evidence]:
    items, seen = [], set()
    for ctx in res.get("context") or []:
        text = " ".join(str(ctx.get("content") or "").split())
        sources = []
        for o in ctx.get("origins") or []:
            doc, src = o.get("document") or {}, o.get("source") or {}
            url = doc.get("url") or src.get("url") or ""
            if url and url not in seen:
                seen.add(url)
                sources.append(
                    {
                        "name": src.get("name") or host(url) or "Source",
                        "document": url,
                        "date": "",
                        "date_precision": None,
                        "source_rank": None,
                    }
                )
        if text and sources:
            items.append(Evidence(f"{prefix}.{len(items) + 1}", text[:MAX_EXCERPT_CHARS], sources))
        if len(items) >= MAX_EXCERPTS:
            break
    return items


def search_claims(client: CalaClient, company: str, claims: dict[int, str]) -> dict[int, list[Evidence]]:
    """Sourced excerpts per claim index. A failed search leaves that claim out; it never fails the check."""

    def one(item: tuple[int, str]) -> tuple[int, list[Evidence]]:
        i, claim = item
        query = claim if not company or company.lower() in claim.lower() else f"{claim} ({company})"
        try:
            return i, _excerpts(client.knowledge_search(query), f"W{i}")
        except CalaError as exc:
            logger.warning("Claim search failed for %r: %s", claim[:80], exc)
            return i, []

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return {i: ev for i, ev in pool.map(one, claims.items()) if ev}
