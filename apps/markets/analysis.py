# ruff: noqa: E501
"""
Runs a market report and keeps it as the user's history (MarketAnalysis).

A run asks Cala its four questions in parallel and stores each section as soon as it arrives, so the page can
show progress. Then the executive summary is written by the LLM from the numbered Cala facts (summary.py) and
stored too. Opening a report from history reads the stored result: no Cala credit is spent. Refresh re-runs
it and overwrites the stored sections, recording what changed.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from django import db
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.core.cala import CalaClient, CalaError

from . import guard, report, summary
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


# -- guard ---------------------------------------------------------------------------


def known_markets(user) -> list[str]:
    names = MarketAnalysis.objects.filter(user=user).values_list("sector", flat=True)
    return list(dict.fromkeys([*PRESET_MARKETS, *names]))[-120:]


def check_text(user, text: str) -> dict:
    """Guard verdict for typed text, cached for a day. If the LLM is unavailable the search goes ahead."""
    key = "market-guard:v1:" + hashlib.sha256(report.normalise_sector(text).encode()).hexdigest()[:32]
    if hit := cache.get(key):
        return hit
    try:
        verdict = guard.check_market_text(text, known_markets(user))
    except LLMError as exc:
        logger.warning("Market guard skipped: %s", exc)
        return {"kind": "market", "reason": "", "suggestions": [], "canonical": ""}
    verdict.pop("tokens", None)
    cache.set(key, verdict, 24 * 3600)
    return verdict


# -- running ---------------------------------------------------------------------------


def open_analysis(user, sector: str, region: str, typed: str = "", refresh: bool = False) -> MarketAnalysis:
    """The stored analysis for this market, started if it never ran (or failed), or re-run on refresh."""
    year = timezone.now().year
    result, created = MarketAnalysis.objects.get_or_create(
        user=user,
        sector=sector,
        region=region,
        defaults={"year": year, "typed": typed if typed != sector else ""},
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
    result.status = MarketAnalysis.Status.LOADING
    result.error = ""
    result.year = timezone.now().year
    result.started_at = timezone.now()
    result.runs += 1
    result.save(update_fields=["status", "error", "year", "started_at", "runs", "updated_at"])
    if settings.DECK_CHECK_ASYNC:
        # Requests run in a transaction (ATOMIC_REQUESTS): start the run once the row is committed, so the
        # thread sees it and does not wait on the request's write lock.
        db.transaction.on_commit(
            lambda: threading.Thread(
                target=_run_in_thread, args=(result.pk,), daemon=True, name=f"market-{result.pk}"
            ).start()
        )
    else:
        run(result.pk)
        result.refresh_from_db()
    return result


def _run_in_thread(analysis_id) -> None:
    try:
        run(analysis_id)
    except Exception:  # noqa: BLE001 - never leave a run stuck in "loading"
        logger.exception("Market analysis %s crashed", analysis_id)
        MarketAnalysis.objects.filter(pk=analysis_id).update(
            status=MarketAnalysis.Status.FAILED,
            error="The analysis stopped unexpectedly.",
            updated_at=timezone.now(),
        )
    finally:
        db.connection.close()


def run(analysis_id) -> None:
    result = MarketAnalysis.objects.filter(pk=analysis_id).first()
    if not result:
        return
    previous = dict(result.data or {})
    data = {
        **previous,
        "errors": {},
        "query": {"sector": result.sector, "region": result.region, "year": result.year},
    }
    client = CalaClient()

    def save(**extra) -> None:
        # Updates only if the analysis still exists: the user may clear their history mid-run.
        MarketAnalysis.objects.filter(pk=analysis_id).update(data=data, updated_at=timezone.now(), **extra)

    def ask(name: str) -> dict:
        # Worker threads only call Cala; every database write happens on this run's own thread.
        return {**SECTIONS[name](client, result.sector, result.region, result.year), "fetched_at": _now_ms()}

    with ThreadPoolExecutor(max_workers=len(SECTIONS)) as ex:
        pending = {ex.submit(ask, name): name for name in SECTIONS}
        for done in as_completed(pending):
            name = pending[done]
            try:
                data[name] = done.result()
            except Exception as exc:  # noqa: BLE001 - one failed section is shown as such; the rest still render
                logger.warning("Market section %s failed for %s", name, analysis_id, exc_info=True)
                data["errors"][name] = _error(exc)
                data.pop(name, None)
            save()  # each section is stored as soon as it arrives, so the page can show progress

    facts = summary_facts(data)
    if not facts:
        data.pop("summary", None)
        data["errors"]["summary"] = {
            "message": "Cala returned no facts for this market, so there is nothing to summarise.",
            "kind": "upstream",
        }
    else:
        try:
            data["summary"] = {
                **summary.summarise(result.sector, result.region, result.year, facts),
                "fetched_at": _now_ms(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Market summary failed for %s", analysis_id, exc_info=True)
            data.pop("summary", None)
            data["errors"]["summary"] = _error(exc)

    failed = all(k in data["errors"] for k in SECTIONS)
    save(
        status=MarketAnalysis.Status.FAILED if failed else MarketAnalysis.Status.READY,
        error=data["errors"].get("size", {}).get("message", "") if failed else "",
        changes=diff(previous, data) if previous.get("query") else [],
        finished_at=timezone.now(),
    )


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
            out.append(f"{label}: Cala's answer changed ({before} → {after} facts).")
    old_head = ((old.get("summary") or {}).get("headline") or {}).get("text")
    new_head = ((new.get("summary") or {}).get("headline") or {}).get("text")
    if "summary" in new and old_head != new_head:
        out.append("The executive summary was rewritten.")
    old_sizes = len((old.get("summary") or {}).get("sizes", []))
    new_sizes = len((new.get("summary") or {}).get("sizes", []))
    if "summary" in new and old_sizes != new_sizes:
        out.append(f"Market size figures: {old_sizes} → {new_sizes}.")
    return out or ["Nothing changed since the last run."]
