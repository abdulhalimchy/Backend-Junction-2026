# ruff: noqa: E501
"""
Executive summary for a market report. The caller sends numbered facts from Cala (with their sources); the LLM
writes only from those facts, then code checks every claim against the facts it cites. Anything that does not
check out is dropped and listed with the reason, never shown as fact.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .llm import generate_json, model

SYSTEM = """You are a market analyst writing for founders and investors.
Use ONLY the numbered facts provided. Never add knowledge of your own, never estimate, never fill gaps.
Every claim must cite the ids of the facts that support it. Numbers must be copied exactly as written in the facts.
Keep currencies as written; never convert between currencies. Keep grants separate from equity funding.
Market research publishers define markets differently: report their figures separately, never average them.
If facts conflict, say so. If the facts do not support a section, return an empty list for it.
Write plainly and concisely. No hype."""

_IDS = {"type": "array", "items": {"type": "string"}}
_CLAIM = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "factIds": _IDS},
    "required": ["text", "factIds"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": _CLAIM,
        "bullets": {"type": "array", "items": _CLAIM},
        "sizes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "publisher": {"type": "string"},
                    "scope": {"type": "string"},
                    "kind": {"type": "string", "enum": ["size", "forecast"]},
                    "year": {"type": "integer"},
                    "amount": {"type": "number"},
                    "scale": {
                        "type": "string",
                        "enum": ["unit", "thousand", "million", "billion", "trillion"],
                    },
                    "currency": {"type": "string"},
                    "cagrPercent": {"type": "number"},
                    "cagrPeriod": {"type": "string"},
                    "quote": {"type": "string"},
                    "factId": {"type": "string"},
                },
                "required": [
                    "publisher",
                    "scope",
                    "kind",
                    "year",
                    "amount",
                    "scale",
                    "currency",
                    "quote",
                    "factId",
                ],
            },
        },
        "deals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["funding", "grant", "acquisition", "merger", "ipo", "other"],
                    },
                    "stage": {"type": "string"},
                    "amount": {"type": "number"},
                    "scale": {
                        "type": "string",
                        "enum": ["unit", "thousand", "million", "billion", "trillion"],
                    },
                    "currency": {"type": "string"},
                    "date": {"type": "string"},
                    "counterparty": {"type": "string"},
                    "quote": {"type": "string"},
                    "factId": {"type": "string"},
                },
                "required": ["company", "type", "date", "quote", "factId"],
            },
        },
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["trend", "opportunity", "risk"]},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "factIds": _IDS,
                },
                "required": ["kind", "title", "text", "factIds"],
            },
        },
    },
    "required": ["headline", "bullets", "sizes", "deals", "insights"],
}

SCALE = {"unit": 1, "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
CURRENCY_MARKS = {
    "USD": ["$", "usd", "dollar"],
    "EUR": ["€", "eur", "euro"],
    "GBP": ["£", "gbp", "pound"],
    "SEK": ["sek", "kronor", "krona"],
    "NOK": ["nok", "krone"],
    "DKK": ["dkk", "krone"],
    "JPY": ["¥", "jpy", "yen"],
    "CNY": ["cny", "rmb", "yuan"],
    "INR": ["₹", "inr", "rupee"],
}
_UNNAMED = re.compile(r"^(unspecified|unknown|n/?a|not (stated|specified)|none)$", re.I)
# Numbers as written ("1,234.5"). Digits glued to letters ("H1", "Q3", "5G", "B2B") are labels, not figures.
_NUMBER = re.compile(r"(?<![A-Za-z\d.,])\d[\d,]*(?:\.\d+)?(?![A-Za-z])")


def summarise(market: str, region: str, year: int, facts: list[dict]) -> dict:
    data, usage = generate_json(SYSTEM, _prompt(market, region, year, facts), SCHEMA)
    return {**check(data, facts), "model": model(), "tokens": usage.as_dict()}


def _prompt(market: str, region: str, year: int, facts: list[dict]) -> str:
    lines = "\n".join(f"[{f['id']}] {' '.join(f['text'].split())}" for f in facts)
    return f"""Market: {market}
Region: {region}
Current year: {year}

Facts (S = market size, D = funding rounds and deals, T = trends, P = companies):
{lines}

Write:
1. headline: one sentence on the state of this market, with fact ids.
2. bullets: 3 to 5 key takeaways for a founder or investor, each with fact ids.
3. sizes: every market size or forecast figure in the S facts. For each: publisher, scope (what market the figure covers, e.g. "global mobile application market"), kind (size or forecast), year, amount exactly as the number appears in the quote, scale (unit, thousand, million, billion, trillion), currency (ISO code), cagrPercent and cagrPeriod if the same fact states them, quote (copied word for word from the fact, containing the amount and currency), and factId.
4. deals: every funding round, grant, acquisition, merger or IPO in the D facts, newest first, at most 15. For each:
   company (the one raising or being acquired), type, stage (e.g. "Seed", "Series A", if stated), amount and scale
   and currency (ISO code) only if the fact states them, date as "YYYY", "YYYY-MM" or "YYYY-MM-DD" (as precise as
   the fact), counterparty (lead investors, or the buyer), quote (copied word for word from the fact, containing
   the company and, if given, the amount), and factId.
5. insights: up to 6 trends, opportunities or risks, each with a short title, one or two sentences, and fact ids."""


def _norm(s: str) -> str:
    """Lower case, markdown stripped, whitespace collapsed."""
    return " ".join(re.sub(r"[*_#`>|]", " ", s.lower()).split())


def _numbers(s: str) -> list[float]:
    out = []
    for n in _NUMBER.findall(s):
        try:
            out.append(float(n.replace(",", "")))
        except ValueError:
            continue
    return out


def _num_str(n: float) -> str:
    """7.0 -> "7", 7.5 -> "7.5" (as the number is written in text)."""
    return str(int(n)) if float(n).is_integer() else str(n)


def check(raw: dict, facts: list[dict]) -> dict:
    by_id = {f["id"]: f for f in facts}
    dropped: list[dict] = []
    checked = 0

    def claim(c: dict | None, what: str) -> dict | None:
        """Survives if it cites at least one known fact and every number in it appears in a cited fact."""
        nonlocal checked
        if not c or not c.get("text"):
            return None
        checked += 1
        cited = [by_id[i] for i in c.get("factIds") or [] if i in by_id]
        if not cited:
            dropped.append({"what": f"{what}: {c['text']}", "reason": "cites no known fact"})
            return None
        known = {n for f in cited for n in _numbers(f["text"])}
        missing = [n for n in _numbers(c["text"]) if n not in known]
        if missing:
            nums = ", ".join(_num_str(n) for n in missing)
            dropped.append(
                {"what": f"{what}: {c['text']}", "reason": f"number {nums} not found in the cited facts"}
            )
            return None
        return {"text": c["text"], "sources": _dedupe([s for f in cited for s in f["sources"]])}

    sizes = []
    for s in raw.get("sizes") or []:
        checked += 1
        what = f"Size figure: {s.get('publisher', '')} {s.get('year', '')}"
        fact = by_id.get(s.get("factId"))
        if not fact:
            dropped.append({"what": what, "reason": "cites no known fact"})
            continue
        quote = _norm(s.get("quote") or "")
        if not quote or quote not in _norm(fact["text"]):
            dropped.append({"what": what, "reason": "quote not found word for word in the cited fact"})
            continue
        amount = s.get("amount")
        if not isinstance(amount, int | float) or not any(
            abs(n - amount) < 1e-9 for n in _numbers(s["quote"])
        ):
            dropped.append({"what": what, "reason": f"amount {amount} not in the quote"})
            continue
        currency = str(s.get("currency") or "")
        marks = CURRENCY_MARKS.get(currency.upper(), [currency.lower()])
        if not currency or not any(m and m in quote for m in marks):
            dropped.append(
                {"what": what, "reason": f"currency {currency or 'missing'} not stated in the quote"}
            )
            continue
        # CAGR is kept only if the same fact states that percentage.
        pct = s.get("cagrPercent")
        cagr = None
        if isinstance(pct, int | float) and re.search(
            rf"{re.escape(_num_str(pct))}\s?%", _norm(fact["text"])
        ):
            cagr = {"percent": pct, "period": s.get("cagrPeriod") or ""}
        # Fall back to the source's site when the fact does not name its publisher.
        publisher = str(s.get("publisher") or "").strip()
        if not publisher or _UNNAMED.match(publisher):
            publisher = _site_name(fact["sources"][0] if fact["sources"] else None)
        sizes.append(
            {
                "fact_id": fact["id"],
                "publisher": publisher,
                "scope": s.get("scope", ""),
                "kind": s.get("kind", "size"),
                "year": s.get("year"),
                "value": {"amount": amount * SCALE.get(s.get("scale"), 1), "currency": currency.upper()},
                "cagr": cagr,
                "quote": s["quote"],
                "sources": fact["sources"],
            }
        )

    deals = []
    for d in raw.get("deals") or []:
        checked += 1
        what = f"Deal: {d.get('company', '')} {d.get('date', '')}"
        fact = by_id.get(d.get("factId"))
        if not fact:
            dropped.append({"what": what, "reason": "cites no known fact"})
            continue
        quote = _norm(d.get("quote") or "")
        if not quote or quote not in _norm(fact["text"]):
            dropped.append({"what": what, "reason": "quote not found word for word in the cited fact"})
            continue
        value = None
        amount, currency = d.get("amount"), str(d.get("currency") or "").upper()
        if isinstance(amount, int | float) and amount > 0:
            marks = CURRENCY_MARKS.get(currency, [currency.lower()])
            in_quote = any(abs(n - amount) < 1e-9 for n in _numbers(d["quote"]))
            if in_quote and currency and any(m and m in quote for m in marks):
                value = {"amount": amount * SCALE.get(d.get("scale"), 1), "currency": currency}
            # An amount that does not check out is left blank ("undisclosed"), the deal itself stays.
        date = str(d.get("date") or "")
        date = date if re.fullmatch(r"\d{4}(-\d{2}){0,2}", date) else ""
        deals.append(
            {
                "fact_id": fact["id"],
                "company": _clean_name(d.get("company")),
                "type": d.get("type") if d.get("type") in DEAL_TYPES else "other",
                "stage": _clean_name(d.get("stage")),
                "value": value,
                "date": date,
                "counterparty": _clean_name(d.get("counterparty")),
                "quote": d["quote"],
                "sources": fact["sources"],
            }
        )

    insights = []
    for i in raw.get("insights") or []:
        c = claim(
            {"text": f"{i.get('title', '')}. {i.get('text', '')}", "factIds": i.get("factIds")}, "Insight"
        )
        if c:
            insights.append(
                {
                    "kind": i.get("kind"),
                    "title": i.get("title"),
                    "text": i.get("text"),
                    "sources": c["sources"],
                }
            )

    bullets = [b for b in (claim(b, "Takeaway") for b in raw.get("bullets") or []) if b]
    return {
        "headline": claim(raw.get("headline"), "Headline"),
        "bullets": bullets,
        "sizes": sorted(sizes, key=lambda x: x["year"] or 0),
        "deals": sorted(deals, key=lambda x: x["date"], reverse=True),
        "insights": insights,
        "fact_count": len(facts),
        "checked": checked,
        "dropped": dropped,
    }


DEAL_TYPES = {"funding", "grant", "acquisition", "merger", "ipo", "other"}


def _clean_name(v: object) -> str:
    s = " ".join(str(v or "").split())[:120]
    return "" if _UNNAMED.match(s) else s


def _site_name(source: dict | None) -> str:
    """ "https://www.giiresearch.com/report/..." -> "giiresearch.com"."""
    if not source:
        return "Unnamed source"
    host = urlparse(str(source.get("document") or "")).hostname
    return host.removeprefix("www.") if host else str(source.get("name") or "")[:40]


def _dedupe(sources: list[dict]) -> list[dict]:
    seen, out = set(), []
    for s in sources:
        key = (s.get("document"), s.get("name"))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# -- company summary ------------------------------------------------------------------------

COMPANY_SYSTEM = SYSTEM.replace("You are a market analyst", "You are a company analyst")
COMPANY_SCHEMA = {
    **SCHEMA,
    "properties": {k: v for k, v in SCHEMA["properties"].items() if k != "sizes"},
    "required": ["headline", "bullets", "deals", "insights"],
}


def summarise_company(name: str, place: str, year: int, facts: list[dict]) -> dict:
    """Same rules and checks as the market summary, for one company (P = profile, D = news, C = competitors)."""
    lines = "\n".join(f"[{f['id']}] {' '.join(f['text'].split())}" for f in facts)
    prompt = f"""Company: {name}
Competitors looked up in: {place}
Current year: {year}

Facts (P = the company's own record, D = recent news and deals, C = competitors):
{lines}

Write:
1. headline: one sentence on where this company stands (stage, funding, momentum), with fact ids.
2. bullets: 3 to 5 key takeaways for a founder competing with it or an investor looking at it, each with fact ids.
3. deals: every funding round, grant, acquisition, merger or IPO in the D facts involving this company, newest first,
   at most 15. For each: company (the one raising or being acquired), type, stage if stated, amount and scale and
   currency (ISO code) only if the fact states them, date as "YYYY", "YYYY-MM" or "YYYY-MM-DD", counterparty (lead
   investors, or the buyer), quote (copied word for word from the fact), and factId.
4. insights: up to 6 trends, opportunities or risks for this company, each with a short title, one or two
   sentences, and fact ids."""
    data, usage = generate_json(COMPANY_SYSTEM, prompt, COMPANY_SCHEMA)
    return {**check(data, facts), "model": model(), "tokens": usage.as_dict()}
