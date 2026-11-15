"""The alert scan: checks watched Cala entities for changes and writes sourced alerts for their watchers.

For each distinct entity on any watch list (plus every founder's own company, every investor's portfolio
companies and the competitors found for them):

1. One Cala lookup collects the facts we track: funding rounds, investments, portfolio, event attendance
   and headcount.
2. The facts are compared with the last snapshot (`EntitySnapshot`):
   - a funding round whose own date is within the last ALERT_BACKFILL_DAYS (rounds are looked up to find
     their date, at most MAX_ROUND_LOOKUPS per entity), or an undated round new since the last scan
   - a portfolio company new since the last scan (investors)
   - attendance at an event space we host (Slush 2026)
   - a changed employee count range
   Old rounds that show up in Cala for the first time are not news and are skipped. Source dates are often
   crawl dates, so only a round's own date decides whether it is recent.
3. Each change becomes one alert per watcher. `dedupe_key` is unique per user, so a rerun, a second scan
   path or re-watching never repeats an alert.
4. The new facts are saved as the snapshot.

Every alert is a fact Cala has on record, with Cala's sources. Nothing here is written by a model.
"""

from __future__ import annotations

import datetime
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import Profile
from apps.core.cala import CalaClient, CalaError, to_sources
from apps.events.models import Event
from apps.portfolio.models import MonitoredCompetitor, PortfolioCompany
from apps.watchlist.models import WatchItem

from .models import Alert, EntitySnapshot, ScanRun

logger = logging.getLogger(__name__)

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
ROUND_TYPES = {"FundingRound", "PrivateCompanyFundingRound"}
ROUND_PROPS = [
    "round_label", "round_type", "transaction_value", "transaction_value_units",
    "announcement_date", "event_date",
]  # fmt: skip
EDGE_LIMIT = 50
# Round details cost one Cala call each; only new rounds are looked up, newest first, up to this many.
MAX_ROUND_LOOKUPS = 5

QUERIES = {
    "company": {
        "properties": ["name", "employee_count_range"],
        "relationships": {
            "outgoing": {
                "PARTICIPATES_IN_FUNDING_ROUND": {"limit": EDGE_LIMIT},
                "PARTICIPATES_IN_CORPORATE_EVENT": {"limit": EDGE_LIMIT},
                "PARTICIPATES_IN_EVENT": {"limit": 20},
            }
        },
    },
    "investor": {
        "properties": ["name"],
        "relationships": {
            "outgoing": {
                "INVESTED_IN": {"limit": EDGE_LIMIT},
                "PARTICIPATES_IN_FUNDING_ROUND": {"limit": EDGE_LIMIT},
                "PARTICIPATES_IN_EVENT": {"limit": 20},
            }
        },
    },
}

REASONS = {
    WatchItem.Tag.COMPETITOR: "One of your competitors",
    WatchItem.Tag.TARGET_INVESTOR: "An investor you're targeting",
    WatchItem.Tag.PARTNER: "A partner you watch",
    WatchItem.Tag.PIPELINE: "In your pipeline",
    WatchItem.Tag.PORTFOLIO: "In your portfolio",
    WatchItem.Tag.PASSED: "A company you passed on",
}
OWN_COMPANY = "Your company"
IN_PORTFOLIO = "In your portfolio"


# -- results ---------------------------------------------------------------------------------------------


@dataclass
class Change:
    key: str  # stable within the entity, e.g. "round:<id>"
    category: str  # round | deal | portfolio | event | headcount
    title: str
    body: str
    href: str
    occurred_on: datetime.date
    sources: list[dict]
    event: Event | None = None
    related_id: str = ""  # the other company in a deal, to explain why an investor's move matters


@dataclass
class ScanResult:
    entities: int = 0
    alerts_created: int = 0
    skipped: list[str] = field(default_factory=list)  # ids that are not Cala entities (demo fixtures)
    errors: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "entities": self.entities,
            "alerts_created": self.alerts_created,
            "skipped": len(self.skipped),
            "errors": self.errors,
        }


# -- entry points ----------------------------------------------------------------------------------------


def targets(user=None) -> list[tuple[str, str]]:
    """(entity_type, cala_id) pairs to scan: watched companies and investors, founders' own companies,
    and investors' portfolio companies with their monitored competitors.

    With `user`, only that user's entities (their alerts still go to everyone watching them).
    """
    items = WatchItem.objects.filter(entity_type__in=QUERIES)
    profiles = Profile.objects.exclude(cala_company_id="")
    portfolio = PortfolioCompany.objects.filter(is_draft=False).exclude(cala_company_id="")
    competitors = MonitoredCompetitor.objects.filter(company__is_draft=False)
    if user is not None:
        items, profiles = items.filter(user=user), profiles.filter(user=user)
        portfolio, competitors = portfolio.filter(user=user), competitors.filter(user=user)
    pairs = set(items.values_list("entity_type", "cala_id"))
    pairs |= {("company", cid) for cid in profiles.values_list("cala_company_id", flat=True)}
    pairs |= {("company", cid) for cid in portfolio.values_list("cala_company_id", flat=True)}
    pairs |= {("company", cid) for cid in competitors.values_list("cala_id", flat=True)}
    return sorted(pairs)


def scan(
    pairs: list[tuple[str, str]],
    *,
    client: CalaClient | None = None,
    today: datetime.date | None = None,
    backfill_days: int | None = None,
) -> ScanResult:
    client = client or CalaClient()
    today = today or timezone.localdate()
    days = settings.ALERT_BACKFILL_DAYS if backfill_days is None else backfill_days
    since = today - datetime.timedelta(days=days)
    events = {e.cala_event_id: e for e in Event.objects.filter(is_active=True).exclude(cala_event_id="")}
    result = ScanResult()

    for entity_type, cala_id in pairs:
        if not UUID.match(cala_id):  # demo fixtures (e.g. "portbotics") are not in Cala
            result.skipped.append(cala_id)
            continue
        try:
            changes = scan_entity(client, entity_type, cala_id, since=since, events=events)
        except CalaError as exc:
            if exc.kind == "missing_key":
                raise  # nothing else will work either
            logger.warning("Alert scan failed for %s %s: %s", entity_type, cala_id, exc)
            result.errors[cala_id] = str(exc)
            continue
        result.entities += 1
        result.alerts_created += fan_out(entity_type, cala_id, changes)
    return result


def run(pairs: list[tuple[str, str]], *, trigger: str, user_id=None, **kwargs) -> tuple[ScanRun, ScanResult]:
    """`scan`, recorded as a ScanRun (for "last checked" in the app and the admin). Re-raises errors."""
    record = ScanRun.objects.create(trigger=trigger, user_id=user_id)
    try:
        result = scan(pairs, **kwargs)
    except Exception as exc:
        record.status, record.errors = ScanRun.Status.FAILED, {"": str(exc)}
        record.finished_at = timezone.now()
        record.save(update_fields=["status", "errors", "finished_at", "updated_at"])
        raise
    record.status = ScanRun.Status.OK
    record.finished_at = timezone.now()
    record.entities, record.alerts_created = result.entities, result.alerts_created
    record.skipped, record.errors = len(result.skipped), result.errors
    record.save()
    return record, result


def scan_new_watch(item: WatchItem) -> None:
    """Check a newly watched entity right away, so its recent rounds show up in Alerts without waiting a
    night. In a background thread unless ALERT_SCAN_ASYNC is off; failures are logged, never raised."""
    if not settings.ALERT_SCAN_ON_WATCH or item.entity_type not in QUERIES or not UUID.match(item.cala_id):
        return
    pair, user_id = (item.entity_type, item.cala_id), item.user_id

    def work():
        from django.db import connection

        try:
            run([pair], trigger=ScanRun.Trigger.WATCH, user_id=user_id)
        except Exception:
            logger.exception("Scan of new watch %s failed", pair)
        finally:
            if settings.ALERT_SCAN_ASYNC:
                connection.close()

    if settings.ALERT_SCAN_ASYNC:
        transaction.on_commit(
            lambda: threading.Thread(target=work, daemon=True, name=f"watch-scan-{item.cala_id}").start()
        )
    else:
        work()


# -- one entity ------------------------------------------------------------------------------------------


def scan_entity(
    client: CalaClient, entity_type: str, cala_id: str, *, since: datetime.date, events: dict[str, Event]
) -> list[Change]:
    entity = client.retrieve_entity(cala_id, QUERIES[entity_type])
    facts = extract_facts(entity_type, entity)
    snapshot = EntitySnapshot.objects.filter(cala_id=cala_id).first()
    prev = snapshot.facts if snapshot else None

    changes = detect_changes(client, entity_type, cala_id, facts, prev, since=since, events=events)

    EntitySnapshot.objects.update_or_create(
        cala_id=cala_id,
        defaults={
            "entity_type": entity_type,
            "name": facts["name"],
            "facts": facts,
            "checked_at": timezone.now(),
        },
    )
    return changes


def extract_facts(entity_type: str, e: dict) -> dict:
    """The tracked facts, in a JSON-friendly shape that is stored as the snapshot."""
    out = (e.get("relationships") or {}).get("outgoing") or {}
    facts: dict = {"name": e.get("name") or "", "rounds": {}, "deals": {}, "portfolio": {}, "events": {}}

    round_edges = out.get("PARTICIPATES_IN_FUNDING_ROUND", []) + [
        r for r in out.get("PARTICIPATES_IN_CORPORATE_EVENT", []) if r.get("entity_type") in ROUND_TYPES
    ]
    for r in round_edges:
        props = r.get("properties") or {}
        role = props.get("role") or "ISSUER"
        entry = {"date": _edge_date(props), "sources": to_sources(props.get("sources"))}
        if entity_type == "company" and role == "ISSUER":
            facts["rounds"].setdefault(r["id"], entry)
        elif entity_type == "investor" and role in ("INVESTOR", "LENDER"):
            facts["deals"].setdefault(r["id"], {**entry, "lead": bool(props.get("is_lead"))})

    if entity_type == "investor":
        for c in out.get("INVESTED_IN", []):
            facts["portfolio"][c["id"]] = {
                "name": c.get("name", ""),
                "sources": to_sources((c.get("properties") or {}).get("sources")),
            }

    for ev in out.get("PARTICIPATES_IN_EVENT", []):
        props = ev.get("properties") or {}
        facts["events"][ev["id"]] = {"name": ev.get("name", ""), "sources": to_sources(props.get("sources"))}

    headcount = (e.get("properties") or {}).get("employee_count_range") or {}
    if (p := headcount) and p.get("value") not in (None, "", []):
        facts["headcount"] = {"value": _range(p["value"]), "sources": to_sources(p.get("sources"))}
    return facts


def detect_changes(
    client: CalaClient,
    entity_type: str,
    cala_id: str,
    facts: dict,
    prev: dict | None,
    *,
    since: datetime.date,
    events: dict[str, Event],
) -> list[Change]:
    name = facts["name"] or "This company"
    page = f"/{entity_type}/{cala_id}"
    prev = prev or {}
    changes: list[Change] = []

    # Rounds (company raised) and deals (investor joined a round). Candidates: dated and recent, or undated
    # and either first seen now or new since the last scan. Most round edges carry no date, so candidates are
    # looked up (newest first, capped) and kept only if the round's own date is recent; an undated round is
    # kept only when it is new since a previous scan.
    key = "rounds" if entity_type == "company" else "deals"
    seen = prev.get(key) if prev else None
    candidates = [
        (rid, r)
        for rid, r in facts[key].items()
        if (_parse(r["date"]) >= since if r["date"] else (seen is None or rid not in seen))
    ]
    candidates.sort(key=lambda x: x[1]["date"] or "9999", reverse=True)
    if len(candidates) > MAX_ROUND_LOOKUPS:
        logger.info("%s: %d candidate rounds, looking up %d", name, len(candidates), MAX_ROUND_LOOKUPS)
    with ThreadPoolExecutor(max_workers=4) as pool:
        details = list(pool.map(lambda x: _round_details(client, x[0]), candidates[:MAX_ROUND_LOOKUPS]))
    deal_companies: set[str] = set()
    for (rid, edge), d in zip(candidates, details, strict=False):
        if d is None:
            continue
        date = d["date"] or edge["date"]
        when = _parse(date) if date else None
        if (when and when < since) or (not when and seen is None):
            continue  # history, or undated on a first scan: not news
        sources = _unique(edge["sources"] + d["sources"])
        if entity_type == "company":
            changes.append(_company_round(rid, name, page, d, when, sources))
        elif d["issuer"]:
            deal_companies.add(d["issuer"]["id"])
            changes.append(_investor_deal(rid, name, edge["lead"], d, when, sources))

    # Portfolio companies new since the last scan (undated, so never backfilled).
    if entity_type == "investor" and prev:
        for cid, c in facts["portfolio"].items():
            if cid in prev.get("portfolio", {}) or cid in deal_companies:
                continue
            changes.append(
                Change(
                    key=f"portfolio:{cid}",
                    category="portfolio",
                    title=f"{name} invested in {c['name']}",
                    body=f"{c['name']} is new in {name}'s portfolio on record.",
                    href=f"/company/{cid}",
                    occurred_on=timezone.localdate(),
                    sources=c["sources"],
                    related_id=cid,
                )
            )

    # Attendance at an event space we host. Current state, so it is always reported (once per user).
    for eid, ev in facts["events"].items():
        if event := events.get(eid):
            first_seen = min((s["date"] for s in ev["sources"] if s.get("date")), default="")
            changes.append(
                Change(
                    key=f"event:{eid}",
                    category="event",
                    title=f"{name} is attending {event.name}",
                    body=f"Listed as attending {event.name} ({event.city}).",
                    href=page,
                    occurred_on=_parse(first_seen) if first_seen else timezone.localdate(),
                    sources=ev["sources"],
                    event=event,
                )
            )

    # Headcount: only a change between two scans counts.
    old, new = (prev.get("headcount") or {}).get("value"), (facts.get("headcount") or {}).get("value")
    if old and new and old != new:
        changes.append(
            Change(
                key=f"headcount:{old}->{new}",
                category="headcount",
                title=f"{name} now lists {new} employees",
                body=f"Was {old} on record at the previous check.",
                href=page,
                occurred_on=timezone.localdate(),
                sources=facts["headcount"]["sources"],
            )
        )
    return changes


def _company_round(rid: str, name: str, page: str, d: dict, when, sources) -> Change:
    title = (
        f"{name} raised {d['amount']} ({d['stage']})"
        if d["amount"]
        else f"{name} has a new {d['stage']} round on record"
    )
    return Change(
        key=f"round:{rid}",
        category="round",
        title=title,
        body=_backers(d),
        href=page,
        occurred_on=when or timezone.localdate(),
        sources=sources,
    )


def _investor_deal(rid: str, name: str, lead: bool, d: dict, when, sources) -> Change:
    company = d["issuer"]["name"]
    verb = "led" if lead or name in d["leads"] else "joined"
    amount = f" ({d['amount']})" if d["amount"] else ""
    return Change(
        key=f"deal:{rid}",
        category="deal",
        title=f"{name} {verb} {company}'s {d['stage']} round{amount}",
        body=_backers(d),
        href=f"/company/{d['issuer']['id']}",
        occurred_on=when or timezone.localdate(),
        sources=sources,
        related_id=d["issuer"]["id"],
    )


def _backers(d: dict) -> str:
    parts = []
    if d["leads"]:
        parts.append(f"Led by {', '.join(d['leads'])}.")
    if d["others"]:
        more = f" and {len(d['others']) - 5} more" if len(d["others"]) > 5 else ""
        parts.append(f"With {', '.join(d['others'][:5])}{more}.")
    return " ".join(parts) or "No investors on record for this round yet."


def _round_details(client: CalaClient, round_id: str) -> dict | None:
    try:
        r = client.retrieve_entity(
            round_id,
            {
                "properties": ["name", *ROUND_PROPS],
                "relationships": {"incoming": {"PARTICIPATES_IN_FUNDING_ROUND": {"limit": 30}}},
            },
        )
    except CalaError as exc:
        logger.warning("Could not load funding round %s: %s", round_id, exc)
        return None
    props = r.get("properties") or {}

    def value(k):
        v = (props.get(k) or {}).get("value")
        return None if v in (None, "", []) else v

    parts = ((r.get("relationships") or {}).get("incoming") or {}).get("PARTICIPATES_IN_FUNDING_ROUND", [])
    issuer = next((p for p in parts if (p.get("properties") or {}).get("role") == "ISSUER"), None)
    backers = [p for p in parts if (p.get("properties") or {}).get("role") in ("INVESTOR", "LENDER")]
    label = value("round_label") or value("round_type")
    amount, units = value("transaction_value"), value("transaction_value_units")
    sources = [s for k in ROUND_PROPS for s in to_sources((props.get(k) or {}).get("sources"))]
    sources += [s for p in backers for s in to_sources((p.get("properties") or {}).get("sources"))]
    date = value("announcement_date") or value("event_date")
    return {
        "stage": _stage(str(label)) if label else "funding",
        "amount": money(amount, units) if isinstance(amount, int | float) and units else "",
        "date": str(date)[:10] if date else "",
        "issuer": {"id": issuer["id"], "name": issuer.get("name", "")} if issuer else None,
        "leads": [p["name"] for p in backers if (p.get("properties") or {}).get("is_lead")],
        "others": [p["name"] for p in backers if not (p.get("properties") or {}).get("is_lead")],
        "sources": _unique(sources),
    }


# -- fan-out ---------------------------------------------------------------------------------------------


def fan_out(entity_type: str, cala_id: str, changes: list[Change]) -> int:
    """One alert per change and watcher. Returns how many alerts were new."""
    if not changes:
        return 0
    watchers = {
        w.user_id: w
        for w in WatchItem.objects.filter(entity_type=entity_type, cala_id=cala_id).select_related("user")
    }
    owners = (
        set(Profile.objects.filter(cala_company_id=cala_id).values_list("user_id", flat=True))
        if entity_type == "company"
        else set()
    )
    # Investors holding the company in their portfolio, or monitoring it as a portfolio company's competitor.
    portfolio: dict = {}
    monitors: dict = {}
    if entity_type == "company":
        for uid, name in PortfolioCompany.objects.filter(cala_company_id=cala_id, is_draft=False).values_list(
            "user_id", "name"
        ):
            portfolio.setdefault(uid, []).append(name)
        for uid, name in MonitoredCompetitor.objects.filter(
            cala_id=cala_id, company__is_draft=False
        ).values_list("user_id", "company__name"):
            monitors.setdefault(uid, []).append(name)
    # What each watcher watches, to explain an investor's deal ("Backs X, one of your competitors").
    related = {c.related_id for c in changes if c.related_id}
    watched_related: dict[tuple, WatchItem] = {
        (w.user_id, w.cala_id): w for w in WatchItem.objects.filter(cala_id__in=related, user_id__in=watchers)
    }

    created = 0
    for user_id in watchers.keys() | owners | portfolio.keys() | monitors.keys():
        own = user_id in owners
        item = watchers.get(user_id)
        for c in changes:
            if own or item:
                kind, reason = _kind_and_reason(c, own, item, watched_related.get((user_id, c.related_id)))
            elif user_id in portfolio:
                kind, reason = _kind_and_reason(c, False, None, None)[0], IN_PORTFOLIO
            elif c.category == "round":  # only a competitor's rounds are news to its rival's investor
                names = sorted(set(monitors[user_id]))
                more = f" +{len(names) - 2}" if len(names) > 2 else ""
                kind, reason = Alert.Kind.PORTFOLIO_COMPETITOR, f"Competes with {', '.join(names[:2])}{more}"
            else:
                continue
            try:
                with transaction.atomic():
                    _, new = Alert.objects.get_or_create(
                        user_id=user_id,
                        dedupe_key=f"{cala_id}:{c.key}",
                        defaults={
                            "entity_type": entity_type,
                            "cala_id": cala_id,
                            "kind": kind,
                            "title": c.title[:255],
                            "body": c.body,
                            "reason": reason[:255],
                            "href": c.href,
                            "event": c.event,
                            "occurred_on": c.occurred_on,
                            "sources": c.sources,
                        },
                    )
            except IntegrityError:  # a concurrent scan created it first
                new = False
            created += new
    return created


def _kind_and_reason(c: Change, own: bool, item: WatchItem | None, related: WatchItem | None):
    reason = OWN_COMPANY if own else REASONS.get(item.tag, "On your watch list") if item else ""
    if c.category == "event":
        return Alert.Kind.SLUSH_ATTENDEE, reason
    if c.category == "headcount":
        return (Alert.Kind.PROFILE if own else Alert.Kind.TEAM_CHANGE), reason
    if c.category in ("deal", "portfolio"):
        if related:  # the investor backed something this user watches: that is the news
            what = REASONS.get(related.tag, "On your watch list")
            reason = f"Backs {related.name or 'a company you watch'}: {what[0].lower()}{what[1:]}"
        return Alert.Kind.INVESTOR_ACTIVITY, reason
    # A company's own round.
    if own:
        return Alert.Kind.PROFILE, reason
    if item and item.tag == WatchItem.Tag.COMPETITOR:
        return Alert.Kind.COMPETITOR_ROUND, reason
    return Alert.Kind.FUNDING_ROUND, reason


# -- helpers ---------------------------------------------------------------------------------------------

SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def money(value: float, units: str) -> str:
    """Compact money with its currency, matching the frontend: €2.4M, NOK 18M."""
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= div:
            n = f"{value / div:.1f}".rstrip("0").rstrip(".") + suffix
            break
    else:
        n = f"{value:,.0f}"
    unit = str(units).upper()
    return f"{SYMBOLS[unit]}{n}" if unit in SYMBOLS else f"{unit} {n}"


def _stage(label: str) -> str:
    return "Pre-seed" if label == "PRE_SEED" else label.replace("_", " ").capitalize()


def _edge_date(props: dict) -> str:
    d = props.get("announcement_date") or props.get("event_date")
    return str(d)[:10] if d else ""


def _parse(value: str) -> datetime.date:
    """YYYY, YYYY-MM or YYYY-MM-DD, at the precision on record (missing parts become the first)."""
    parts = [int(p) for p in value[:10].split("-")] + [1, 1]
    return datetime.date(parts[0], parts[1], parts[2])


def _range(v) -> str:
    if isinstance(v, list) and v:
        return f"{v[0]}–{v[-1]}" if len(v) > 1 and v[0] != v[-1] else str(v[0])
    return str(v)


def _unique(sources: list[dict]) -> list[dict]:
    return list({(s["name"], s["document"]): s for s in sources}.values())
