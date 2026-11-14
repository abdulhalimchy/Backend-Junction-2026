# ruff: noqa: E501
"""
Turns whatever the user typed into a market-analysis plan for Cala, in one LLM call.

The search box takes free text ("seed-stage fintech in Finland", "who builds underwater drones in Europe?",
"vertical farming southeast asia"). The LLM decides whether it describes a market at all (so no Cala credit is
spent on "fix my code"), names the market and the place in a standard form (so different wordings share one
stored report), and writes the four Cala requests:

- three knowledge_search questions (open-ended, answered with sourced facts): size, trends, deals;
- one knowledge_query in Cala's dot-notation filter language for the leading companies, the structured path
  Cala recommends for "find all X where Y" (each "." is one filter; order_by, limit and return() go last).

Code validates every field and falls back to safe defaults, so a weak LLM answer never breaks a run.
"""

from __future__ import annotations

import json
import re

from .llm import generate_json

MAX_TEXT = 300
MAX_KNOWN = 120
PLAYER_LIMIT = 12

SYSTEM = """You plan a market analysis for founders and investors. The user typed free text into a search box.
The data comes from Cala, a knowledge graph of verified, sourced facts about companies, funding rounds,
investors, deals and markets. Your output is turned into Cala requests, so it must be precise.

1. kind:
   - "market": the text describes an industry, sector, technology, product category or customer segment,
     possibly with a place, a stage or another qualifier ("AI chips", "seed fintech in Finland",
     "who builds underwater drones in Europe?", "vertical farming southeast asia"). Be generous: niche and
     emerging markets are markets, and questions about a market are markets.
   - "company": the text names one specific company, brand or product ("Spotify", "GE HealthCare").
   - "not_market": anything else (tasks, code, people, gibberish, generic words like "help" or "money").
   reason: one short sentence for the user. suggestions: when not "market", up to 3 short market searches
   the user may have meant; otherwise [].

2. Only for "market" (otherwise empty strings and lists):
   - market: the market as a short lowercase standard name, without the place. Fix spelling, expand obvious
     abbreviations ("ed tech" -> "edtech"). Keep every meaningful qualifier ("seed-stage fintech",
     "b2b payments", "ev charging"); never broaden or narrow it. If a known market means exactly the same,
     return that name exactly.
   - place: where, as a short standard English name: a country ("Finland"), a region ("Nordics", "Europe",
     "Southeast Asia", "DACH"), a city ("Helsinki") or "Worldwide" when no place is given.
   - countries: the English country names the place covers (["Finland"]; Nordics -> Finland, Sweden, Norway,
     Denmark, Iceland; Europe -> the European countries). [] for Worldwide. For a city, its country.
   - title: a short display title, e.g. "Seed-stage fintech in Finland" or "Underwater drones in Europe".
   - size_question: a Cala question asking for the market size and growth rate (CAGR) of this market in this
     place, with figures from market research reports with their year, currency and forecast period.
   - trends_question: a Cala question about key trends, growth drivers, challenges and regulation in this
     market in this place in the previous and current year.
   - deals_question: a Cala question about notable funding rounds, acquisitions, mergers, IPOs and exits in this
     market in this place in the previous and current year: company, amount with currency, round type, date,
     lead investors; for deals the buyer, target, value and date.
   - players_query: a Cala knowledge_query in its dot-notation filter language that lists the leading companies.
     Form: companies.<filter>=<value>.<filter>=<value>.order_by=funding DESC.limit=12.return(name, country, funding)
     Filters: industry=<market>, location=<place> (omit for Worldwide), and only if the user asked for them
     funding_round.series=<Seed|A|B|...>, founded>=<year>. Values are plain words (no quotes, no dots inside a
     value; write "us" not "u.s."). Example: companies.industry=underwater drones.location=Europe.order_by=funding DESC.limit=12.return(name, country, funding)
Write questions in plain English, one sentence or two, naming the market and place explicitly."""

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["market", "company", "not_market"]},
        "reason": {"type": "string"},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "market": {"type": "string"},
        "place": {"type": "string"},
        "countries": {"type": "array", "items": {"type": "string"}},
        "title": {"type": "string"},
        "size_question": {"type": "string"},
        "trends_question": {"type": "string"},
        "deals_question": {"type": "string"},
        "players_query": {"type": "string"},
    },
    "required": ["kind", "reason", "suggestions", "market", "place", "countries", "title"],
}

KINDS = {"market", "company", "not_market"}
_QUERY = re.compile(r"^[a-z_]+(\.[^.]+)+$")


def _clean(s: object, limit: int) -> str:
    return " ".join(str(s or "").split())[:limit]


def plan_market(text: str, known: list[str], year: int) -> dict:
    """Returns {kind, reason, suggestions, plan, tokens}; plan is None unless kind is "market"."""
    prompt = (
        f"Current year: {year}\nKnown markets: {json.dumps(known[-MAX_KNOWN:])}\n"
        f"Text: {json.dumps(text[:MAX_TEXT])}"
    )
    data, usage = generate_json(SYSTEM, prompt, SCHEMA)
    kind = data.get("kind") if data.get("kind") in KINDS else "market"
    out = {
        "kind": kind,
        "reason": _clean(data.get("reason"), 300),
        "suggestions": [_clean(s, 80) for s in data.get("suggestions") or [] if str(s).strip()][:3],
        "plan": None,
        "tokens": usage.as_dict(),
    }
    if kind == "market":
        out["plan"] = complete_plan(data, text, year)
    return out


def complete_plan(data: dict, text: str, year: int) -> dict:
    """Validated plan with defaults for anything missing; also used when the LLM is unavailable."""
    market = _clean(data.get("market"), 80).lower() or _clean(text, 80).lower()
    place = _clean(data.get("place"), 60) or "Worldwide"
    worldwide = place.lower() in {"worldwide", "global", "anywhere", "world"}
    if worldwide:
        place = "Worldwide"
    countries = (
        [] if worldwide else [_clean(c, 60) for c in data.get("countries") or [] if str(c).strip()][:60]
    )
    where = " worldwide" if worldwide else f" in {place}"
    prev = year - 1
    return {
        "market": market,
        "place": place,
        "countries": countries,
        "title": _clean(data.get("title"), 120) or f"{market.capitalize()}{where}",
        "size_question": _clean(data.get("size_question"), 500)
        or f"What is the market size and expected growth rate (CAGR) of the {market} market{where}? Give figures from market research reports with their year, currency and forecast period.",
        "trends_question": _clean(data.get("trends_question"), 500)
        or f"What are the key trends, growth drivers, challenges and regulations in the {market} market{where} in {prev} and {year}?",
        "deals_question": _clean(data.get("deals_question"), 600)
        or f"What notable funding rounds, acquisitions, mergers, IPOs and exits happened in the {market} market{where} in {prev} and {year}? "
        "For funding rounds give the company, amount with currency, round type, date and lead investors. For deals give the buyer, the target, the value if disclosed, and the date.",
        "players_query": players_query(data.get("players_query"), market, "" if worldwide else place),
    }


def players_query(raw: object, market: str, place: str) -> str:
    """The LLM's dot-notation query if it is well formed (with a capped limit), else one built from the plan."""
    q = _clean(raw, 400)
    if q and _QUERY.match(q.split(".return(")[0]) and q.count("(") <= 1:
        q = re.sub(r"\.limit=\d+", f".limit={PLAYER_LIMIT}", q)
        if ".limit=" not in q:
            q = (
                q.replace(".return(", f".limit={PLAYER_LIMIT}.return(")
                if ".return(" in q
                else q + f".limit={PLAYER_LIMIT}"
            )
        if ".return(" not in q:
            q += ".return(name, country, funding)"
        return q
    value = lambda s: re.sub(r"[.()=,]", " ", s).strip()  # noqa: E731 - dots and operators split filters
    location = f".location={value(place)}" if place else ""
    return f"companies.industry={value(market)}{location}.order_by=funding DESC.limit={PLAYER_LIMIT}.return(name, country, funding)"


def fallback_players_definition(plan: dict) -> str:
    """The natural-language query used before the planner, tried when the structured one finds nothing."""
    where = f" in {plan['place']}" if plan["countries"] else " worldwide"
    return f"Leading and largest companies in the {plan['market']} market{where}, including public companies.return(name, country)"
