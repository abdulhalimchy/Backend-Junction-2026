# ruff: noqa: E501
"""
Runs a market report and keeps it as the user's history (MarketAnalysis).

The typed free text is first turned into a plan by the LLM (planner.py): the market, the place and the four
Cala requests. A run asks Cala those four in parallel and stores each section as soon as it arrives, so the page can
show progress. Then the executive summary is written by the LLM from the numbered Cala facts (summary.py) and
stored too. Opening a report from history reads the stored result: no Cala credit is spent. Refresh re-runs
it and overwrites the stored sections, recording what changed. Stop (cancel) rolls a run back to the result
stored before it.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from django import db
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.core.cala import CalaClient, CalaError

from . import company, planner, report, summary
from .llm import LLMError
from .models import MarketAnalysis

logger = logging.getLogger(__name__)

STALE_RUN = timedelta(minutes=6)  # a "loading" run older than this is assumed dead and may be restarted
SECTIONS = {
    "size": report.market_size,
    "deals": report.market_deals,
    "trends": report.market_trends,
    "players": report.market_players,
}
PRESET_MARKETS = [
    "mobile applications",
    "climate tech",
    "edtech",
    "industrial robotics",
    "fintech",
    "health tech",
]


# -- planning ---------------------------------------------------------------------------


def known_markets(user) -> list[str]:
    names = MarketAnalysis.objects.filter(user=user, kind=MarketAnalysis.Kind.MARKET).values_list(
        "sector", flat=True
    )
    return list(dict.fromkeys([*PRESET_MARKETS, *names]))[-120:]


def plan_text(user, text: str) -> dict:
    """
    {kind, reason, suggestions, plan} for free text typed into the search box, cached for a day. If the LLM is
    unavailable the search still goes ahead, with the text as the market and no place.
    """
    year = timezone.now().year
    key = f"market-plan:v1:{year}:" + hashlib.sha256(report.normalise_sector(text).encode()).hexdigest()[:32]
    if hit := cache.get(key):
        return hit
    try:
        verdict = planner.plan_market(text, known_markets(user), year)
    except LLMError as exc:
        logger.warning("Market planner skipped: %s", exc)
        return {
            "kind": "market",
            "reason": "",
            "suggestions": [],
            "plan": planner.complete_plan({}, text, year),
        }
    verdict.pop("tokens", None)
    cache.set(key, verdict, 24 * 3600)
    return verdict


def legacy_plan(result: MarketAnalysis) -> dict:
    """Plan for analyses stored before free-text search, when the region was one of a fixed list."""
    r = report.REGIONS.get(result.region)
    place = (r or {}).get("label") or result.region or "Worldwide"
    countries = (r or {}).get("countries") or []
    return planner.complete_plan(
        {"market": result.sector, "place": place if countries else "Worldwide", "countries": countries},
        result.sector,
        result.year or timezone.now().year,
    )


# -- running ---------------------------------------------------------------------------


def open_company(user, plan: dict, typed: str = "") -> MarketAnalysis:
    """The stored analysis of this company (and competitor place), started if it never ran or failed."""
    result, created = MarketAnalysis.objects.get_or_create(
        user=user,
        kind=MarketAnalysis.Kind.COMPANY,
        entity_id=plan["entity_id"],
        sector=plan["market"][:120],
        region=plan["place"].lower(),
        defaults={"year": timezone.now().year, "typed": typed[:300], "title": plan["title"], "plan": plan},
    )
    if created or result.status == MarketAnalysis.Status.FAILED or not result.data:
        start(result)
    else:
        result.save(update_fields=["updated_at"])
    return result


def read_company_text(text: str) -> dict:
    """The LLM's reading of company search text, cached for a day; if the LLM is unavailable, the text is the name."""
    key = "company-find:v1:" + hashlib.sha256(report.normalise_sector(text).encode()).hexdigest()[:32]
    if hit := cache.get(key):
        return hit
    try:
        read = company.read_text(text)
    except LLMError as exc:
        logger.warning("Company search reading skipped: %s", exc)
        return {"kind": "company", "reason": "", "suggestions": [], **company.place_fields({}, text)}
    cache.set(key, read, 24 * 3600)
    return read


def open_analysis(user, plan: dict, typed: str = "", refresh: bool = False) -> MarketAnalysis:
    """The stored analysis for this market and place, started if it never ran (or failed), or re-run on refresh."""
    result, created = MarketAnalysis.objects.get_or_create(
        user=user,
        kind=MarketAnalysis.Kind.MARKET,
        entity_id="",
        sector=plan["market"],
        region=plan["place"].lower(),
        defaults={
            "year": timezone.now().year,
            "typed": typed[:300],
            "title": plan["title"],
            "plan": plan,
        },
    )
    if created or refresh or result.status == MarketAnalysis.Status.FAILED or not result.data:
        start(result)
    else:
        result.save(update_fields=["updated_at"])  # opening it moves it to the top of the history
    return result


def start(result: MarketAnalysis) -> MarketAnalysis:
    running = result.status == MarketAnalysis.Status.LOADING and result.started_at
    if running and timezone.now() - result.started_at < STALE_RUN and result.runs:
        return result
    if not result.plan:
        result.plan = legacy_plan(result)
    # What Stop restores: the last finished result (empty for a first run, which Stop then deletes).
    if result.status != MarketAnalysis.Status.LOADING:
        result.backup = {
            "data": result.data,
            "changes": result.changes,
            "status": result.status,
            "error": result.error,
            "finished_at": result.finished_at.isoformat() if result.finished_at else None,
            "year": result.year,
        }
    result.status = MarketAnalysis.Status.LOADING
    result.error = ""
    result.year = timezone.now().year
    result.started_at = timezone.now()
    result.runs += 1
    result.save(
        update_fields=["status", "error", "year", "started_at", "runs", "plan", "backup", "updated_at"]
    )
    if settings.DECK_CHECK_ASYNC:
        # Requests run in a transaction (ATOMIC_REQUESTS): start the run once the row is committed, so the
        # thread sees it and does not wait on the request's write lock.
        run_no = result.runs
        db.transaction.on_commit(
            lambda: threading.Thread(
                target=_run_in_thread, args=(result.pk, run_no), daemon=True, name=f"market-{result.pk}"
            ).start()
        )
    else:
        run(result.pk, result.runs)
        result.refresh_from_db()
    return result


def cancel(result: MarketAnalysis) -> MarketAnalysis | None:
    """
    Stops a running analysis and rolls it back to the last finished result. A first run has nothing to go back
    to, so it is deleted. Returns the restored analysis, or None when it was deleted. The run's thread notices
    on its next write (the row is no longer loading for its run number) and discards what it fetched.
    """
    if result.status != MarketAnalysis.Status.LOADING:
        return result
    b = result.backup or {}
    if not b.get("data"):
        result.delete()
        return None
    finished = b.get("finished_at")
    result.data = b["data"]
    result.changes = b.get("changes") or []
    result.status = b.get("status") or MarketAnalysis.Status.READY
    result.error = b.get("error") or ""
    result.finished_at = datetime.fromisoformat(finished) if finished else None
    result.year = b.get("year") or result.year
    result.backup = {}
    # runs is not decremented: the stopped thread's writes match on (runs, status=loading), so they no longer
    # apply, and the next run gets a new number.
    result.save()
    return result


def _run_in_thread(analysis_id, run_no: int) -> None:
    try:
        run(analysis_id, run_no)
    except Exception:  # noqa: BLE001 - never leave a run stuck in "loading"
        logger.exception("Market analysis %s crashed", analysis_id)
        MarketAnalysis.objects.filter(
            pk=analysis_id, status=MarketAnalysis.Status.LOADING, runs=run_no
        ).update(
            status=MarketAnalysis.Status.FAILED,
            error="The analysis stopped unexpectedly.",
            updated_at=timezone.now(),
        )
    finally:
        db.connection.close()


class RunStoppedError(Exception):
    pass


def run(analysis_id, run_no: int | None = None) -> None:
    result = MarketAnalysis.objects.filter(pk=analysis_id).first()
    if not result:
        return
    run_no = run_no or result.runs
    plan = result.plan or legacy_plan(result)
    is_company = result.kind == MarketAnalysis.Kind.COMPANY
    sections = company.SECTIONS if is_company else SECTIONS
    previous = dict((result.backup or {}).get("data") or result.data or {})
    data = {
        **previous,
        "errors": {},
        "query": {
            "sector": result.sector,
            "region": plan["place"],
            "year": result.year,
            "title": plan["title"],
        },
        "plan": plan,
    }
    client = CalaClient()

    def save(**extra) -> None:
        # Writes only while this run is still the live one: the user may have stopped it (rolled back), started
        # a new run, or cleared their history meanwhile. Then everything fetched is discarded.
        live = MarketAnalysis.objects.filter(
            pk=analysis_id, runs=run_no, status=MarketAnalysis.Status.LOADING
        )
        if not live.update(data=data, updated_at=timezone.now(), **extra):
            raise RunStoppedError

    def ask(name: str) -> dict:
        # Worker threads only call Cala; every database write happens on this run's own thread.
        return {**sections[name](client, plan), "fetched_at": _now_ms()}

    ex = ThreadPoolExecutor(max_workers=len(sections))
    try:
        pending = {ex.submit(ask, name): name for name in sections}
        for done in as_completed(pending):
            name = pending[done]
            try:
                data[name] = done.result()
            except Exception as exc:  # noqa: BLE001 - one failed section is shown as such; the rest still render
                logger.warning("Analysis section %s failed for %s", name, analysis_id, exc_info=True)
                data["errors"][name] = _error(exc)
                data.pop(name, None)
            save()  # each section is stored as soon as it arrives, so the page can show progress

        facts = company.summary_facts(data) if is_company else summary_facts(data)
        if not facts:
            data.pop("summary", None)
            data["errors"]["summary"] = {
                "message": "No verified facts were found, so there is nothing to summarise.",
                "kind": "upstream",
            }
        else:
            write = summary.summarise_company if is_company else summary.summarise
            try:
                data["summary"] = {
                    **write(result.sector, plan["place"], result.year, facts),
                    "fetched_at": _now_ms(),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Market summary failed for %s", analysis_id, exc_info=True)
                data.pop("summary", None)
                data["errors"]["summary"] = _error(exc)

        # A company without its own record has nothing to show; a market fails only if every section did.
        failed = "profile" in data["errors"] if is_company else all(k in data["errors"] for k in sections)
        first = next(iter(data["errors"].values()), {}) if failed else {}
        changes = (company.diff if is_company else diff)(previous, data) if previous.get("query") else []
        save(
            status=MarketAnalysis.Status.FAILED if failed else MarketAnalysis.Status.READY,
            error=(data["errors"].get("profile") or first).get("message", "") if failed else "",
            changes=changes,
            finished_at=timezone.now(),
            backup={},
        )
    except RunStoppedError:
        logger.info("Analysis %s run %s was stopped; its results are discarded", analysis_id, run_no)
    finally:
        # Requests still waiting are dropped; ones already sent finish in the background and are ignored.
        ex.shutdown(wait=False, cancel_futures=True)


def _now_ms() -> int:
    return int(timezone.now().timestamp() * 1000)


def _error(exc: Exception) -> dict:
    if isinstance(exc, CalaError):
        return {"message": str(exc), "kind": exc.kind}
    return {"message": str(exc)[:500], "kind": "llm" if isinstance(exc, LLMError) else "upstream"}


# -- summary facts ------------------------------------------------------------------------


def summary_facts(data: dict) -> list[dict]:
    """Numbered facts for the LLM, from the stored sections (no extra Cala requests)."""
    facts: list[dict] = []
    for prefix, name, limit in (("S", "size", 15), ("D", "deals", 18), ("T", "trends", 15)):
        for i, e in enumerate((data.get(name) or {}).get("evidence", [])[:limit]):
            facts.append({"id": f"{prefix}{i + 1}", "text": e["text"][:1500], "sources": e["sources"]})
    for i, c in enumerate((data.get("players") or {}).get("companies", [])[:10]):
        last = last_equity_round(c)
        text = " ".join(
            t
            for t in (
                f"{c['name']}: {(c.get('description') or {}).get('value') or 'no description on record'}",
                f"Headquarters: {c['hq']['value']}." if c.get("hq") else "",
                f"Last equity round on record: {last['stage']}, {_money(last['amount'])}, {(last['date'] or {}).get('date') or 'date unknown'}."
                if last
                else "No funding rounds on record.",
            )
            if t
        )
        sources = [*((c.get("description") or {}).get("sources") or []), *((last or {}).get("sources") or [])]
        facts.append({"id": f"P{i + 1}", "text": text, "sources": sources})
    return facts


def last_equity_round(c: dict) -> dict | None:
    equity = [r for r in c.get("rounds", []) if r["kind"] == "equity"]
    return max(equity, key=lambda r: (r["date"] or {}).get("date", ""), default=None)


def _money(m: dict | None) -> str:
    if not m:
        return "amount undisclosed"
    n = m["amount"]
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{m['currency']} {n / div:.1f}{unit}"
    return f"{m['currency']} {n:,.0f}"


# -- what a refresh changed ---------------------------------------------------------------


def diff(old: dict, new: dict) -> list[str]:
    out: list[str] = []
    old_players = {c["name"] for c in (old.get("players") or {}).get("companies", [])}
    new_players = {c["name"] for c in (new.get("players") or {}).get("companies", [])}
    if "players" in new:
        if added := sorted(new_players - old_players):
            out.append(f"New leading players: {', '.join(added)}.")
        if removed := sorted(old_players - new_players):
            out.append(f"No longer listed: {', '.join(removed)}.")
    labels = {"size": "Market size", "deals": "Funding and deals", "trends": "Trends"}
    for name, label in labels.items():
        if name not in new:
            continue
        before = len((old.get(name) or {}).get("evidence", []))
        after = len(new[name].get("evidence", []))
        if (old.get(name) or {}).get("summary") != new[name].get("summary"):
            out.append(f"{label}: the sourced answer changed ({before} → {after} facts).")
    old_head = ((old.get("summary") or {}).get("headline") or {}).get("text")
    new_head = ((new.get("summary") or {}).get("headline") or {}).get("text")
    if "summary" in new and old_head != new_head:
        out.append("The executive summary was rewritten.")
    old_sizes = len((old.get("summary") or {}).get("sizes", []))
    new_sizes = len((new.get("summary") or {}).get("sizes", []))
    if "summary" in new and old_sizes != new_sizes:
        out.append(f"Market size figures: {old_sizes} → {new_sizes}.")
    return out or ["Nothing changed since the last run."]
