# ruff: noqa: E501
"""
Company analysis: one company picked by the user, with its sourced profile and funding, recent developments,
and the market of its competitors. Runs through the same stored, stoppable pipeline as a market report
(analysis.py); this module holds the company-specific parts.

Finding the company: the free text is read by the LLM first (which company, any place for competitors, what
it does), then the name goes to Cala's entity search. Names are ambiguous (brands, legal entities and national
subsidiaries are separate records), so the user picks one; that entity's UUID is what every later request uses.

Cala requests per run (all in parallel):
- profile: entity retrieval with a projection (properties, investors, funding rounds, corporate events);
- competitors: knowledge_query in dot notation (companies.competitors_of=<name>...), then each competitor is
  loaded from its own entity record;
- news: knowledge_search for the latest rounds, deals, launches and leadership changes, with its facts.
"""

from __future__ import annotations

import json
import re

from django.utils import timezone

from apps.core.cala import CalaClient, to_sources
from apps.deck_checks.evidence import _prop, normalise_name

from . import report
from .llm import generate_json

COMPANY_TYPES = ["Company", "Organization"]
MAX_CANDIDATES = 8
COMPETITOR_LIMIT = 12

# -- reading the search text ---------------------------------------------------------------

FIND_SYSTEM = """You read text typed into a company search box for founders and investors, before a paid lookup.
kind: "company" if it names (or clearly asks about) one specific company, brand or organisation, even with extra
words ("orange telecom", "who invests in Wolt?", "Spotify competitors in Europe"); "market" if it describes an
industry, sector or category instead ("fintech in Finland"); "not_company" for anything else (tasks, code,
people's names, gibberish).
reason: one short sentence for the user. suggestions: when not "company", up to 3 short company names the user
may have meant (none if nothing sensible).
Only for "company" (otherwise empty strings and lists):
- name: the company name to look up, as the company calls itself, without legal suffixes or extra words
  ("orange telecom" -> "Orange", "who invests in Wolt?" -> "Wolt").
- hint: what the user says the company does, a few words, or "" ("orange telecom" -> "telecommunications").
- place: where to look for competitors, if the user gives a place ("Spotify competitors in Europe" -> "Europe"),
  as a short English name, else "Worldwide".
- countries: English country names that place covers; [] for Worldwide."""

FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["company", "market", "not_company"]},
        "reason": {"type": "string"},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "name": {"type": "string"},
        "hint": {"type": "string"},
        "place": {"type": "string"},
        "countries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["kind", "reason", "suggestions", "name", "hint", "place", "countries"],
}


def _clean(s: object, limit: int) -> str:
    return " ".join(str(s or "").split())[:limit]


def read_text(text: str) -> dict:
    """{kind, reason, suggestions, name, hint, place, countries} for the typed text (one small LLM call)."""
    data, _usage = generate_json(FIND_SYSTEM, f"Text: {json.dumps(text[:300])}", FIND_SCHEMA)
    kind = data.get("kind") if data.get("kind") in {"company", "market", "not_company"} else "company"
    return {
        "kind": kind,
        "reason": _clean(data.get("reason"), 300),
        "suggestions": [_clean(s, 80) for s in data.get("suggestions") or [] if str(s).strip()][:3],
        **place_fields(data, text if kind == "company" else ""),
    }


def place_fields(data: dict, fallback_name: str) -> dict:
    place = _clean(data.get("place"), 60) or "Worldwide"
    worldwide = place.lower() in {"worldwide", "global", "anywhere", "world"}
    return {
        "name": _clean(data.get("name"), 100) or _clean(fallback_name, 100),
        "hint": _clean(data.get("hint"), 100),
        "place": "Worldwide" if worldwide else place,
        "countries": []
        if worldwide
        else [_clean(c, 60) for c in data.get("countries") or [] if str(c).strip()][:60],
    }


def find_candidates(client: CalaClient, name: str) -> list[dict]:
    """Company records whose name matches, exact names (ignoring case and legal suffixes) first."""
    hits = client.search_entities(name[:100], COMPANY_TYPES, MAX_CANDIDATES)
    target = normalise_name(name)
    out = [
        {
            "id": h["id"],
            "name": h.get("name", ""),
            "entity_type": h.get("entity_type", ""),
            "description": h.get("description") or "",
            "exact": normalise_name(h.get("name", "")) == target,
        }
        for h in hits
    ]
    return sorted(out, key=lambda c: not c["exact"])


def company_plan(read: dict, entity_id: str, name: str, description: str, year: int) -> dict:
    """The Cala requests for one picked company, written from its record so an ambiguous name is not an issue."""
    what = _clean(description, 160) or read.get("hint") or ""
    place = read.get("place") or "Worldwide"
    value = lambda s: re.sub(r"[.()=,]", " ", s).strip()  # noqa: E731 - dots and operators split filters
    location = f".location={value(place)}" if place != "Worldwide" else ""
    return {
        "entity_id": entity_id,
        "market": name,
        "place": place,
        "countries": read.get("countries") or [],
        "title": name,
        "description": what,
        "news_questions": news_questions(name, what, year),
        "competitors_query": f"companies.competitors_of={value(name)}{location}.order_by=funding DESC.limit={COMPETITOR_LIMIT}.return(name, country, funding)",
    }


# -- the three sections -------------------------------------------------------------------

DEAL_EVENTS = {
    "MNA_COMPLETED": "Acquisition completed",
    "MNA_ANNOUNCED": "Acquisition announced",
    "MATERIAL_AGREEMENT_ANNOUNCED": "Agreement announced",
    "MATERIAL_AGREEMENT_TERMINATED": "Agreement terminated",
    "IPO": "IPO",
}


def company_profile(client: CalaClient, plan: dict) -> dict:
    e = client.retrieve_entity(
        plan["entity_id"], {"properties": report.COMPANY_PROPS, "relationships": report.COMPANY_RELATIONSHIPS}
    )
    base = report.to_market_company(client, e)
    report.enrich_investors(client, [base])
    out = (e.get("relationships") or {}).get("outgoing", {})

    def label(key: str) -> dict | None:
        s = report.sourced(e, key)
        return {"value": report.labelise(str(s["value"])), "sources": s["sources"]} if s else None

    deals = [
        {
            "id": r["id"],
            "kind": DEAL_EVENTS[r["name"]],
            "role": report.labelise(r["properties"]["role"])
            if isinstance((r.get("properties") or {}).get("role"), str)
            else None,
            "date": str((r.get("properties") or {}).get("event_date") or "")[:10] or None,
            "sources": to_sources((r.get("properties") or {}).get("sources")),
        }
        for r in out.get("PARTICIPATES_IN_CORPORATE_EVENT", [])
        if r.get("entity_type") == "CorporateEvent" and r.get("name") in DEAL_EVENTS
    ]
    return {
        **base,
        "legalName": report.sourced(e, "legal_name"),
        "website": report.sourced(e, "website"),
        "employees": _employees(e),
        "status": label("operational_status") or label("company_ownership_status"),
        "fundingStage": label("funding_stage"),
        "totalFundingExDebt": report.sourced_money(
            e, "total_funding_excluding_debt", "total_funding_excluding_debt_units"
        ),
        "industries": [
            {
                "name": report.labelise(i.get("name", "")),
                "label": (i.get("properties") or {}).get("original_label")
                if isinstance((i.get("properties") or {}).get("original_label"), str)
                else None,
                "sources": to_sources((i.get("properties") or {}).get("sources")),
            }
            for i in out.get("OPERATES_IN_INDUSTRY", [])
        ],
        "countries": report.company_countries(e),
        "deals": sorted(deals, key=lambda d: d["date"] or "", reverse=True),
    }


def _employees(e: dict) -> dict | None:
    p = _prop(e, "employee_count_range")
    v = (p or {}).get("value")
    if not isinstance(v, list) or not v or not all(isinstance(n, int | float) for n in v):
        return None
    a, b = v[0], v[-1]
    text = f"{int(a):,}–{int(b):,}" if b != a else f"{int(a):,}"
    return {"value": text, "sources": to_sources(p.get("sources"))}


def company_competitors(client: CalaClient, plan: dict) -> dict:
    """Competitors from Cala's structured filter; without a place filter if the place leaves none."""
    definition = plan["competitors_query"]
    companies, unresolved = _competitors(client, definition, plan)
    if not companies and ".location=" in definition:
        definition = re.sub(r"\.location=[^.]*", "", definition)
        companies, unresolved = _competitors(client, definition, {**plan, "countries": []})
    return {"definition": definition, "companies": companies, "unresolved": unresolved}


def _competitors(client: CalaClient, definition: str, plan: dict) -> tuple[list[dict], int]:
    companies, unresolved = report.load_candidates(
        client, client.knowledge_query(definition), plan["countries"]
    )
    # The company itself and its other legal entities (same name) are not its competitors.
    me = normalise_name(plan["market"])
    return [
        c for c in companies if c["id"] != plan["entity_id"] and normalise_name(c["name"]) != me
    ], unresolved


def news_questions(name: str, what: str, year: int) -> list[str]:
    """Two short questions: Cala answers "too complex" to one long question listing everything."""
    who = f"{name}, {short_what(what)}" if short_what(what) else name
    return [
        f"What funding rounds, acquisitions and mergers involved {who} in {year - 1} and {year}?",
        f"What were the main business developments of {who} in {year - 1} and {year}?",
    ]


def short_what(text: str) -> str:
    """ "Publicly listed French telecommunications company headquartered in …" -> "Publicly listed French telecommunications company"."""
    first = re.split(r"[.;(]| headquartered| founded| based| that | which ", text, maxsplit=1)[0]
    words = first.split()[:8]
    if words and words[0].lower() in {"a", "an", "the"}:
        words = words[1:]
    return " ".join(words)


def company_news(client: CalaClient, plan: dict) -> dict:
    """Both news questions; answers and facts combined (facts deduplicated), each answer kept under its question."""
    questions = plan.get("news_questions") or news_questions(
        plan["market"], plan.get("description", ""), timezone.now().year
    )
    answers = [report._ask(client, q) for q in questions if q]
    seen, evidence = set(), []
    for a in answers:
        for e in a["evidence"]:
            if e["id"] not in seen:
                seen.add(e["id"])
                evidence.append(e)
    useful = [a for a in answers if a["evidence"]] or answers
    return {
        "question": " / ".join(a["question"] for a in answers),
        "summary": "\n\n".join(f"**{a['question']}**\n\n{a['summary']}" for a in useful),
        "evidence": evidence,
    }


SECTIONS = {"profile": company_profile, "competitors": company_competitors, "news": company_news}


# -- summary facts and what a refresh changed ---------------------------------------------


def summary_facts(data: dict) -> list[dict]:
    from .analysis import _money, last_equity_round

    facts: list[dict] = []
    p = data.get("profile")
    if p:

        def add(text: str, sources: list) -> None:
            facts.append({"id": f"P{len(facts) + 1}", "text": text[:1500], "sources": sources})

        if p.get("description"):
            add(f"{p['name']}: {p['description']['value']}", p["description"]["sources"])
        if p.get("hq"):
            add(f"{p['name']} headquarters: {p['hq']['value']}.", p["hq"]["sources"])
        if p.get("founded"):
            add(f"{p['name']} was founded in {p['founded']['value']['date']}.", p["founded"]["sources"])
        if p.get("employees"):
            add(f"{p['name']} employees: {p['employees']['value']}.", p["employees"]["sources"])
        if p.get("totalFunding"):
            add(
                f"{p['name']} total funding reported: {_money(p['totalFunding']['value'])}.",
                p["totalFunding"]["sources"],
            )
        for r in sorted(p.get("rounds", []), key=lambda r: (r["date"] or {}).get("date", ""), reverse=True)[
            :12
        ]:
            add(
                f"{p['name']} {r['kind']} round: {r['stage']}, {_money(r['amount'])}, {(r['date'] or {}).get('date') or 'date unknown'}.",
                r["sources"],
            )
        if p.get("investors"):
            names = ", ".join(i["name"] for i in p["investors"][:20])
            add(
                f"Investors in {p['name']} on record: {names}.",
                [s for i in p["investors"][:5] for s in i["sources"]][:5],
            )
    for i, e in enumerate((data.get("news") or {}).get("evidence", [])[:18]):
        facts.append({"id": f"D{i + 1}", "text": e["text"][:1500], "sources": e["sources"]})
    for i, c in enumerate((data.get("competitors") or {}).get("companies", [])[:10]):
        last = last_equity_round(c)
        text = (
            f"Competitor {c['name']}: {(c.get('description') or {}).get('value') or 'no description on record'}"
            + (
                f" Last equity round: {last['stage']}, {_money(last['amount'])}, {(last['date'] or {}).get('date') or 'date unknown'}."
                if last
                else ""
            )
        )
        sources = [*((c.get("description") or {}).get("sources") or []), *((last or {}).get("sources") or [])]
        facts.append({"id": f"C{i + 1}", "text": text, "sources": sources})
    return facts


def diff(old: dict, new: dict) -> list[str]:
    out: list[str] = []
    if "profile" in new and old.get("profile"):
        before, after = len(old["profile"].get("rounds", [])), len(new["profile"].get("rounds", []))
        if after != before:
            out.append(f"Funding rounds on record: {before} → {after}.")
        old_inv = {i["name"] for i in old["profile"].get("investors", [])}
        if added := sorted({i["name"] for i in new["profile"].get("investors", [])} - old_inv):
            out.append(f"New investors: {', '.join(added[:8])}.")
    if "competitors" in new:
        old_c = {c["name"] for c in (old.get("competitors") or {}).get("companies", [])}
        new_c = {c["name"] for c in new["competitors"].get("companies", [])}
        if added := sorted(new_c - old_c):
            out.append(f"New competitors: {', '.join(added)}.")
        if removed := sorted(old_c - new_c):
            out.append(f"No longer listed as competitors: {', '.join(removed)}.")
    old_head = ((old.get("summary") or {}).get("headline") or {}).get("text")
    new_head = ((new.get("summary") or {}).get("headline") or {}).get("text")
    if "summary" in new and old_head != new_head:
        out.append("The summary was rewritten.")
    return out or ["Nothing changed since the last run."]
