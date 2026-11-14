# ruff: noqa: E501
"""Background lookup for a startup added by domain.

Two sources, most trusted first:
1. Cala: the company whose website on record matches the domain (or whose name matches the name the website
   gives). Every value keeps Cala's sources.
2. The company's own website, read by the LLM. Values are cited to the page they came from.

Nothing is guessed: a field neither source states stays empty for the user to fill in.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
from django.utils import timezone

from apps.core.cala import CalaClient, CalaError, is_configured, to_sources
from apps.deck_checks import llm
from apps.deck_checks.evidence import (
    COMPANY_RELATIONSHIPS,
    PROFILE_PROPS,
    _domain,
    _prop,
    _richness,
    normalise_name,
)

from .models import PROFILE_FIELDS, Startup

logger = logging.getLogger(__name__)

PAGE_LIMIT_BYTES = 400_000
PAGE_TEXT_CHARS = 12_000
STAGES = {"PRE_SEED": "Pre-seed", "SEED": "Seed"}
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov"}


def normalise_domain(value: str) -> str:
    return _domain(value).split(":")[0].strip(".")


def domain_stem(domain: str) -> str:
    """'northlight' for northlight.ai, app.northlight.co.uk or www.northlight.com."""
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


# -- website ----------------------------------------------------------------


class UnsafeHostError(Exception):
    pass


def _check_public(request: httpx.Request) -> None:
    """Refuse hosts that resolve to private, loopback or link-local addresses (the server fetches the URL)."""
    host = request.url.host
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeHostError(f"{host} does not resolve") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeHostError(f"{host} is not a public address")


def fetch_website(domain: str) -> tuple[str, str] | None:
    """(final URL, readable text) of the company's homepage, or None when it cannot be read."""
    for url in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
        try:
            with (
                httpx.Client(
                    follow_redirects=True,
                    max_redirects=4,
                    timeout=httpx.Timeout(12.0, connect=6.0),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; WarmlineBot/1.0)"},
                    event_hooks={"request": [_check_public]},
                ) as client,
                client.stream("GET", url) as res,
            ):
                if res.status_code >= 400 or "html" not in res.headers.get("content-type", "html"):
                    continue
                body = b""
                for chunk in res.iter_bytes():
                    body += chunk
                    if len(body) > PAGE_LIMIT_BYTES:
                        break
                text = page_text(body.decode(res.encoding or "utf-8", errors="replace"))
                if text:
                    return str(res.url), text
        except (httpx.HTTPError, UnsafeHostError) as exc:
            logger.info("Could not read %s: %s", url, exc)
    return None


def page_text(raw: str) -> str:
    """Title, meta description and visible text of an HTML page, trimmed for the LLM."""
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    metas = re.findall(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|og:title|og:site_name)["\'][^>]*content=["\']([^"\']*)',
        raw,
        re.I,
    )
    body = re.sub(r"<(script|style|noscript|svg|template)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    parts = [title.group(1) if title else "", *metas, body]
    text = html.unescape(" \n".join(parts))
    return re.sub(r"\s+", " ", text).strip()[:PAGE_TEXT_CHARS]


WEBSITE_TOOL = {
    "name": "report_company",
    "description": "Report what the company's own website says about it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The company's name as the site gives it. Empty if unclear.",
            },
            "other_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Legal name or other names the site uses for the company.",
            },
            "description": {
                "type": "string",
                "description": "One or two plain sentences on what the company does, in the third person. No marketing superlatives.",
            },
            "headquarters": {
                "type": "string",
                "description": "City, Country if the site states it. Else empty.",
            },
            "founded": {
                "type": "string",
                "description": "Founding year (YYYY) if the site states it. Else empty.",
            },
            "employees": {
                "type": "string",
                "description": "Team size if the site states it (e.g. 11-50). Else empty.",
            },
            "stage": {
                "type": "string",
                "description": "Funding stage if the site states it (e.g. Seed). Else empty.",
            },
            "industry": {
                "type": "string",
                "description": "The industry in 2-4 words, e.g. 'Marine robotics'.",
            },
        },
        "required": [
            "name",
            "other_names",
            "description",
            "headquarters",
            "founded",
            "employees",
            "stage",
            "industry",
        ],
    },
}

WEBSITE_SYSTEM = (
    "You read a company's homepage text for a startup profile. Report only what the text states or makes plain. "
    "Never guess or use outside knowledge: leave a field empty when the page does not say it. The industry may be "
    "summarised from what the company describes doing."
)


def read_website(domain: str) -> tuple[dict, str] | None:
    """(fields, page URL) read from the website by the LLM, or None."""
    page = fetch_website(domain)
    if not page:
        return None
    url, text = page
    try:
        out = llm._call_tool(WEBSITE_SYSTEM, f"Website: {url}\n\nPage text:\n{text}", WEBSITE_TOOL)
    except llm.LLMError as exc:
        logger.warning("Website read failed for %s: %s", domain, exc)
        return None
    fields = {k: str(out.get(k) or "").strip() for k in PROFILE_FIELDS}
    if not re.fullmatch(r"\d{4}", fields["founded"]):
        fields["founded"] = ""
    other = out.get("other_names") if isinstance(out.get("other_names"), list) else []
    fields["_other_names"] = [str(n).strip() for n in other if str(n).strip()][:4]
    return fields, url


# -- Cala -------------------------------------------------------------------


def find_in_cala(client: CalaClient, domain: str, names: list[str], name_confirmed: bool) -> dict | None:
    """The Cala company for this domain, or None. Among matches, the one with the most data.

    A company whose website on record is this domain always matches. A name match counts only when the
    company's own website confirmed the name (`name_confirmed`) and Cala holds no other website for it:
    names guessed from the domain alone are too ambiguous ("oura" is also a French retailer).
    """
    query = {"properties": PROFILE_PROPS, "relationships": COMPANY_RELATIONSHIPS}
    names = [n for n in dict.fromkeys(n.strip() for n in names) if n]
    wanted = {normalise_name(n) for n in names}
    hits: dict[str, dict] = {}
    for n in names[:4]:
        for h in client.search_entities(n, ["Company", "Organization"], 8):
            hits.setdefault(h["id"], h)
    exact = [i for i, h in hits.items() if normalise_name(h["name"]) in wanted]
    # Exact names first, then close ones (e.g. "Oura Health Oy" for oura.com), checked by website on record.
    pool = (exact + [i for i in hits if i not in exact])[:8]
    if not pool:
        return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        entities = list(ex.map(lambda i: client.retrieve_entity(i, query), pool))

    def site(e):
        p = _prop(e, "website")
        return normalise_domain(str(p["value"])) if p else ""

    by_site = [e for e in entities if site(e) and (site(e) == domain or site(e).endswith(f".{domain}"))]
    if by_site:
        return max(by_site, key=_richness)

    def named(e):
        names_on_record = [e.get("name", "")]
        if p := _prop(e, "legal_name"):
            names_on_record.append(str(p["value"]))
        if p := _prop(e, "aliases"):
            names_on_record += (
                [str(a) for a in p["value"]] if isinstance(p["value"], list) else [str(p["value"])]
            )
        return any(normalise_name(n) in wanted for n in names_on_record)

    if not name_confirmed:
        return None
    by_name = [e for e in entities if named(e) and not site(e)]
    return max(by_name, key=_richness, default=None)


def cala_fields(e: dict) -> dict[str, dict]:
    """field -> {value, sources} for the values Cala holds with sources."""
    out: dict[str, dict] = {}

    def put(field, value, sources):
        if value and sources:
            out[field] = {"value": value, "sources": sources}

    if e.get("name"):  # the entity's own name; its sources when Cala has them
        out["name"] = {
            "value": str(e["name"]),
            "sources": to_sources((_prop(e, "name") or {}).get("sources")),
        }
    if p := _prop(e, "description"):
        put("description", str(p["value"]), to_sources(p.get("sources")))
    if p := _prop(e, "founding_date"):
        value = str(p["value"])[:10]
        precision = str((_prop(e, "founding_date__precision") or {}).get("value") or "").lower()
        value = value[:4] if precision == "year" else value[:7] if precision == "month" else value
        put("founded", value, to_sources(p.get("sources")))
    if p := _prop(e, "headquarters_address"):
        put("headquarters", str(p["value"]), to_sources(p.get("sources")))
    if p := _prop(e, "employee_count_range"):
        v = p["value"]
        rng = (
            f"{v[0]}–{v[-1]}"
            if isinstance(v, list) and len(v) > 1 and v[0] != v[-1]
            else str(v[0] if isinstance(v, list) else v)
        )
        put("employees", rng, to_sources(p.get("sources")))
    if p := _prop(e, "funding_stage"):
        raw = str(p["value"])
        put("stage", STAGES.get(raw, raw.replace("_", " ").capitalize()), to_sources(p.get("sources")))
    for ind in (e.get("relationships") or {}).get("outgoing", {}).get("OPERATES_IN_INDUSTRY", [])[:1]:
        props = ind.get("properties", {})
        put(
            "industry",
            _label(str(props.get("original_label") or ind.get("name", ""))),
            to_sources(props.get("sources")),
        )
    return out


def _label(value: str) -> str:
    """SOFTWARE_AND_SERVICES -> Software and services; other labels unchanged."""
    return value.replace("_", " ").capitalize() if value.isupper() or "_" in value else value


# -- pipeline ---------------------------------------------------------------


def run_enrichment(startup_id) -> None:
    startup = Startup.objects.filter(pk=startup_id).first()
    if not startup:
        return
    startup.enrichment_status = Startup.Enrichment.PROCESSING
    startup.save(update_fields=["enrichment_status", "updated_at"])
    try:
        suggestions, cala_id, note = enrich(startup.domain)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user; the draft stays editable
        logger.exception("Startup lookup failed for %s", startup.domain)
        Startup.objects.filter(pk=startup.pk).update(
            enrichment_status=Startup.Enrichment.FAILED,
            enrichment_error=f"The lookup failed: {exc}"[:500],
            enriched_at=timezone.now(),
        )
        return
    Startup.objects.filter(pk=startup.pk).update(
        enrichment_status=Startup.Enrichment.COMPLETED,
        enrichment_error="",
        enrichment_note=note,
        suggestions=suggestions,
        cala_company_id=cala_id,
        enriched_at=timezone.now(),
    )


def enrich(domain: str) -> tuple[dict, str, str]:
    """(suggestions, Cala company id, note for the user)."""
    website = read_website(domain)
    site_fields, site_url = website if website else ({}, "")

    entity = None
    if is_configured():
        names = [site_fields.get("name", ""), *site_fields.get("_other_names", []), domain_stem(domain)]
        try:
            entity = find_in_cala(CalaClient(), domain, names, name_confirmed=bool(site_fields.get("name")))
        except CalaError as exc:
            logger.warning("Cala lookup failed for %s: %s", domain, exc)

    cala = cala_fields(entity) if entity else {}
    site_source = {
        "name": f"{domain} (company website)",
        "document": site_url,
        "date": date.today().isoformat(),
        "date_precision": "day",
        "source_rank": "PRIMARY",
    }
    suggestions: dict[str, dict] = {}
    for field in PROFILE_FIELDS:
        c = cala.get(field)
        # Cala's industry is a coarse registry code; the company's own wording describes it better.
        if field == "industry" and site_fields.get(field):
            c = None
        if c and c["value"] and (c["sources"] or field == "name"):
            suggestions[field] = {"value": c["value"], "origin": "cala", "sources": c["sources"]}
        elif site_fields.get(field):
            suggestions[field] = {"value": site_fields[field], "origin": "website", "sources": [site_source]}
    if "name" not in suggestions:
        suggestions["name"] = {"value": domain_stem(domain).capitalize(), "origin": "domain", "sources": []}

    found = [p for p, ok in (("Cala", entity), ("the company website", website)) if ok]
    if found:
        note = f"Filled in from {' and '.join(found)}. Check each value before saving."
    else:
        note = "Nothing on record for this domain yet. Fill in the details yourself."
    if not website:
        note += " The website could not be read automatically."
    return suggestions, (entity or {}).get("id", ""), note
