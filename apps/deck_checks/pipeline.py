"""Runs a deck check end to end: extract claims, gather Cala evidence, verify, store results."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from pypdf import PdfReader

from apps.core.cala import CalaClient, CalaError, is_configured

from . import llm
from .evidence import Evidence, build_evidence
from .models import DeckCheck, DeckClaim
from .search import host, search_claims

logger = logging.getLogger(__name__)


def page_count(pdf_bytes: bytes) -> int | None:
    try:
        return len(PdfReader(BytesIO(pdf_bytes)).pages)
    except Exception:  # noqa: BLE001 - a broken page tree must not fail the check
        return None


def _fail(deck: DeckCheck, message: str) -> None:
    deck.status = DeckCheck.Status.FAILED
    deck.error = message
    deck.checked_at = timezone.now()
    deck.save(update_fields=["status", "error", "checked_at", "updated_at"])


def run_deck_check(deck_id) -> None:
    """Safe to call from a background thread: never raises, records failures on the row."""
    close_old_connections()
    try:
        deck = DeckCheck.objects.get(pk=deck_id)
    except DeckCheck.DoesNotExist:
        return
    try:
        _run(deck)
    except (llm.LLMError, CalaError) as exc:
        logger.warning("Deck check %s failed: %s", deck_id, exc)
        _fail(deck, str(exc))
    except Exception:  # noqa: BLE001 - last resort so the row never sticks in "processing"
        logger.exception("Deck check %s crashed", deck_id)
        _fail(deck, "Something went wrong while checking this deck.")
    finally:
        close_old_connections()


@dataclass
class CheckResult:
    company_name: str
    rows: list[dict]


# A web verdict needs this many independent sites behind it: one page can be wrong.
MIN_WEB_SITES = 2


def _dedupe_sources(cited: list[Evidence]) -> list[dict]:
    seen, sources = set(), []
    for e in cited:
        for s in e.sources:
            if (s["name"], s["document"]) not in seen:
                seen.add((s["name"], s["document"]))
                sources.append(s)
    return sources


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w%$€£.,]+", " ", text.lower()).split())


def _quoted(quote, cited: list[Evidence]) -> bool:
    q = _norm(str(quote or ""))
    return len(q) >= 20 and any(q in _norm(e.text) for e in cited)


def check_pdf(pdf_bytes: bytes, *, company_name: str = "", company_id: str = "", client=None) -> CheckResult:
    """The whole check, without the database: read claims, verify against Cala's record, search the rest."""
    client = client or CalaClient()
    reading = llm.extract_claims(pdf_bytes)
    claims = reading.claims
    if not claims:
        raise llm.LLMError("No checkable claims were found in this deck. Is it image-only or empty?")

    # The deck is the best evidence of which company it is about; the upload's name is only a fallback.
    company_name = reading.company_name or company_name
    needs_competitors = any(c["category"] == "market" for c in claims)
    on_record_name, evidence = build_evidence(
        client,
        company_id=company_id,
        names=[company_name, *reading.other_names],
        website=reading.website,
        people=reading.people,
        include_competitors=needs_competitors,
    )
    name = on_record_name or company_name
    verdicts = llm.verify_claims(name, claims, evidence) if evidence else []
    by_index = {v.get("index"): v for v in verdicts if isinstance(v, dict)}
    by_id = {e.id: e for e in evidence}

    rows = []
    for i, c in enumerate(claims):
        v = by_index.get(i) or {}
        cited = [by_id[x] for x in v.get("evidence_ids", []) if x in by_id]
        status = v.get("status")
        # A verdict without sourced evidence behind it is not a verdict.
        if status not in ("confirmed", "differs") or not cited:
            status, cited, on_record, basis = DeckClaim.Status.CANT_VERIFY, [], "", ""
        else:
            on_record, basis = str(v.get("on_record", "")).strip(), DeckClaim.Basis.RECORD
        rows.append(
            {
                "slide": c["slide"],
                "claim": c["claim"],
                "category": c["category"],
                "status": str(status),
                "on_record": on_record,
                "sources": _dedupe_sources(cited),
                "basis": str(basis),
            }
        )

    open_claims = {i: r["claim"] for i, r in enumerate(rows) if r["status"] == DeckClaim.Status.CANT_VERIFY}
    if open_claims and settings.DECK_CHECK_WEB_SEARCH:
        found = search_claims(client, name, open_claims)
        web = llm.verify_claims_web(name, claims, found) if found else []
        for v in web:
            i = v.get("index") if isinstance(v, dict) else None
            if i not in found or v.get("status") not in ("confirmed", "differs"):
                continue
            own = {e.id: e for e in found[i]}
            cited = [own[x] for x in v.get("evidence_ids", []) if x in own]
            if len({host(s["document"]) for e in cited for s in e.sources}) < MIN_WEB_SITES:
                continue
            # The verdict must rest on a sentence the sources actually contain, not on the model's inference.
            if not _quoted(v.get("quote"), cited):
                continue
            rows[i].update(
                status=v["status"],
                on_record=str(v.get("on_record", "")).strip(),
                sources=_dedupe_sources(cited),
                basis=str(DeckClaim.Basis.WEB),
            )
    return CheckResult(name, rows)


def _run(deck: DeckCheck) -> None:
    deck.status = DeckCheck.Status.PROCESSING
    deck.error = ""
    deck.save(update_fields=["status", "error", "updated_at"])

    if not is_configured():
        raise CalaError("CALA_API_KEY is not set", 401, "missing_key")

    with deck.file.open("rb") as f:
        pdf_bytes = f.read()
    deck.pages = page_count(pdf_bytes)

    result = check_pdf(pdf_bytes, company_name=deck.company_name, company_id=deck.cala_company_id)
    rows = [DeckClaim(deck_check=deck, **r) for r in result.rows]

    with transaction.atomic():
        deck.claims.all().delete()
        DeckClaim.objects.bulk_create(rows)
        deck.company_name = result.company_name
        deck.status = DeckCheck.Status.COMPLETED
        deck.checked_at = timezone.now()
        deck.save()
