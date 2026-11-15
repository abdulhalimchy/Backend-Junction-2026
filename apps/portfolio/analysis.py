"""On-demand Cala analyses for portfolio companies, stored in PortfolioAnalysis.

The competitors and investors analyses are the founders' ones (`apps.startups.analysis`): they read only
the profile fields a portfolio company shares with a startup. `rounds` is lighter: the company's own recent
rounds and money raised, so the page can show what happened since the investor came in.

Nothing runs on its own except `rounds`, which starts once when a company with a Cala id is saved. Every
value comes from Cala with its sources; no LLM is involved.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.core.cala import CalaClient
from apps.startups import analysis as startup_analysis

from .models import MonitoredCompetitor, PortfolioAnalysis, PortfolioCompany

logger = logging.getLogger(__name__)

STALE_RUN = timedelta(minutes=5)  # a "loading" run older than this is assumed dead and may be restarted
Kind = PortfolioAnalysis.Kind
ROUNDS_QUERY = {
    "properties": [
        "name", "funding_stage", "total_funding", "total_funding_units",
        "total_funding_excluding_debt", "total_funding_excluding_debt_units",
    ],
    "relationships": {
        "outgoing": {
            "PARTICIPATES_IN_FUNDING_ROUND": {"limit": 12},
            "PARTICIPATES_IN_CORPORATE_EVENT": {"limit": 15},
        }
    },
}  # fmt: skip


def needs_cala_id(kind: str) -> bool:
    return kind == Kind.ROUNDS


def can_run(company: PortfolioCompany, kind: str) -> bool:
    """Rounds need the company in Cala; the other two can fall back on its industry or description."""
    if company.is_draft:
        return False
    if needs_cala_id(kind):
        return bool(company.cala_company_id)
    return bool(company.cala_company_id or company.industry or company.description)


# -- running ----------------------------------------------------------------------


def mark_loading(company: PortfolioCompany, kind: str) -> PortfolioAnalysis | None:
    """Mark the analysis as loading, or None when a run is already in progress."""
    result, _ = PortfolioAnalysis.objects.get_or_create(company=company, kind=kind)
    running = result.status == PortfolioAnalysis.Status.LOADING and result.started_at
    if running and timezone.now() - result.started_at < STALE_RUN:
        return None
    result.status = PortfolioAnalysis.Status.LOADING
    result.error = ""
    result.started_at = timezone.now()
    result.save(update_fields=["status", "error", "started_at", "updated_at"])
    return result


def start(company: PortfolioCompany, kinds: list[str]) -> list[PortfolioAnalysis]:
    """Start (or refresh) these analyses for one company in the background, one after another."""
    return start_many([(company, k) for k in kinds])


def start_many(jobs: list[tuple[PortfolioCompany, str]]) -> list[PortfolioAnalysis]:
    """Run the jobs one at a time in one background thread, so a whole portfolio never floods Cala."""
    marked = [r for c, k in jobs if can_run(c, k) and (r := mark_loading(c, k))]
    ids = [r.pk for r in marked]
    if not ids:
        return []
    if settings.DECK_CHECK_ASYNC:

        def work():
            try:
                for pk in ids:
                    run(pk)
            finally:
                connection.close()

        transaction.on_commit(
            lambda: threading.Thread(target=work, daemon=True, name="portfolio-analyses").start()
        )
    else:
        for pk in ids:
            run(pk)
    return marked


def run(analysis_id) -> None:
    result = PortfolioAnalysis.objects.select_related("company").filter(pk=analysis_id).first()
    if not result:
        return
    try:
        data = BUILDERS[result.kind](result.company, CalaClient())
    except Exception as exc:  # noqa: BLE001 - shown on the page; the previous result is kept
        logger.warning("Portfolio analysis %s failed", analysis_id, exc_info=True)
        PortfolioAnalysis.objects.filter(pk=analysis_id).update(
            status=PortfolioAnalysis.Status.FAILED, error=str(exc)[:500], updated_at=timezone.now()
        )
        return
    PortfolioAnalysis.objects.filter(pk=analysis_id).update(
        status=PortfolioAnalysis.Status.READY,
        data=data,
        error="",
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    if result.kind == Kind.COMPETITORS:
        sync_competitors(result.company, data)


# -- builders ---------------------------------------------------------------------


def build_rounds(company: PortfolioCompany, client: CalaClient) -> dict:
    """The company's recent rounds (newest first, at most 6) and money raised, with sources."""
    e = client.retrieve_entity(company.cala_company_id, ROUNDS_QUERY)
    rounds = startup_analysis.company_rounds(client, e)
    equity = [r for r in rounds if r["kind"] == "equity"]
    return {
        "rounds": rounds,
        "raised": startup_analysis.raised(e, equity),
        "capped": len(rounds) >= startup_analysis.MAX_ROUNDS_PER_COMPANY,
    }


def build_competitors(company: PortfolioCompany, client: CalaClient) -> dict:
    return startup_analysis.build_competitors(company, client)


def build_investors(company: PortfolioCompany, client: CalaClient) -> dict:
    return startup_analysis.build_investors(company, client)


BUILDERS = {Kind.ROUNDS: build_rounds, Kind.COMPETITORS: build_competitors, Kind.INVESTORS: build_investors}


def sync_competitors(company: PortfolioCompany, data: dict) -> None:
    """Make the monitored competitors match the latest analysis. A competitor that is itself in the
    investor's portfolio is left out: its rounds already reach them as a portfolio company."""
    own = set(
        PortfolioCompany.objects.filter(user=company.user, is_draft=False)
        .exclude(cala_company_id="")
        .values_list("cala_company_id", flat=True)
    )
    found = {
        c["id"]: c.get("name", "") for c in data.get("companies") or [] if c.get("id") and c["id"] not in own
    }
    with transaction.atomic():
        MonitoredCompetitor.objects.filter(company=company).exclude(cala_id__in=found).delete()
        for cala_id, name in found.items():
            MonitoredCompetitor.objects.update_or_create(
                company=company, cala_id=cala_id, defaults={"user": company.user, "name": name}
            )
