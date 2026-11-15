# ruff: noqa: E501
"""On-demand Cala analyses for a startup's tabs, stored in StartupAnalysis.

Nothing here runs when a startup is added: each analysis starts only when the user asks for it (or refreshes
it) on its tab. Every value comes from Cala entity retrieval with its sources. The only LLM step is planning
the searches for companies like the startup (search_terms); knowledge-query rows only pick which companies
to retrieve.

competitors: the companies Cala records as competitors (matched startups) or whose description matches the
    startup's industry (not matched), each with stage, money raised, last round, lead investor and team, plus
    the startup's own row, so the page can show where it stands.
investors:  investors in those companies, scored for fit with the startup: overlap with its space, stage,
    recent activity, leading rounds, and location. Investors already backing the startup are listed apart.
"""

from __future__ import annotations

import logging
import re
import statistics
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.cala import CalaClient, to_sources
from apps.deck_checks.evidence import _prop, normalise_name

from . import search_terms
from .enrichment import STAGES, _label
from .models import Startup, StartupAnalysis

logger = logging.getLogger(__name__)

MAX_COMPANIES = 10
MAX_INVESTOR_PEERS = 16  # more companies for investors: many peers have no investors on record
MIN_COMPETITORS = 3  # fewer competitors on record than this: search for peers as well
MAX_ROUNDS_PER_COMPANY = 6
MAX_CANDIDATE_INVESTORS = 24
STALE_RUN = timedelta(minutes=5)  # a "loading" run older than this is assumed dead and may be restarted
COMPANY_TYPES = {"Company", "Organization"}
ROUND_TYPES = {"FundingRound", "PrivateCompanyFundingRound"}
ROUND_PROPS = [
    "round_label", "round_type", "instrument", "transaction_value", "transaction_value_units",
    "announcement_date", "event_date",
]  # fmt: skip
COMPANY_PROPS = [
    "name", "description", "website", "headquarters_address", "founding_date", "founding_date__precision",
    "funding_stage", "employee_count_range", "total_funding", "total_funding_units",
    "total_funding_excluding_debt", "total_funding_excluding_debt_units",
]  # fmt: skip
INVESTOR_PROPS = ["name", "description", "website", "headquarters_address", "registered_address"]


# -- running ----------------------------------------------------------------


def get(startup: Startup, kind: str) -> StartupAnalysis | None:
    return StartupAnalysis.objects.filter(startup=startup, kind=kind).first()


def start(startup: Startup, kind: str) -> StartupAnalysis:
    """Start (or refresh) an analysis in the background. A run already in progress is left alone."""
    result, _ = StartupAnalysis.objects.get_or_create(startup=startup, kind=kind)
    running = result.status == StartupAnalysis.Status.LOADING and result.started_at
    if running and timezone.now() - result.started_at < STALE_RUN:
        return result
    result.status = StartupAnalysis.Status.LOADING
    result.error = ""
    result.started_at = timezone.now()
    result.save(update_fields=["status", "error", "started_at", "updated_at"])
    if settings.DECK_CHECK_ASYNC:
        threading.Thread(
            target=run, args=(result.pk,), daemon=True, name=f"analysis-{kind}-{startup.pk}"
        ).start()
    else:
        run(result.pk)
        result.refresh_from_db()
    return result


def run(analysis_id) -> None:
    result = StartupAnalysis.objects.select_related("startup").filter(pk=analysis_id).first()
    if not result:
        return
    try:
        build = build_competitors if result.kind == StartupAnalysis.Kind.COMPETITORS else build_investors
        data = build(result.startup, CalaClient())
    except Exception as exc:  # noqa: BLE001 - shown on the page; the previous result is kept
        logger.warning("Analysis %s failed", analysis_id, exc_info=True)
        StartupAnalysis.objects.filter(pk=analysis_id).update(
            status=StartupAnalysis.Status.FAILED, error=str(exc)[:500], updated_at=timezone.now()
        )
        return
    StartupAnalysis.objects.filter(pk=analysis_id).update(
        status=StartupAnalysis.Status.READY,
        data=data,
        error="",
        finished_at=timezone.now(),
        updated_at=timezone.now(),
    )


# -- finding companies like the startup ---------------------------------------


def find_companies(client: CalaClient, s: Startup, limit: int = MAX_COMPANIES) -> dict:
    """
    {mode, query, searches, ids, unresolved}. mode "competitors": Cala's competitors of the matched company.
    "peers": companies found by the LLM-planned searches (see search_terms), used when the startup has no
    Cala record or fewer than MIN_COMPETITORS competitors on record. "none": nothing to search on.
    """
    if s.cala_company_id:
        query = f"companies.competitors_of={s.name}.return(name, country)"
        found = _resolve_rows(client, s, [_query_rows(client, query)], limit)
        found.pop("per_search")
        if len(found["ids"]) >= MIN_COMPETITORS:
            searches = [{"label": f"Competitors of {s.name}", "query": query, "found": len(found["ids"])}]
            return {"mode": "competitors", "query": query, "searches": searches, **found}

    searches = search_terms.plan_searches(s)
    if not searches:
        return {"mode": "none", "query": "", "searches": [], "ids": [], "unresolved": 0}
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(lambda q: _query_rows(client, q["query"]), searches))
    found = _resolve_rows(client, s, results, limit)
    return {
        "mode": "peers",
        "query": searches[0]["query"],
        "searches": [{**q, "found": n} for q, n in zip(searches, found.pop("per_search"), strict=True)],
        **found,
    }


def similar_peers(s: Startup, mode: str, peers: list[dict]) -> tuple[list[dict], list[str]]:
    """
    In "peers" mode, keeps the companies the LLM judges to build a product like the startup's (searches by
    category also find companies that only share the customer's industry). Returns (kept, names left out).
    Competitors on record are never filtered.
    """
    if mode != "peers":
        return peers, []
    keep = search_terms.similar_companies(
        s,
        [
            {
                "id": e["id"],
                "name": e.get("name", ""),
                "description": (_prop(e, "description") or {}).get("value") or e.get("description"),
            }
            for e in peers
        ],
    )
    if keep is None:
        return peers, []
    return [e for e in peers if e["id"] in keep], [e.get("name", "") for e in peers if e["id"] not in keep]


def _query_rows(client: CalaClient, query: str) -> tuple[list[str], list[dict]]:
    """(row names, linked company entities) for one knowledge_query; a failed search counts as empty."""
    try:
        res = client.knowledge_query(query)
    except Exception:  # noqa: BLE001 - the other searches still count
        logger.warning("knowledge_query failed: %s", query, exc_info=True)
        return [], []
    rows = list(dict.fromkeys(n for n in map(_row_name, res.get("results") or []) if n))
    linked = [e for e in res.get("entities") or [] if e.get("entity_type") in COMPANY_TYPES]
    return rows, linked


def _resolve_rows(
    client: CalaClient, s: Startup, results: list[tuple[list[str], list[dict]]], limit: int
) -> dict:
    """
    Company ids for the rows of several searches, taken in turn from each search (so every angle counts),
    up to `limit`. Rows are matched to the search's linked entities, else to an exact-name entity search.
    """
    me = normalise_name(s.name)
    picked: list[tuple[int, str]] = []
    seen: set[str] = {me}
    for depth in range(max((len(r) for r, _ in results), default=0)):
        for i, (rows, _) in enumerate(results):
            if depth < len(rows) and normalise_name(rows[depth]) not in seen:
                seen.add(normalise_name(rows[depth]))
                picked.append((i, rows[depth]))
    picked = picked[: limit * 2]  # some rows have no record; resolve extra and cut to `limit` below

    def resolve(item: tuple[int, str]) -> str | None:
        i, row = item
        key = normalise_name(row)
        for e in results[i][1]:
            if any(normalise_name(n) == key for n in [e.get("name", ""), *(e.get("mentions") or [])]):
                return e["id"]
        try:
            hits = client.search_entities(row.split("(")[0].strip(), list(COMPANY_TYPES), 5)
        except Exception:  # noqa: BLE001
            return None
        return next((h["id"] for h in hits if normalise_name(h["name"]) == key), None)

    with ThreadPoolExecutor(max_workers=4) as ex:
        found = list(ex.map(resolve, picked))
    ids: list[str] = []
    per_search = [0] * len(results)
    for (i, _), cid in zip(picked, found, strict=True):
        if cid and cid != s.cala_company_id and cid not in ids and len(ids) < limit:
            ids.append(cid)
            per_search[i] += 1
    return {"ids": ids, "unresolved": sum(1 for f in found if not f), "per_search": per_search}


def _row_name(row: dict) -> str:
    for k in ("name", "company", "startup", "company_name", "organization"):
        if isinstance(row.get(k), str) and row[k].strip():
            return row[k].strip()
    return next((v.strip() for v in row.values() if isinstance(v, str) and v.strip()), "")


def _retrieve(client: CalaClient, entity_id: str, query: dict) -> dict | None:
    try:
        return client.retrieve_entity(entity_id, query)
    except Exception:  # noqa: BLE001 - one missing entity should not sink the analysis
        logger.warning("Could not retrieve %s", entity_id, exc_info=True)
        return None


def _parallel(fn, items, workers: int = 6) -> list:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def _sourced(e: dict, key: str, value=None) -> dict | None:
    p = _prop(e, key)
    sources = to_sources(p.get("sources")) if p else []
    if not p or not sources:
        return None
    return {"value": p["value"] if value is None else value, "sources": sources}


def _dedupe(sources: list[dict], limit: int = 12) -> list[dict]:
    return list({(s["name"], s["document"]): s for s in sources}.values())[:limit]


# -- competitors ----------------------------------------------------------------


def build_competitors(s: Startup, client: CalaClient) -> dict:
    found = find_companies(client, s)
    query = {
        "properties": COMPANY_PROPS,
        "relationships": {
            "incoming": {"INVESTED_IN": {"limit": 50}},
            "outgoing": {
                "PARTICIPATES_IN_FUNDING_ROUND": {"limit": 12},
                "PARTICIPATES_IN_CORPORATE_EVENT": {"limit": 15},
            },
        },
    }
    ids = ([s.cala_company_id] if s.cala_company_id else []) + found["ids"]
    entities = _parallel(lambda i: _retrieve(client, i, query), ids)
    own = [entities.pop(0)] if s.cala_company_id else []
    peers, left_out = similar_peers(s, found["mode"], [e for e in entities if e])
    rows = _parallel(lambda e: company_row(client, e) if e else None, own + peers, workers=4)
    you = rows.pop(0) if s.cala_company_id else None
    you = {**(you or own_row(s)), "you": True, "name": s.name}
    companies = [r for r in rows if r]

    # Money raised is only compared within one currency: the most common one among the companies.
    everyone = [you, *companies]
    currencies = Counter(r["raised"]["value"]["currency"] for r in everyone if r.get("raised"))
    currency = currencies.most_common(1)[0][0] if currencies else None
    comparable = sorted(
        (r for r in everyone if r.get("raised") and r["raised"]["value"]["currency"] == currency),
        key=lambda r: -r["raised"]["value"]["amount"],
    )
    amounts = [r["raised"]["value"]["amount"] for r in comparable if not r.get("you")]
    rank = next((i + 1 for i, r in enumerate(comparable) if r.get("you")), None)
    return {
        "mode": found["mode"],
        "query": found["query"],
        "searches": found["searches"],
        "unresolved": found["unresolved"],
        "left_out": left_out,
        "you": you,
        "companies": companies,
        "currency": currency,
        "median_raised": {"amount": statistics.median(amounts), "currency": currency} if amounts else None,
        "rank": rank,
        "compared": len(comparable),
    }


def own_row(s: Startup) -> dict:
    """The startup's own row when it is not in Cala: only what the founder entered, marked as such."""

    def mine(field: str):
        value = getattr(s, field)
        return {"value": value, "sources": (s.sources or {}).get(field, [])} if value else None

    return {
        "id": "",
        "name": s.name,
        "description": mine("description"),
        "headquarters": mine("headquarters"),
        "website": {"value": s.domain, "sources": []} if s.domain else None,
        "founded": mine("founded"),
        "employees": mine("employees"),
        "stage": mine("stage"),
        "raised": None,
        "last_round": None,
        "rounds": 0,
        "investors": [],
        "self_reported": True,
    }


def company_row(client: CalaClient, e: dict) -> dict:
    rounds = company_rounds(client, e)
    equity = [r for r in rounds if r["kind"] == "equity"]
    latest = max(rounds, key=lambda r: r["date"] or "", default=None) if rounds else None
    latest_equity = max(equity, key=lambda r: r["date"] or "", default=None) if equity else None

    stage = None
    if p := _prop(e, "funding_stage"):
        stage = _sourced(e, "funding_stage", STAGES.get(str(p["value"]), _label(str(p["value"]))))
    if not stage and latest_equity:
        stage = {"value": latest_equity["stage"], "sources": latest_equity["sources"]}

    founded = None
    if p := _prop(e, "founding_date"):
        precision = str((_prop(e, "founding_date__precision") or {}).get("value") or "").lower()
        raw = str(p["value"])[:10]
        founded = _sourced(
            e, "founding_date", raw[:4] if precision == "year" else raw[:7] if precision == "month" else raw
        )

    employees = None
    if p := _prop(e, "employee_count_range"):
        v = p["value"]
        rng = (
            f"{v[0]}–{v[-1]}"
            if isinstance(v, list) and len(v) > 1 and v[0] != v[-1]
            else str(v[0] if isinstance(v, list) else v)
        )
        employees = _sourced(e, "employee_count_range", rng)

    return {
        "id": e["id"],
        "name": e.get("name", ""),
        "description": _sourced(e, "description"),
        "headquarters": _sourced(e, "headquarters_address"),
        "website": _sourced(e, "website"),
        "founded": founded,
        "employees": employees,
        "stage": stage,
        "raised": raised(e, equity),
        "last_round": latest,
        "rounds": len(rounds),
        "investors": [
            {"id": i["id"], "name": i["name"]}
            for i in (e.get("relationships") or {}).get("incoming", {}).get("INVESTED_IN", [])
            if to_sources((i.get("properties") or {}).get("sources"))
        ],
        "self_reported": False,
    }


def raised(e: dict, equity: list[dict]) -> dict | None:
    """Equity raised: the sum of equity rounds in one currency, else the company total Cala reports
    (excluding debt when it has that). Never mixes currencies."""
    with_amount = [r for r in equity if r["amount"]]
    if with_amount:
        cur = Counter(r["amount"]["currency"] for r in with_amount).most_common(1)[0][0]
        same = [r for r in with_amount if r["amount"]["currency"] == cur]
        return {
            "value": {"amount": sum(r["amount"]["amount"] for r in same), "currency": cur},
            "basis": f"{len(same)} equity round{'s' if len(same) != 1 else ''}",
            "sources": _dedupe([src for r in same for src in r["sources"]]),
        }
    for key, basis in (
        ("total_funding_excluding_debt", "reported total, excl. debt"),
        ("total_funding", "reported total"),
    ):
        v, u = _prop(e, key), _prop(e, f"{key}_units")
        if v and u and isinstance(v["value"], int | float) and isinstance(u["value"], str):
            return {
                "value": {"amount": v["value"], "currency": u["value"]},
                "basis": basis,
                "sources": to_sources(v.get("sources")),
            }
    return None


def company_rounds(client: CalaClient, e: dict) -> list[dict]:
    """The company's most recent rounds (as issuer), newest first, duplicates (same month and amount) merged."""
    out = (e.get("relationships") or {}).get("outgoing", {})
    edges = [
        *out.get("PARTICIPATES_IN_FUNDING_ROUND", []),
        *[r for r in out.get("PARTICIPATES_IN_CORPORATE_EVENT", []) if r.get("entity_type") in ROUND_TYPES],
    ]
    edges = [r for r in edges if ((r.get("properties") or {}).get("role") or "ISSUER") == "ISSUER"]
    edges.sort(key=lambda r: str((r.get("properties") or {}).get("event_date") or ""), reverse=True)
    query = {
        "properties": ROUND_PROPS,
        "relationships": {"incoming": {"PARTICIPATES_IN_FUNDING_ROUND": {"limit": 30}}},
    }
    fetched = _parallel(
        lambda r: (r, _retrieve(client, r["id"], query)), edges[:MAX_ROUNDS_PER_COMPANY], workers=3
    )
    rounds, seen = [], set()
    for edge, r in fetched:
        if not r:
            continue
        rd = round_row(r, edge.get("name", ""))
        key = (rd["date"] or "")[:7], rd["amount"] and (rd["amount"]["amount"], rd["amount"]["currency"])
        if rd["amount"] and key in seen:
            continue
        seen.add(key)
        rounds.append(rd)
    return rounds


def round_row(r: dict, fallback: str) -> dict:
    label, rtype = _prop(r, "round_label"), _prop(r, "round_type")
    stage = (
        _label(str(label["value"]))
        if label
        else str(rtype["value"])
        if rtype
        else fallback.replace("_", " ").capitalize()
    )
    instrument = str((_prop(r, "instrument") or {}).get("value") or "").upper()
    value, units = _prop(r, "transaction_value"), _prop(r, "transaction_value_units")
    when = _prop(r, "announcement_date") or _prop(r, "event_date")
    parts = (r.get("relationships") or {}).get("incoming", {}).get("PARTICIPATES_IN_FUNDING_ROUND", [])
    leads = [
        {"id": x["id"], "name": x["name"]}
        for x in parts
        if (x.get("properties") or {}).get("is_lead")
        and (x.get("properties") or {}).get("role") in ("INVESTOR", "LENDER")
    ]
    return {
        "stage": stage,
        "kind": classify(instrument, stage),
        "date": str(when["value"])[:10] if when else None,
        "amount": (
            {"amount": value["value"], "currency": units["value"]}
            if value and units and isinstance(value["value"], int | float) and isinstance(units["value"], str)
            else None
        ),
        "leads": leads,
        "sources": _dedupe(
            [s for k in ROUND_PROPS if (p := _prop(r, k)) for s in to_sources(p.get("sources"))]
        ),
    }


def classify(instrument: str, stage: str) -> str:
    """equity | grant | other. Anything unclear is "other" and never counted as raised."""
    if instrument == "EQUITY":
        return "equity"
    if instrument == "GRANT":
        return "grant"
    if instrument:
        return "other"
    if re.search(r"grant", stage, re.I):
        return "grant"
    if re.search(r"debt|loan|facility|note|bond|lease|financing raised", stage, re.I):
        return "other"
    if re.search(r"seed|series|angel|venture|equity|growth", stage, re.I):
        return "equity"
    return "other"


# -- find investors ---------------------------------------------------------------

STAGE_ORDER = ["pre-seed", "seed", "series-a", "series-b", "growth"]
STAGE_LABEL = {
    "pre-seed": "Pre-seed",
    "seed": "Seed",
    "series-a": "Series A",
    "series-b": "Series B",
    "growth": "Growth",
}


def stage_key(text: str) -> str | None:
    t = (text or "").lower().replace("_", " ").replace("-", " ")
    if "pre" in t and "seed" in t or "angel" in t:
        return "pre-seed"
    if "seed" in t:
        return "seed"
    if re.search(r"series\s*a\b", t):
        return "series-a"
    if re.search(r"series\s*b\b", t):
        return "series-b"
    if re.search(r"series\s*[c-h]\b|growth|late|private equity", t):
        return "growth"
    return None


ISO = {
    "FI": "Finland", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "IS": "Iceland", "EE": "Estonia", "LV": "Latvia",
    "LT": "Lithuania", "DE": "Germany", "AT": "Austria", "CH": "Switzerland", "NL": "Netherlands", "BE": "Belgium",
    "LU": "Luxembourg", "GB": "United Kingdom", "UK": "United Kingdom", "IE": "Ireland", "FR": "France", "ES": "Spain",
    "PT": "Portugal", "IT": "Italy", "PL": "Poland", "US": "United States", "USA": "United States", "CA": "Canada",
    "SG": "Singapore", "IL": "Israel", "AU": "Australia", "JP": "Japan", "CN": "China", "IN": "India",
}  # fmt: skip
NORDICS = {"Finland", "Sweden", "Norway", "Denmark", "Iceland"}
EUROPE = NORDICS | {
    "Estonia", "Latvia", "Lithuania", "Germany", "Austria", "Switzerland", "Netherlands", "Belgium", "Luxembourg",
    "United Kingdom", "Ireland", "France", "Spain", "Portugal", "Italy", "Poland",
}  # fmt: skip


def country_of(text: str) -> str | None:
    """'Helsinki, Finland' → Finland; 'Maarintie 6, 02150 ESPOO, FI-02150, FI' → Finland."""
    names = {n.lower(): n for n in ISO.values()}
    for token in reversed([t.strip() for t in re.split(r"[,\n]", text or "") if t.strip()]):
        if token.lower() in names:
            return names[token.lower()]
        code = token.upper().split("-")[0]
        if code in ISO and len(token) <= 9:
            return ISO[code]
    return None


def build_investors(s: Startup, client: CalaClient) -> dict:
    found = find_companies(client, s, MAX_INVESTOR_PEERS)
    query = {
        "properties": ["name", "description"],
        "relationships": {"incoming": {"INVESTED_IN": {"limit": 50}}},
    }
    ids = ([s.cala_company_id] if s.cala_company_id else []) + found["ids"]
    entities = _parallel(lambda i: _retrieve(client, i, query), ids)
    own = entities.pop(0) if s.cala_company_id else None
    peers, left_out = similar_peers(s, found["mode"], [e for e in entities if e])

    def backers(e):
        return [
            (i, to_sources((i.get("properties") or {}).get("sources")))
            for i in ((e or {}).get("relationships") or {}).get("incoming", {}).get("INVESTED_IN", [])
            if to_sources((i.get("properties") or {}).get("sources"))
        ]

    candidates: dict[str, dict] = {}
    for edge, sources in backers(own):
        candidates[edge["id"]] = {
            "id": edge["id"],
            "name": edge["name"],
            "existing": True,
            "backs": [],
            "sources": sources,
        }
    for peer in peers:
        for edge, sources in backers(peer):
            c = candidates.setdefault(
                edge["id"],
                {"id": edge["id"], "name": edge["name"], "existing": False, "backs": [], "sources": []},
            )
            if peer["name"] not in c["backs"]:
                c["backs"].append(peer["name"])
            c["sources"] = _dedupe(c["sources"] + sources)

    existing = [c for c in candidates.values() if c["existing"]][:8]
    ranked = sorted(
        (c for c in candidates.values() if not c["existing"]),
        key=lambda c: (-len(c["backs"]), c["name"].lower()),
    )
    pool = existing + ranked[:MAX_CANDIDATE_INVESTORS]

    inv_query = {
        "properties": INVESTOR_PROPS,
        "relationships": {
            "outgoing": {"INVESTED_IN": {"limit": 100}, "PARTICIPATES_IN_FUNDING_ROUND": {"limit": 100}}
        },
    }
    details = _parallel(lambda c: _retrieve(client, c["id"], inv_query), pool)
    my_stage = stage_key(s.stage)
    my_country = country_of(s.headquarters)
    competitor_mode = found["mode"] == "competitors"
    investors = [
        investor_row(
            c,
            d,
            my_stage=my_stage,
            my_country=my_country,
            competitor_mode=competitor_mode,
            peer_count=len(peers),
        )
        for c, d in zip(pool, details, strict=True)
    ]
    return {
        "mode": found["mode"],
        "query": found["query"],
        "searches": found["searches"],
        "unresolved": found["unresolved"],
        "peers": [{"id": p["id"], "name": p["name"]} for p in peers],
        "left_out": left_out,
        "your_stage": STAGE_LABEL.get(my_stage or "", s.stage or None),
        "your_country": my_country,
        "existing": [i for i in investors if i["existing"]],
        "investors": sorted((i for i in investors if not i["existing"]), key=lambda i: -i["match"]["score"]),
        "candidates_total": len(ranked),
    }


def investor_row(
    c: dict, e: dict | None, *, my_stage, my_country, competitor_mode: bool, peer_count: int
) -> dict:
    e = e or {}
    out = (e.get("relationships") or {}).get("outgoing", {})
    rounds = [
        r
        for r in out.get("PARTICIPATES_IN_FUNDING_ROUND", [])
        if (r.get("properties") or {}).get("role") != "ISSUER"
    ]
    led = sum(1 for r in rounds if (r.get("properties") or {}).get("is_lead") is True)
    stages = Counter(k for r in rounds if (k := stage_key(r.get("name", ""))))
    dates = [str(d)[:10] for r in rounds if (d := (r.get("properties") or {}).get("event_date"))]
    last_deal, basis = (max(dates), "round") if dates else (None, None)
    if not last_deal:
        reported = [
            src["date"][:10]
            for r in out.get("INVESTED_IN", [])
            for src in to_sources((r.get("properties") or {}).get("sources"))
            if src["source_rank"] in ("PRESS", "PRIMARY", "FILING") and src["date"]
        ]
        last_deal, basis = (max(reported), "reported") if reported else (None, None)
    hq = _sourced(e, "headquarters_address") or _sourced(e, "registered_address")
    country = country_of(str(hq["value"])) if hq else None

    conflict = None
    if c["backs"] and not c["existing"]:
        conflict = "competitor" if competitor_mode else "space"
    row = {
        "id": c["id"],
        "name": e.get("name") or c["name"],
        "entity_type": e.get("entity_type", ""),
        "description": _sourced(e, "description"),
        "website": _sourced(e, "website"),
        "headquarters": hq,
        "country": country,
        "existing": c["existing"],
        "backs": c["backs"],
        "conflict": conflict,
        "rounds_total": len(rounds),
        "rounds_led": led,
        "stages": [
            {"stage": STAGE_LABEL[k], "count": n}
            for k, n in sorted(stages.items(), key=lambda kv: STAGE_ORDER.index(kv[0]))
        ],
        "last_deal": {"date": last_deal, "basis": basis} if last_deal else None,
        "portfolio_size": len(out.get("INVESTED_IN", [])),
        "sources": c["sources"],
    }
    row["match"] = match(row, stages, my_stage=my_stage, my_country=my_country, peer_count=peer_count)
    row["why"] = why(row, my_stage)
    return row


def match(row: dict, stages: Counter, *, my_stage, my_country, peer_count: int) -> dict:
    """Fit score out of 100 with its parts, so the page can show how it was reached."""
    parts = []
    n = len(row["backs"])
    pts = {0: 0, 1: 18, 2: 27}.get(n, 35)
    parts.append({"label": "Invests in your space", "points": pts, "max": 35,
                  "detail": f"Backs {n} of the {peer_count} companies like yours" if n else "No investment in companies like yours on record"})  # fmt: skip

    total = sum(stages.values())
    if not my_stage:
        pts, detail = 12, "Set your stage to score this"
    elif not total:
        pts, detail = 8, "No round history on record"
    else:
        i = STAGE_ORDER.index(my_stage)
        near = sum(stages.get(STAGE_ORDER[j], 0) for j in (i - 1, i + 1) if 0 <= j < len(STAGE_ORDER))
        same = stages.get(my_stage, 0)
        # Full points when half their rounds are at your stage; an adjacent stage counts for much less.
        pts = round(25 * min(1.0, 2 * same / total + 0.6 * near / total))
        detail = f"{same} of {total} rounds at {STAGE_LABEL[my_stage]}" + (
            f", {near} at an adjacent stage" if near else ""
        )
    parts.append({"label": "Invests at your stage", "points": pts, "max": 25, "detail": detail})

    last = row["last_deal"]["date"] if row["last_deal"] else None
    if not last:
        pts, detail = 5, "No dated deal on record"
    else:
        days = (date.today() - _to_date(last)).days
        pts = 20 if days <= 365 else 10 if days <= 730 else 3
        detail = f"Last deal {last[:7]}" + (
            " (date reported)" if row["last_deal"]["basis"] == "reported" else ""
        )
    parts.append({"label": "Recently active", "points": pts, "max": 20, "detail": detail})

    led = row["rounds_led"]
    parts.append({"label": "Leads rounds", "points": min(10, 5 + led) if led else 0, "max": 10,
                  "detail": f"Led {led} round{'s' if led != 1 else ''}" if led else "No led round on record"})  # fmt: skip

    c = row["country"]
    if not c or not my_country:
        pts, detail = 3, "Location not comparable"
    elif c == my_country:
        pts, detail = 10, f"Based in {c}, like you"
    elif {c, my_country} <= NORDICS or {c, my_country} <= EUROPE:
        pts, detail = 5, f"Based in {c}, same region"
    else:
        pts, detail = 0, f"Based in {c}"
    parts.append({"label": "Location", "points": pts, "max": 10, "detail": detail})

    if row["conflict"] == "competitor":
        parts.append(
            {"label": "Backs a competitor", "points": -10, "max": 0, "detail": ", ".join(row["backs"][:3])}
        )
    score = max(0, min(100, sum(p["points"] for p in parts)))
    return {"score": score, "parts": parts}


def _to_date(value: str) -> date:
    """'2025', '2025-03' or '2025-03-12' → a date (first day of the period when less precise)."""
    padded = {4: "-01-01", 7: "-01"}.get(len(value[:10]), "")
    try:
        return date.fromisoformat(value[:10] + padded)
    except ValueError:
        return date.fromisoformat(f"{value[:4]}-01-01")


def why(row: dict, my_stage) -> str:
    if row["existing"]:
        return "Already invested in you"
    bits = []
    if row["backs"]:
        names = ", ".join(row["backs"][:2]) + (f" +{len(row['backs']) - 2}" if len(row["backs"]) > 2 else "")
        bits.append(f"Invested in {names}")
    at_stage = next(
        (s["count"] for s in row["stages"] if my_stage and s["stage"] == STAGE_LABEL.get(my_stage)), 0
    )
    if at_stage:
        bits.append(f"{at_stage} {STAGE_LABEL[my_stage]} round{'s' if at_stage != 1 else ''} on record")
    elif row["rounds_led"]:
        bits.append(f"led {row['rounds_led']} round{'s' if row['rounds_led'] != 1 else ''}")
    return "; ".join(bits) or "Investor in your space"
