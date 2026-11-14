# ruff: noqa: E501
"""
Guard against spending Cala credit on text that is not a market ("fix python code", "asdf", a question).
One small LLM call (~650 tokens). It also returns a canonical market name so different wordings of the same
market ("mobile apps", "mobile applications") share one cached analysis.
"""

from __future__ import annotations

import json

from .llm import generate_json

MAX_TEXT = 120
MAX_KNOWN = 120

SYSTEM = """You classify text typed into a market-analysis search box, before a paid data lookup runs.
- "market": an industry, sector, technology, product category or customer segment, optionally with a place
  or qualifier. Examples: "edtech", "underwater drones", "B2B payments in Germany", "vertical farming", "AI chips".
- "company": the name of one specific company, brand or product (e.g. "GE HealthCare", "Spotify", "Figma").
- "not_market": anything else: tasks, questions, instructions, code, people's names, gibberish, single
  generic words that name no market ("help", "test", "money").
Be generous: niche or emerging markets are markets. Only say not_market when no reasonable market is described.
reason: one short sentence for the user. suggestions: when the kind is not "market", up to 3 short market
descriptions the user may have meant (none if nothing sensible); when it is "market", an empty list.
canonical (only for "market"): the same market as a short, lowercase, standard name, used to share cached
analyses between wordings. Fix spelling and expand obvious abbreviations ("mobile apps" -> "mobile applications",
"ed tech" -> "edtech"). If one of the known markets means exactly the same market, return that name exactly.
Never broaden or narrow the market and keep every meaningful qualifier: "mobile games" is not "mobile applications",
"B2B payments" is not "payments", "EV charging" is not "electric vehicles". If unsure, keep the user's wording.
Leave place names out of canonical; the region is chosen separately."""

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["market", "company", "not_market"]},
        "reason": {"type": "string"},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "canonical": {"type": "string"},
    },
    "required": ["kind", "reason", "suggestions", "canonical"],
}

KINDS = {"market", "company", "not_market"}


def check_market_text(text: str, known: list[str]) -> dict:
    """Returns {kind, reason, suggestions, canonical, tokens}. `known` lists markets already analysed."""
    prompt = f"Known markets: {json.dumps(known[-MAX_KNOWN:])}\nText: {json.dumps(text[:MAX_TEXT])}"
    data, usage = generate_json(SYSTEM, prompt, SCHEMA)
    kind = data.get("kind") if data.get("kind") in KINDS else "market"
    canonical = " ".join(str(data.get("canonical") or "").split()).lower()[:80]
    suggestions = [str(s) for s in data.get("suggestions") or [] if str(s).strip()][:3]
    return {
        "kind": kind,
        "reason": str(data.get("reason") or ""),
        "suggestions": suggestions,
        "canonical": canonical if kind == "market" else "",
        "tokens": usage.as_dict(),
    }
