# ruff: noqa: E501
"""
Cala side of a market report: four questions (size, trends, funding and deals, leading players).

Text sections come from knowledge_search together with the facts (KnowBits) behind them. Leading players come
from knowledge_query, then each company is loaded from entity retrieval with its sources: query rows are only
used to pick which entities to load, never shown as facts. Ported from the frontend's lib/cala/report.ts and
lib/cala/market.ts so the shapes match what the report page renders.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from apps.core.cala import CalaClient, to_sources
from apps.deck_checks.evidence import _prop, normalise_name

logger = logging.getLogger(__name__)

EUROPE = [
    "Finland", "Sweden", "Norway", "Denmark", "Iceland", "Estonia", "Latvia", "Lithuania", "Germany", "Austria", "Switzerland",
    "Netherlands", "Belgium", "Luxembourg", "United Kingdom", "Ireland", "France", "Spain", "Portugal", "Italy", "Poland",
    "Czechia", "Slovakia", "Hungary", "Romania", "Bulgaria", "Greece", "Croatia", "Slovenia",
]  # fmt: skip

REGIONS: dict[str, dict] = {
    "anywhere": {"label": "Anywhere", "countries": []},
    "europe": {"label": "Europe", "countries": EUROPE},
    "nordics": {"label": "Nordics", "countries": ["Finland", "Sweden", "Norway", "Denmark", "Iceland"]},
    "finland": {"label": "Finland", "countries": ["Finland"]},
    "baltics": {"label": "Baltics", "countries": ["Estonia", "Latvia", "Lithuania"]},
    "dach": {"label": "DACH", "countries": ["Germany", "Austria", "Switzerland"]},
    "benelux": {"label": "Benelux", "countries": ["Netherlands", "Belgium", "Luxembourg"]},
    "uki": {"label": "UK & Ireland", "countries": ["United Kingdom", "Ireland"]},
    "us": {"label": "United States", "countries": ["United States"]},
}

ISO = {
    "FI": "Finland", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "IS": "Iceland", "EE": "Estonia", "LV": "Latvia",
    "LT": "Lithuania", "DE": "Germany", "AT": "Austria", "CH": "Switzerland", "NL": "Netherlands", "BE": "Belgium",
    "LU": "Luxembourg", "GB": "United Kingdom", "UK": "United Kingdom", "IE": "Ireland", "FR": "France", "ES": "Spain",
    "PT": "Portugal", "IT": "Italy", "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia", "HU": "Hungary", "RO": "Romania",
    "BG": "Bulgaria", "GR": "Greece", "HR": "Croatia", "SI": "Slovenia", "US": "United States", "USA": "United States",
    "CA": "Canada", "JP": "Japan", "CN": "China", "IN": "India", "KR": "South Korea", "SG": "Singapore", "IL": "Israel",
    "AU": "Australia", "BR": "Brazil",
}  # fmt: skip

COUNTRY_TYPES = {"Country", "DependentTerritory"}
COMPANY_TYPES = {"Company", "Organization"}
ROUND_TYPES = {"FundingRound", "PrivateCompanyFundingRound"}
FINANCING_EVENT = "FINANCING_RAISED_OR_FACILITY_ENTERED"
FILING_LABEL = "Financing (filing)"

MAX_COMPANIES = 12
MAX_FINANCING_EVENTS = 10
MAX_INVESTORS_ENRICHED = 25
CONCURRENCY = 4

COMPANY_RELATIONSHIPS = {
    "outgoing": {
        "PARTICIPATES_IN_FUNDING_ROUND": {"limit": 12},
        "PARTICIPATES_IN_CORPORATE_EVENT": {"limit": 50},
        "HAS_HEADQUARTERS_IN": {"limit": 3},
        "IS_REGISTERED_IN": {"limit": 3},
        "OPERATES_IN_INDUSTRY": {"limit": 5},
    },
    "incoming": {"INVESTED_IN": {"limit": 25}},
}
COMPANY_PROPS = [
    "name", "description", "founding_date", "founding_date__precision", "headquarters_address",
    "legal_name", "website", "employee_count_range", "operational_status", "company_ownership_status", "funding_stage",
    "total_funding", "total_funding_units", "total_funding_excluding_debt", "total_funding_excluding_debt_units",
]  # fmt: skip
ROUND_PROPS = [
    "round_label", "round_type", "instrument", "transaction_value", "transaction_value_units",
    "announcement_date", "announcement_date__precision", "event_date", "event_date__precision",
]  # fmt: skip


def region_of(key: str) -> dict:
    return REGIONS.get(key) or REGIONS["nordics"]


def normalise_sector(text: str) -> str:
    return " ".join(text.split()).lower()


def _map(fn, items, workers: int = CONCURRENCY) -> list:
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


# -- text sections --------------------------------------------------------------


def _evidence(k: dict) -> dict:
    return {
        "id": k.get("id", ""),
        "text": k.get("content", ""),
        # KnowBit origins carry no date or rank: unranked sources named after their document.
        "sources": [
            {
                "name": (o.get("document") or {}).get("name")
                or (o.get("source") or {}).get("name")
                or "Source",
                "document": (o.get("document") or {}).get("url") or (o.get("source") or {}).get("url") or "",
                "date": "",
                "date_precision": None,
                "source_rank": None,
            }
            for o in k.get("origins") or []
        ],
    }


def _ask(client: CalaClient, question: str) -> dict:
    res = client.knowledge_search(question)
    return {
        "question": question,
        "summary": res.get("content") or "",
        "evidence": [_evidence(k) for k in res.get("context") or []],
    }


def market_size(client: CalaClient, plan: dict) -> dict:
    return _ask(client, plan["size_question"])


def market_trends(client: CalaClient, plan: dict) -> dict:
    return _ask(client, plan["trends_question"])


def market_deals(client: CalaClient, plan: dict) -> dict:
    return _ask(client, plan["deals_question"])


# -- leading players --------------------------------------------------------------


def market_players(client: CalaClient, plan: dict) -> dict:
    """
    Leading companies from Cala's structured filter (knowledge_query, dot notation, ordered by funding). If that
    finds no company records, the broader natural-language definition is tried once.
    """
    from .planner import fallback_players_definition

    definition = plan["players_query"]
    res = client.knowledge_query(definition)
    companies, unresolved = load_candidates(client, res, plan["countries"])
    if not companies:
        definition = fallback_players_definition(plan)
        companies, unresolved = load_candidates(client, client.knowledge_query(definition), plan["countries"])
    return {"definition": definition, "companies": companies, "unresolved": unresolved}


def _row_name(row: dict) -> str:
    for k in ("name", "company", "startup", "company_name", "organization"):
        if isinstance(row.get(k), str) and row[k].strip():
            return row[k].strip()
    return next((v.strip() for v in row.values() if isinstance(v, str) and v.strip()), "")


def load_candidates(client: CalaClient, res: dict, countries: list[str]) -> tuple[list[dict], int]:
    rows = list(dict.fromkeys(n for n in map(_row_name, res.get("results") or []) if n))
    linked = [e for e in res.get("entities") or [] if e.get("entity_type") in COMPANY_TYPES]

    def resolve(row: str) -> str | None:
        # Entities Cala already linked to a row; otherwise an exact-name match from entity search only.
        key = normalise_name(row)
        for e in linked:
            if any(normalise_name(n) == key for n in [e.get("name", ""), *(e.get("mentions") or [])]):
                return e["id"]
        try:
            hits = client.search_entities(re.sub(r"\([^)]*\)", "", row).strip()[:100], list(COMPANY_TYPES), 5)
        except Exception:  # noqa: BLE001 - an unresolved row is counted, not fatal
            return None
        return next((h["id"] for h in hits if normalise_name(h.get("name", "")) == key), None)

    resolved = _map(resolve, rows[:MAX_COMPANIES])
    ids = list(dict.fromkeys(i for i in resolved if i))
    unresolved = sum(1 for i in resolved if not i)

    def fetch(entity_id: str):
        try:
            e = client.retrieve_entity(
                entity_id, {"properties": COMPANY_PROPS, "relationships": COMPANY_RELATIONSHIPS}
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not retrieve %s", entity_id, exc_info=True)
            return None
        return to_market_company(client, e), company_countries(e)

    fetched = [f for f in _map(fetch, ids) if f]
    enrich_investors(client, [c for c, _ in fetched])

    # Cala's query does not strictly enforce location: drop companies on record outside the region.
    allowed = set(countries)
    companies = [
        c for c, countries in fetched if not allowed or not countries or any(x in allowed for x in countries)
    ]
    return companies, unresolved


def enrich_investors(client: CalaClient, companies: list[dict]) -> None:
    """Adds investor aliases and website so the page can merge records of the same firm."""
    ids = list(dict.fromkeys(i["id"] for c in companies for i in c["investors"]))[:MAX_INVESTORS_ENRICHED]

    def load(entity_id: str):
        try:
            e = client.retrieve_entity(entity_id, {"properties": ["aliases", "website"]})
        except Exception:  # noqa: BLE001 - name matching still applies
            return entity_id, None
        aliases = (_prop(e, "aliases") or {}).get("value")
        website = (_prop(e, "website") or {}).get("value")
        return entity_id, {
            "aliases": [a for a in aliases if isinstance(a, str)] if isinstance(aliases, list) else [],
            "website": website if isinstance(website, str) else None,
        }

    info = dict(_map(load, ids))
    for c in companies:
        for i in c["investors"]:
            if info.get(i["id"]):
                i.update(info[i["id"]])


def company_countries(e: dict) -> list[str]:
    out = (e.get("relationships") or {}).get("outgoing", {})

    def names(key: str) -> list[str]:
        return [r["name"] for r in out.get(key, []) if r.get("entity_type") in COUNTRY_TYPES]

    hq = names("HAS_HEADQUARTERS_IN")
    address = (_prop(e, "headquarters_address") or {}).get("value")
    if isinstance(address, str):
        tail = address.split(",")[-1].strip()
        country = ISO.get(tail) or (tail if tail in ISO.values() else None)
        if country:
            hq.append(country)
    return list(dict.fromkeys(hq)) if hq else names("IS_REGISTERED_IN")


def to_market_company(client: CalaClient, e: dict) -> dict:
    out = (e.get("relationships") or {}).get("outgoing", {})

    def issuer(r: dict) -> bool:
        return ((r.get("properties") or {}).get("role") or "ISSUER") == "ISSUER"

    events = [
        r
        for r in out.get("PARTICIPATES_IN_CORPORATE_EVENT", [])
        if issuer(r) and (r.get("entity_type") in ROUND_TYPES or r.get("name") == FINANCING_EVENT)
    ]
    events.sort(key=lambda r: str((r.get("properties") or {}).get("event_date") or ""), reverse=True)
    edges = [
        *[r for r in out.get("PARTICIPATES_IN_FUNDING_ROUND", []) if issuer(r)],
        *events[:MAX_FINANCING_EVENTS],
    ]

    def fetch_round(edge: dict):
        try:
            return parse_round(
                client.retrieve_entity(edge["id"], {"properties": ROUND_PROPS}), edge.get("name", "")
            )
        except Exception:  # noqa: BLE001
            return None

    rounds = dedupe_rounds([r for r in _map(fetch_round, edges) if r])
    return {
        "id": e["id"],
        "name": e.get("name", ""),
        "description": sourced(e, "description"),
        "hq": sourced(e, "headquarters_address"),
        "founded": fuzzy_date(e, "founding_date"),
        # The company's reported total (all instruments), used when its rounds carry no amounts.
        "totalFunding": sourced_money(e, "total_funding", "total_funding_units"),
        "rounds": rounds,
        "investors": [
            {
                "id": i["id"],
                "name": i["name"],
                "sources": to_sources((i.get("properties") or {}).get("sources")),
            }
            for i in (e.get("relationships") or {}).get("incoming", {}).get("INVESTED_IN", [])
        ],
    }


def parse_round(r: dict, fallback: str) -> dict:
    instrument = str((_prop(r, "instrument") or {}).get("value") or "").upper()
    value, units = _prop(r, "transaction_value"), _prop(r, "transaction_value_units")
    date = fuzzy_date(r, "announcement_date") or fuzzy_date(r, "event_date")
    label, rtype = (_prop(r, "round_label") or {}).get("value"), (_prop(r, "round_type") or {}).get("value")
    if isinstance(label, str):
        stage = labelise(label)
    elif isinstance(rtype, str):
        stage = rtype
    else:
        stage = FILING_LABEL if fallback == FINANCING_EVENT else fallback
    seen, sources = set(), []
    for k in ROUND_PROPS:
        for s in to_sources((_prop(r, k) or {}).get("sources")):
            if (s["document"], s["name"]) not in seen:
                seen.add((s["document"], s["name"]))
                sources.append(s)
    return {
        "id": r["id"],
        "kind": classify(instrument, stage),
        "stage": stage,
        "instrument": instrument or None,
        "date": date["value"] if date else None,
        "amount": (
            {"amount": value["value"], "currency": units["value"]}
            if value and units and isinstance(value["value"], int | float) and isinstance(units["value"], str)
            else None
        ),
        "leadInvestorIds": [],
        "participantIds": [],
        "sources": sources,
    }


def classify(instrument: str, stage: str) -> str:
    if instrument == "EQUITY":
        return "equity"
    if instrument == "GRANT":
        return "grant"
    if instrument:
        return "other"
    if re.search(r"grant", stage, re.I):
        return "grant"
    if re.search(r"debt|loan|facility|note|bond|lease", stage, re.I):
        return "other"
    if re.search(r"seed|series|angel|venture|equity|growth", stage, re.I):
        return "equity"
    return "other"


def dedupe_rounds(rounds: list[dict]) -> list[dict]:
    """Same amount, currency and month count as one round; a classified round wins over a filing."""

    def is_filing(r: dict) -> bool:
        return r["stage"] == FILING_LABEL

    out: list[dict] = []
    for r in sorted(rounds, key=is_filing):
        twin = None
        if r["amount"]:
            twin = next(
                (
                    o
                    for o in out
                    if (o["kind"] == r["kind"] or is_filing(r))
                    and o["amount"]
                    and o["amount"]["amount"] == r["amount"]["amount"]
                    and o["amount"]["currency"] == r["amount"]["currency"]
                    and (o["date"] or {}).get("date", "")[:7] == (r["date"] or {}).get("date", "")[:7]
                ),
                None,
            )
        if twin:
            docs = {s["document"] for s in twin["sources"]}
            twin["sources"] += [s for s in r["sources"] if s["document"] not in docs]
        else:
            out.append(r)
    return out


def sourced(e: dict, key: str) -> dict | None:
    p = _prop(e, key)
    if not p:
        return None
    return {"value": p["value"], "sources": to_sources(p.get("sources"))}


def sourced_money(e: dict, value_key: str, units_key: str) -> dict | None:
    v, u = _prop(e, value_key), _prop(e, units_key)
    if not v or not u or not isinstance(v.get("value"), int | float) or not isinstance(u.get("value"), str):
        return None
    if v["value"] <= 0:
        return None
    return {"value": {"amount": v["value"], "currency": u["value"]}, "sources": to_sources(v.get("sources"))}


PRECISIONS = ["year", "month", "day"]


def fuzzy_date(e: dict, key: str) -> dict | None:
    s = sourced(e, key)
    if not s or not isinstance(s["value"], str):
        return None
    v = s["value"][:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        precision = "day"
    elif re.fullmatch(r"\d{4}-\d{2}", v):
        precision = "month"
    elif re.fullmatch(r"\d{4}", v):
        precision = "year"
    else:
        return None
    stated = [(_prop(e, f"{key}__precision") or {}).get("value")]
    stated += [src.get("date_precision") for src in s["sources"] if (src.get("date") or "")[:10] == v]
    for p in stated:
        p = p.lower() if isinstance(p, str) else None
        if p in PRECISIONS and PRECISIONS.index(p) < PRECISIONS.index(precision):
            precision = p
    length = {"year": 4, "month": 7, "day": 10}[precision]
    return {"value": {"date": v[:length], "precision": precision}, "sources": s["sources"]}


def labelise(label: str) -> str:
    if label == "PRE_SEED":
        return "Pre-seed"
    words = label.lower().split("_")
    return " ".join(w[:1].upper() + w[1:] if i == 0 or len(w) == 1 else w for i, w in enumerate(words))
