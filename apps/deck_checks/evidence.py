# ruff: noqa: E501
"""Gathers sourced facts about a company from Cala, as numbered evidence the verifier can cite.

Only values that come with Cala sources are evidence. `knowledge_query` rows carry no sources, so they are
used to find entity ids and never as facts themselves (same rule as the frontend's Cala layer).
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from apps.core.cala import CalaClient, CalaError, to_sources

logger = logging.getLogger(__name__)

PROFILE_PROPS = [
    "name", "legal_name", "aliases", "description", "website", "founding_date", "founding_date__precision",
    "headquarters_address", "employee_count_range", "operational_status", "funding_stage",
    "total_funding", "total_funding_units", "total_funding_excluding_debt", "total_funding_excluding_debt_units",
]  # fmt: skip
ROUND_PROPS = [
    "round_label", "round_type", "instrument", "transaction_value", "transaction_value_units",
    "announcement_date", "event_date",
]  # fmt: skip
ROUND_TYPES = {"FundingRound", "PrivateCompanyFundingRound"}
COMPANY_RELATIONSHIPS = {
    "outgoing": {
        "PARTICIPATES_IN_FUNDING_ROUND": {"limit": 12},
        "PARTICIPATES_IN_CORPORATE_EVENT": {"limit": 12},
        "HAS_HEADQUARTERS_IN": {"limit": 3},
        "OPERATES_IN_INDUSTRY": {"limit": 5},
    },
    "incoming": {
        "INVESTED_IN": {"limit": 25},
        "FOUNDED": {"limit": 10},
        "IS_CEO_OF": {"limit": 5},
        "IS_CTO_OF": {"limit": 5},
    },
}
# Exact-name candidates to compare when several legal entities share a name (brand, group, subsidiaries).
MAX_CANDIDATES = 6
LEGAL_SUFFIXES = {
    "as", "asa", "ab", "oy", "oyj", "aps", "ltd", "limited", "inc", "incorporated", "corp", "corporation", "co",
    "llc", "plc", "gmbh", "ag", "se", "sa", "sas", "bv", "nv", "spa", "srl", "kk", "pte", "pty",
}  # fmt: skip
MAX_COMPETITORS = 6


@dataclass
class Evidence:
    id: str
    text: str
    sources: list[dict] = field(default_factory=list)


def normalise_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", " ", name.lower())
    words = re.sub(r"[.'’]", "", name).replace(",", " ").replace("&", " ").split()
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _prop(entity: dict, key: str) -> dict | None:
    p = (entity.get("properties") or {}).get(key)
    if not p or p.get("value") in (None, "", []):
        return None
    return p


def _date(entity: dict, key: str) -> tuple[str, list[dict]] | None:
    p = _prop(entity, key)
    if not p or not isinstance(p["value"], str):
        return None
    value = p["value"][:10]
    precision = str((_prop(entity, f"{key}__precision") or {}).get("value") or "").lower()
    value = value[:4] if precision == "year" else value[:7] if precision == "month" else value
    return value, to_sources(p.get("sources"))


def _label(value: str) -> str:
    return "Pre-seed" if value == "PRE_SEED" else value.replace("_", " ").capitalize()


class EvidenceBuilder:
    def __init__(self, client: CalaClient):
        self.client = client
        self.items: list[Evidence] = []

    def add(self, text: str, sources: list[dict]) -> None:
        if sources:  # an unsourced value is not evidence
            self.items.append(Evidence(f"E{len(self.items) + 1}", text, sources))

    # -- company resolution -------------------------------------------------

    def resolve_company(self, company_id: str, names: list[str], website: str = "") -> dict | None:
        """The Cala company the deck is about, or None when no candidate matches safely.

        Cala records companies under their legal name with brands as aliases, and models brands, groups and
        subsidiaries as separate entities. So: search every name the deck goes by, accept a candidate only if
        its name, legal name or an alias equals one of them (or its website matches the deck's), and among
        accepted candidates take the one with the most data on record.
        """
        query = {"properties": PROFILE_PROPS, "relationships": COMPANY_RELATIONSHIPS}
        if company_id:
            return self.client.retrieve_entity(company_id, query)

        names = [n.strip() for n in dict.fromkeys(names) if n and n.strip()]
        wanted = {normalise_name(n) for n in names}
        domain = _domain(website)
        if not wanted and not domain:
            return None

        hits: dict[str, dict] = {}
        for n in names[:4]:
            for h in self.client.search_entities(n, ["Company", "Organization"], 8):
                hits.setdefault(h["id"], h)
        exact = [i for i, h in hits.items() if normalise_name(h["name"]) in wanted]
        pool = (exact or list(hits))[:MAX_CANDIDATES]
        if not pool:
            return None

        with ThreadPoolExecutor(max_workers=4) as ex:
            entities = list(ex.map(lambda i: self.client.retrieve_entity(i, query), pool))
        accepted = [e for e in entities if _matches(e, wanted, domain)]
        return max(accepted, key=_richness, default=None)

    # -- facts ----------------------------------------------------------------

    def add_company_facts(self, e: dict) -> None:
        name = e.get("name", "The company")
        if p := _prop(e, "legal_name"):
            self.add(f"{name} legal name: {p['value']}.", to_sources(p.get("sources")))
        if p := _prop(e, "aliases"):
            self.add(
                f"{name} is also known as: {', '.join(map(str, p['value']))}.", to_sources(p.get("sources"))
            )
        if p := _prop(e, "website"):
            self.add(f"{name} website: {p['value']}.", to_sources(p.get("sources")))
        if d := _date(e, "founding_date"):
            self.add(f"{name} was founded {d[0]}.", d[1])
        if p := _prop(e, "headquarters_address"):
            self.add(f"{name} headquarters: {p['value']}.", to_sources(p.get("sources")))
        if p := _prop(e, "employee_count_range"):
            v = p["value"]
            rng = (
                f"{v[0]}-{v[-1]}"
                if isinstance(v, list) and len(v) > 1 and v[0] != v[-1]
                else str(v[0] if isinstance(v, list) else v)
            )
            self.add(f"{name} has {rng} employees on record.", to_sources(p.get("sources")))
        if p := _prop(e, "description"):
            self.add(f"{name} description: {p['value']}", to_sources(p.get("sources")))
        if p := _prop(e, "operational_status"):
            self.add(f"{name} operational status: {p['value']}.", to_sources(p.get("sources")))
        if p := _prop(e, "funding_stage"):
            self.add(f"{name} funding stage: {_label(str(p['value']))}.", to_sources(p.get("sources")))
        for key, unit_key, label in (
            ("total_funding", "total_funding_units", "total funding"),
            (
                "total_funding_excluding_debt",
                "total_funding_excluding_debt_units",
                "total funding excluding debt",
            ),
        ):
            v, u = _prop(e, key), _prop(e, unit_key)
            if v and u and isinstance(v["value"], int | float):
                self.add(
                    f"{name} {label} on record: {v['value']:,.0f} {u['value']}.", to_sources(v.get("sources"))
                )

        incoming = e.get("relationships", {}).get("incoming", {})
        for rel, label in (("FOUNDED", "a founder of"), ("IS_CEO_OF", "CEO of"), ("IS_CTO_OF", "CTO of")):
            for person in incoming.get(rel, []):
                props = person.get("properties", {})
                since = f" (since {str(props['valid_since'])[:10]})" if props.get("valid_since") else ""
                until = f" (until {str(props['valid_until'])[:10]})" if props.get("valid_until") else ""
                self.add(
                    f"{person['name']} is recorded as {label} {name}{since}{until}.",
                    to_sources(props.get("sources")),
                )
        for ind in e.get("relationships", {}).get("outgoing", {}).get("OPERATES_IN_INDUSTRY", []):
            label = ind.get("properties", {}).get("original_label") or ind["name"]
            self.add(
                f"{name} operates in the industry: {label}.",
                to_sources(ind.get("properties", {}).get("sources")),
            )

        investors = incoming.get("INVESTED_IN", [])
        for inv in investors:
            self.add(
                f"{inv['name']} is recorded as an investor in {name}.",
                to_sources(inv.get("properties", {}).get("sources")),
            )

        out = e.get("relationships", {}).get("outgoing", {})
        edges = [
            *out.get("PARTICIPATES_IN_FUNDING_ROUND", []),
            *[
                r
                for r in out.get("PARTICIPATES_IN_CORPORATE_EVENT", [])
                if r.get("entity_type") in ROUND_TYPES
            ],
        ]
        edges = [r for r in edges if (r.get("properties", {}).get("role") or "ISSUER") == "ISSUER"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            rounds = list(pool.map(lambda r: self._round(name, r), edges))
        for text, sources in filter(None, rounds):
            self.add(text, sources)

    def _round(self, company: str, edge: dict) -> tuple[str, list[dict]] | None:
        try:
            r = self.client.retrieve_entity(
                edge["id"],
                {
                    "properties": ["name", *ROUND_PROPS],
                    "relationships": {"incoming": {"PARTICIPATES_IN_FUNDING_ROUND": {"limit": 30}}},
                },
            )
        except CalaError as exc:
            logger.warning("Could not load funding round %s: %s", edge["id"], exc)
            return None
        label, rtype = _prop(r, "round_label"), _prop(r, "round_type")
        stage = (
            _label(label["value"])
            if label
            else rtype["value"]
            if rtype
            else edge.get("name", "Funding round")
        )
        instrument = str((_prop(r, "instrument") or {}).get("value") or "").lower()
        value, units = _prop(r, "transaction_value"), _prop(r, "transaction_value_units")
        when = _date(r, "announcement_date") or _date(r, "event_date")
        amount = (
            f"{value['value']:,.0f} {units['value']}"
            if value and units and isinstance(value["value"], int | float)
            else "undisclosed amount"
        )
        parts = (r.get("relationships") or {}).get("incoming", {}).get("PARTICIPATES_IN_FUNDING_ROUND", [])
        backers = [x for x in parts if (x.get("properties", {}).get("role") or "") in ("INVESTOR", "LENDER")]
        leads = [x["name"] for x in backers if x.get("properties", {}).get("is_lead")]
        others = [x["name"] for x in backers if not x.get("properties", {}).get("is_lead")]
        who = (f" Led by {', '.join(leads)}." if leads else "") + (
            f" Participants: {', '.join(others[:12])}." if others else ""
        )
        text = f"{company} funding round: {stage}{f' ({instrument})' if instrument else ''}, {amount}{f', {when[0]}' if when else ''}.{who}"
        sources = [s for k in ROUND_PROPS if (p := _prop(r, k)) for s in to_sources(p.get("sources"))]
        sources += [s for x in backers for s in to_sources(x.get("properties", {}).get("sources"))]
        unique = list({(s["name"], s["document"]): s for s in sources}.values())
        return text, unique

    def add_competitors(self, company_name: str) -> None:
        """Competitors Cala lists for the company, each with its own sourced description."""
        res = self.client.knowledge_query(f"companies.competitors_of={company_name}.return(name, country)")
        names = list(
            dict.fromkeys(
                str(r.get("name") or r.get("company") or "").strip() for r in res.get("results", [])
            )
        )
        own = normalise_name(company_name)
        linked = {
            normalise_name(n): x["id"]
            for x in res.get("entities") or []
            for n in [x["name"], *x.get("mentions", [])]
        }
        ids = []
        for n in filter(None, names):
            key = normalise_name(n)
            if key == own:
                continue
            ids.append(
                linked.get(key)
                or next(
                    (
                        h["id"]
                        for h in self.client.search_entities(n, ["Company", "Organization"], 5)
                        if normalise_name(h["name"]) == key
                    ),
                    None,
                )
            )
            if len([i for i in ids if i]) >= MAX_COMPETITORS:
                break
        listed = [i for i in ids if i]
        if not listed:
            return
        with ThreadPoolExecutor(max_workers=4) as pool:
            entities = list(
                pool.map(
                    lambda i: self.client.retrieve_entity(
                        i, {"properties": ["name", "description", "headquarters_address"]}
                    ),
                    listed,
                )
            )
        for c in entities:
            if (d := _prop(c, "description")) and (src := to_sources(d.get("sources"))):
                loc = (_prop(c, "headquarters_address") or {}).get("value")
                self.add(
                    f"Cala lists {c['name']} as a competitor of {company_name}{f' (HQ: {loc})' if loc else ''}. {d['value']}",
                    src,
                )


def _domain(url: str) -> str:
    host = re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/")[0]
    return host.removeprefix("www.")


def _matches(e: dict, wanted: set[str], domain: str) -> bool:
    names = [e.get("name", "")]
    if p := _prop(e, "legal_name"):
        names.append(str(p["value"]))
    if p := _prop(e, "aliases"):
        names += [str(a) for a in p["value"]] if isinstance(p["value"], list) else [str(p["value"])]
    if any(normalise_name(n) in wanted for n in names):
        return True
    site = _prop(e, "website")
    return bool(domain and site and _domain(str(site["value"])) == domain)


def _richness(e: dict) -> int:
    """How much this entity holds: used to prefer the main company over a thin subsidiary or duplicate."""
    rels = e.get("relationships") or {}
    return sum(1 for k in PROFILE_PROPS if _prop(e, k)) + sum(
        len(v) for d in rels.values() for v in d.values()
    )


def build_evidence(
    client: CalaClient, *, company_id: str, names: list[str], website: str = "", include_competitors: bool
) -> tuple[str, list[Evidence]]:
    """Returns (company name on record, evidence). Evidence is empty when the company is not in Cala."""
    builder = EvidenceBuilder(client)
    entity = builder.resolve_company(company_id, names, website)
    if entity is None:
        return "", []
    builder.add_company_facts(entity)
    if include_competitors:
        try:
            builder.add_competitors(entity["name"])
        except CalaError as exc:
            logger.warning("Competitor lookup failed: %s", exc)
    return entity["name"], builder.items
