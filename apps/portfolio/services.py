"""Write-side logic for an investor's portfolio.

The profile lookup is the startups one (`apps.startups.enrichment.enrich`): Cala first, then the company's
website read by the LLM, nothing guessed. Every saved company with a Cala id is also put on the user's
watch list, tagged portfolio, so the nightly alert scan reports its new rounds.
"""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Profile
from apps.core.cala import CalaClient, CalaError, is_configured, to_sources
from apps.startups import enrichment
from apps.startups.models import PROFILE_FIELDS
from apps.startups.services import save_startup as apply_profile_edits
from apps.watchlist.models import WatchItem

from .models import DismissedSuggestion, PortfolioCompany

logger = logging.getLogger(__name__)

PORTFOLIO_LIMIT = 100
SOURCE_RANKS_DATED = {"PRESS", "PRIMARY", "FILING"}


# -- lookup -------------------------------------------------------------------


@transaction.atomic
def start_lookup(*, user, domain: str = "", cala_id: str = "", name: str = "", quick: bool = False):
    """A company looked up in the background from `domain` or the Cala company `cala_id`.

    By default it is a draft: the lookup fills `suggestions` for the user to review, then they save it.
    With `quick` (adding a Cala suggestion in one click) it is saved straight away, and the lookup fills
    in whatever is still empty, keeping each value's sources.
    """
    company = PortfolioCompany.objects.create(
        user=user,
        name=name,
        domain=domain,
        cala_company_id=cala_id,
        origin=PortfolioCompany.Origin.CALA if quick else PortfolioCompany.Origin.MANUAL,
        is_draft=not quick,
        enrichment_status=PortfolioCompany.Enrichment.PENDING,
    )
    if quick:
        sync_watch(company)
    transaction.on_commit(lambda: enqueue(company.pk))
    return company


def enqueue(company_id) -> None:
    """Run the lookup off the request, like startups. A thread is enough for one process."""
    if settings.DECK_CHECK_ASYNC:
        threading.Thread(
            target=run_enrichment, args=(company_id,), daemon=True, name=f"portfolio-{company_id}"
        ).start()
    else:
        run_enrichment(company_id)


def run_enrichment(company_id) -> None:
    company = PortfolioCompany.objects.filter(pk=company_id).first()
    if not company:
        return
    PortfolioCompany.objects.filter(pk=company.pk).update(
        enrichment_status=PortfolioCompany.Enrichment.PROCESSING
    )
    try:
        suggestions, cala_id, note, domain = enrichment.enrich(company.domain, company.cala_company_id)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user; the company stays editable
        logger.exception("Portfolio lookup failed for %s", company.domain or company.cala_company_id)
        PortfolioCompany.objects.filter(pk=company.pk).update(
            enrichment_status=PortfolioCompany.Enrichment.FAILED,
            enrichment_error=f"The lookup failed: {exc}"[:500],
            enriched_at=timezone.now(),
        )
        return

    company.refresh_from_db()
    company.enrichment_status = PortfolioCompany.Enrichment.COMPLETED
    company.enrichment_error = ""
    company.enrichment_note = note
    company.suggestions = suggestions
    company.enriched_at = timezone.now()
    if company.is_draft or not company.cala_company_id:
        company.cala_company_id = cala_id
    if not company.domain:
        company.domain = domain
    if not company.is_draft:
        # Saved already (a one-click add): fill in only what is still empty, with its sources.
        sources = dict(company.sources or {})
        for field in PROFILE_FIELDS:
            s = suggestions.get(field)
            if s and s.get("value") and not getattr(company, field):
                setattr(company, field, s["value"])
                if s.get("sources"):
                    sources[field] = s["sources"]
        company.sources = sources
    company.save()


# -- save / delete ------------------------------------------------------------


def save_company(company: PortfolioCompany, data: dict) -> PortfolioCompany:
    """Apply edits. Profile values kept exactly as suggested keep their sources; edited ones lose them."""
    apply_profile_edits(company, data)
    if not company.is_draft:
        sync_watch(company)
    return company


@transaction.atomic
def delete_company(company: PortfolioCompany) -> None:
    """Delete the company and its portfolio watch (a watch the user tagged otherwise is left alone)."""
    if company.cala_company_id and not company.is_draft:
        WatchItem.objects.filter(
            user=company.user,
            entity_type=WatchItem.EntityType.COMPANY,
            cala_id=company.cala_company_id,
            tag=WatchItem.Tag.PORTFOLIO,
        ).delete()
    company.delete()


def sync_watch(company: PortfolioCompany) -> None:
    """Watch the company, tagged portfolio, so new rounds show up in Alerts."""
    if not company.cala_company_id:
        return
    item, created = WatchItem.objects.get_or_create(
        user=company.user,
        entity_type=WatchItem.EntityType.COMPANY,
        cala_id=company.cala_company_id,
        defaults={"name": company.name, "tag": WatchItem.Tag.PORTFOLIO},
    )
    if not created and (item.tag != WatchItem.Tag.PORTFOLIO or (company.name and item.name != company.name)):
        item.tag = WatchItem.Tag.PORTFOLIO
        item.name = company.name or item.name
        item.save(update_fields=["tag", "name", "updated_at"])


# -- fund and suggestions -----------------------------------------------------


def profile_of(user) -> Profile:
    return Profile.objects.get_or_create(user=user)[0]


def link_fund(user, cala_id: str) -> None:
    profile = profile_of(user)
    profile.cala_investor_id = cala_id
    profile.save(update_fields=["cala_investor_id", "updated_at"])


def suggestions(user, client: CalaClient | None = None) -> dict:
    """The fund linked to the user's profile and the companies Cala says it invested in, minus the ones
    already in the portfolio or dismissed. {"fund": None, "results": []} when no fund is linked."""
    fund_id = profile_of(user).cala_investor_id
    if not fund_id:
        return {"fund": None, "results": []}
    if not is_configured():
        raise CalaError("CALA_API_KEY is not set", 401, "missing_key")
    client = client or CalaClient()
    e = client.retrieve_entity(
        fund_id,
        {"properties": ["name"], "relationships": {"outgoing": {"INVESTED_IN": {"limit": PORTFOLIO_LIMIT}}}},
    )
    edges = ((e.get("relationships") or {}).get("outgoing") or {}).get("INVESTED_IN") or []
    taken = set(
        PortfolioCompany.objects.filter(user=user, is_draft=False)
        .exclude(cala_company_id="")
        .values_list("cala_company_id", flat=True)
    ) | set(DismissedSuggestion.objects.filter(user=user).values_list("cala_company_id", flat=True))

    results = []
    for r in edges:
        if not r.get("id") or r["id"] in taken:
            continue
        sources = to_sources((r.get("properties") or {}).get("sources"))
        dated = sorted(s["date"] for s in sources if s["source_rank"] in SOURCE_RANKS_DATED and s["date"])
        results.append(
            {
                "id": r["id"],
                "name": r.get("name") or "",
                "first_reported": dated[0] if dated else None,
                "sources": sources,
            }
        )
    results.sort(key=lambda x: x["first_reported"] or "", reverse=True)
    return {
        "fund": {"id": fund_id, "name": e.get("name") or ""},
        "results": results,
        "capped": len(edges) >= PORTFOLIO_LIMIT,
    }


def dismiss(user, cala_id: str, name: str = "") -> None:
    DismissedSuggestion.objects.get_or_create(user=user, cala_company_id=cala_id, defaults={"name": name})
