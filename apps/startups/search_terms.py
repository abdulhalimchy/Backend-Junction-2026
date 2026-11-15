# ruff: noqa: E501
"""
Cala searches for companies like a startup, written by the LLM from what the founder entered.

A startup with no Cala record is compared with "peers": companies found by Cala knowledge_query filters. One
literal filter on the industry field is often too narrow (7Scholar's "Medical writing software" finds three
names, none with investors on record), while a good set of angles finds the venture-backed startups whose
investors are the ones to talk to. The LLM writes 3 to 5 searches, each from a different angle (the product,
the customer and problem, the category investors file it under, a close neighbour); code builds and checks
each query string, so a weak answer never breaks a run, and falls back to the plain industry search.
"""

from __future__ import annotations

import logging
import re

from apps.markets import llm

from .models import Startup

logger = logging.getLogger(__name__)

MAX_SEARCHES = 5

SYSTEM = """You help a startup founder find investors. Investors who backed companies like this startup are the
best ones to approach. Those companies are found with Cala, a knowledge graph of verified facts about companies,
funding rounds and investors, through a dot-notation filter on one field.

Write 3 to 5 searches that find venture-backed companies like this startup, each from a different angle.
Examples below are for a made-up startup selling AI bookkeeping software to restaurants; write your own for
the startup you are given.
1. the exact product category (e.g. "ai bookkeeping")
2. the customer and the problem solved (e.g. "accounting automation for restaurants")
3. the broader category investors would file it under, still about the same kind of product
   (e.g. "smb finance automation"; not "restaurant tech", which is mostly ordering and delivery)
4. optionally, a close neighbour: a product sold to the same buyers for a related job (e.g. "restaurant
   payroll software")
Every search must find companies that build the same kind of product, not every company in the customer's
industry.

For each search:
- entity: "startups" (young venture-backed companies; prefer this) or "companies" (any company; only when
  startups would be too few).
- field: "industry" for a category name, "description" for a short phrase of what the companies do.
- value: 2 to 6 plain lowercase words. No dots, quotes, commas, brackets or "=". Write "ai" not "a.i.".
- label: the same angle as a short phrase for the founder to read (e.g. "AI for life sciences").

Avoid angles dominated by large incumbents, consultancies, agencies, publishers or service providers: their
backers are public shareholders, not venture investors. Do not add a place unless the startup is clearly local."""

SCHEMA = {
    "type": "object",
    "properties": {
        "searches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "entity": {"type": "string", "enum": ["startups", "companies"]},
                    "field": {"type": "string", "enum": ["industry", "description"]},
                    "value": {"type": "string"},
                },
                "required": ["label", "entity", "field", "value"],
            },
        }
    },
    "required": ["searches"],
}

_UNSAFE = re.compile(r"[.()=,\"'\[\]{}]")


def _value(text: str) -> str:
    """Plain words only: dots and operators would split or break a Cala filter."""
    return " ".join(_UNSAFE.sub(" ", str(text or "")).split())[:80]


def query_for(entity: str, field: str, value: str) -> str:
    extra = ", founded_year" if entity == "startups" else ""
    return f"{entity}.{field}={value}.return(name, country{extra})"


def fallback(s: Startup) -> list[dict]:
    """The single search used before the LLM: the industry, else the start of the description."""
    topic = _value((s.industry or s.description)[:80])
    if not topic:
        return []
    return [{"label": topic, "query": query_for("startups", "description", topic)}]


def plan_searches(s: Startup) -> list[dict]:
    """[{label, query}], best angle first. Falls back to the plain industry search when the LLM is unavailable."""
    if not (s.industry or s.description):
        return []
    if not llm.configured():
        return fallback(s)
    prompt = "\n".join(
        f"{k}: {v}"
        for k, v in [
            ("Name", s.name),
            ("Website", s.domain),
            ("Industry", s.industry),
            ("Description", s.description[:1500]),
            ("Stage", s.stage),
            ("Headquarters", s.headquarters),
        ]
        if v
    )
    try:
        data, _ = llm.generate_json(SYSTEM, prompt, SCHEMA)
    except Exception:  # noqa: BLE001 - the plain search still works without the LLM
        logger.warning("Search planning failed for startup %s", s.pk, exc_info=True)
        return fallback(s)

    out: list[dict] = []
    seen: set[str] = set()
    for item in data.get("searches") or []:
        if not isinstance(item, dict):
            continue
        entity = item.get("entity") if item.get("entity") in {"startups", "companies"} else "startups"
        field = item.get("field") if item.get("field") in {"industry", "description"} else "industry"
        value = _value(item.get("value", "")).lower()
        if len(value) < 3 or value in seen:
            continue
        seen.add(value)
        label = " ".join(str(item.get("label") or value).split())[:60]
        out.append({"label": label, "query": query_for(entity, field, value)})
        if len(out) == MAX_SEARCHES:
            break
    return out or fallback(s)


FILTER_SYSTEM = """You check which companies are like a startup, so that the startup can approach their
investors. Investors who back a company of the same kind (the same kind of product, sold to the same kind of
buyer) are likely to back the startup too.

Keep a company when it builds the same kind of product as the startup, or a different product of the same kind
(e.g. other software) for the same buyers, even for a different job.
Drop a company when it is a different kind of business that only shares the startup's customers or industry:
makers of the customers' own products (e.g. drug developers when the customers are pharma companies), service
providers, consultancies, agencies, publishers, or large public incumbents.
Return the ids of the companies to keep. When unsure, keep the company."""

FILTER_SCHEMA = {
    "type": "object",
    "properties": {"keep": {"type": "array", "items": {"type": "string"}}},
    "required": ["keep"],
}


def similar_companies(s: Startup, companies: list[dict]) -> set[str] | None:
    """
    Ids of `companies` ([{id, name, description}]) that build a product like the startup's. None when the
    check could not run (no LLM, or it failed): the caller then keeps every company.
    """
    if not companies or not llm.configured():
        return None
    lines = [
        f"- id={c['id']} | {c['name']} | {' '.join(str(c.get('description') or 'no description').split())[:300]}"
        for c in companies
    ]
    prompt = (
        f"Startup: {s.name}\nWhat it does: {(s.description or s.industry)[:1200]}\n\nCompanies:\n"
        + "\n".join(lines)
    )
    try:
        data, _ = llm.generate_json(FILTER_SYSTEM, prompt, FILTER_SCHEMA)
    except Exception:  # noqa: BLE001 - unfiltered results are still useful
        logger.warning("Peer check failed for startup %s", s.pk, exc_info=True)
        return None
    known = {c["id"] for c in companies}
    keep = {str(i) for i in data.get("keep") or []} & known
    # An empty answer is more likely a failure than "none are similar": keep everything rather than nothing.
    return keep or None
