"""Runs a deck check end to end: extract claims, gather Cala evidence, verify, store results."""

from __future__ import annotations

import logging
from io import BytesIO

from django.db import close_old_connections, transaction
from django.utils import timezone
from pypdf import PdfReader

from apps.core.cala import CalaClient, CalaError, is_configured

from . import llm
from .evidence import build_evidence
from .models import DeckCheck, DeckClaim

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


def _run(deck: DeckCheck) -> None:
    deck.status = DeckCheck.Status.PROCESSING
    deck.error = ""
    deck.save(update_fields=["status", "error", "updated_at"])

    if not is_configured():
        raise CalaError("CALA_API_KEY is not set", 401, "missing_key")

    with deck.file.open("rb") as f:
        pdf_bytes = f.read()
    deck.pages = page_count(pdf_bytes)

    reading = llm.extract_claims(pdf_bytes)
    claims = reading.claims
    if not claims:
        raise llm.LLMError("No checkable claims were found in this deck. Is it image-only or empty?")

    # The deck is the best evidence of which company it is about; the upload's name is only a fallback.
    company_name = reading.company_name or deck.company_name
    needs_competitors = any(c["category"] == "market" for c in claims)
    on_record_name, evidence = build_evidence(
        CalaClient(),
        company_id=deck.cala_company_id,
        names=[company_name, *reading.other_names],
        website=reading.website,
        include_competitors=needs_competitors,
    )
    verdicts = llm.verify_claims(on_record_name or company_name, claims, evidence) if evidence else []
    by_index = {v.get("index"): v for v in verdicts if isinstance(v, dict)}
    by_id = {e.id: e for e in evidence}

    rows = []
    for i, c in enumerate(claims):
        v = by_index.get(i) or {}
        cited = [by_id[x] for x in v.get("evidence_ids", []) if x in by_id]
        status = v.get("status")
        # A verdict without sourced evidence behind it is not a verdict.
        if status not in ("confirmed", "differs") or not cited:
            status, cited, on_record = DeckClaim.Status.CANT_VERIFY, [], ""
        else:
            on_record = str(v.get("on_record", "")).strip()
        seen, sources = set(), []
        for e in cited:
            for s in e.sources:
                if (s["name"], s["document"]) not in seen:
                    seen.add((s["name"], s["document"]))
                    sources.append(s)
        rows.append(
            DeckClaim(
                deck_check=deck,
                slide=c["slide"],
                claim=c["claim"],
                category=c["category"],
                status=status,
                on_record=on_record,
                sources=sources,
            )
        )

    with transaction.atomic():
        deck.claims.all().delete()
        DeckClaim.objects.bulk_create(rows)
        deck.company_name = on_record_name or company_name
        deck.status = DeckCheck.Status.COMPLETED
        deck.checked_at = timezone.now()
        deck.save()
