# ruff: noqa: E501
"""LLM calls for deck checks (Gemini or Claude): read the deck's claims, then compare each with Cala evidence."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

import anthropic
from django.conf import settings
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from .evidence import Evidence

CATEGORIES = ["funding", "team", "traction", "market", "company"]
MAX_CLAIMS = 40
# gemini-2.5-flash is closed to new API keys; gemini-4-hydrogen matches the market-analysis default.
DEFAULT_MODELS = {"gemini": "gemini-4-hydrogen", "anthropic": "claude-sonnet-5-5"}


class LLMError(Exception):
    pass


def _model() -> str:
    return settings.DECK_CHECK_LLM_MODEL or DEFAULT_MODELS[settings.DECK_CHECK_LLM_PROVIDER]


def _call_tool(system: str, text: str, tool: dict, pdf: bytes | None = None) -> dict:
    """One structured-output call: returns a dict matching `tool["input_schema"]`. The PDF is read natively."""
    provider = settings.DECK_CHECK_LLM_PROVIDER
    if provider == "gemini":
        return _gemini(system, text, tool, pdf)
    if provider == "anthropic":
        return _anthropic(system, text, tool, pdf)
    raise LLMError(f"Unknown DECK_CHECK_LLM_PROVIDER: {provider!r} (use 'gemini' or 'anthropic')")


def _gemini(system: str, text: str, tool: dict, pdf: bytes | None) -> dict:
    if not settings.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set")
    parts = [genai_types.Part.from_bytes(data=pdf, mime_type="application/pdf")] if pdf else []
    # Keep the client referenced for the whole call: a temporary client is garbage-collected mid-request,
    # which closes its HTTP connection ("Cannot send a request, as the client has been closed").
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    try:
        res = client.models.generate_content(
            model=_model(),
            contents=[*parts, text],
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=tool["input_schema"],
                temperature=0,
                http_options=genai_types.HttpOptions(timeout=180_000),
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
    except genai_errors.APIError as exc:
        raise LLMError(f"Gemini API error: {exc}") from exc
    try:
        out = json.loads(res.text or "")
    except json.JSONDecodeError as exc:
        raise LLMError("The model returned invalid JSON.") from exc
    if not isinstance(out, dict):
        raise LLMError("The model returned no structured result.")
    return out


def _anthropic(system: str, text: str, tool: dict, pdf: bytes | None) -> dict:
    if not settings.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set")
    content: list[dict] = []
    if pdf:
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf).decode(),
                },
            }
        )
    content.append({"type": "text", "text": text})
    try:
        res = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=180.0).messages.create(
            model=_model(),
            max_tokens=8000,
            temperature=0,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as exc:
        raise LLMError(f"Anthropic API error: {exc}") from exc
    for block in res.content:
        if block.type == "tool_use":
            return block.input
    raise LLMError("The model returned no structured result.")


EXTRACT_TOOL = {
    "name": "report_claims",
    "description": "Report the verifiable factual claims found in the pitch deck.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_name": {
                "type": "string",
                "description": "The company the deck is about, as the deck names it. Empty if unclear.",
            },
            "other_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Other names to look the company up by: its legal name, other names in the deck, "
                "and the name it is publicly known by today if it has since been renamed. Lookup only.",
            },
            "website": {
                "type": "string",
                "description": "The company's website domain if the deck shows one, else empty.",
            },
            "people": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Full names of the people the deck presents as founders, team, advisors or investors.",
            },
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "slide": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "1-based page the claim appears on.",
                        },
                        "claim": {
                            "type": "string",
                            "description": "The claim, close to the deck's wording. One fact per claim.",
                        },
                        "category": {"type": "string", "enum": CATEGORIES},
                    },
                    "required": ["slide", "claim", "category"],
                },
            },
        },
        "required": ["company_name", "other_names", "website", "people", "claims"],
    },
}

EXTRACT_SYSTEM = (
    "You read startup pitch decks for an investor-diligence tool. Extract the factual, checkable claims the deck "
    "makes about the company: founding date and place, headquarters, founders and executives by name, funding "
    "raised (amounts, rounds, dates, lead investors), named investors or accelerators, team size, "
    "customers, revenue or usage numbers, market size, and claims about competitors or being first/only. "
    "Skip vision statements, opinions, product descriptions, business-model descriptions and future plans. "
    "Do not turn comparison grids or positioning charts into one claim per cell; at most extract an explicit "
    "claim such as 'the only' or 'first to market'. Keep one fact per claim and the "
    "deck's own numbers and currency. For people, split each fact into its own claim: 'X is a co-founder', "
    "'X is Chief Scientist' and 'X previously worked at Meta' are three claims, never one. "
    f"Report at most {MAX_CLAIMS} claims. Read slide images and charts too, "
    "not just text. Never invent claims."
)


@dataclass
class DeckReading:
    company_name: str
    other_names: list[str]
    website: str
    claims: list[dict]
    people: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [self.company_name, *self.other_names]


def extract_claims(pdf_bytes: bytes) -> DeckReading:
    """Reads the deck's company and claims. The model reads the PDF natively, so scanned decks work."""
    out = _call_tool(EXTRACT_SYSTEM, "Extract the claims from this deck.", EXTRACT_TOOL, pdf=pdf_bytes)
    claims = []
    for c in out.get("claims", [])[:MAX_CLAIMS]:
        text = str(c.get("claim", "")).strip()
        if text and isinstance(c.get("slide"), int) and c["slide"] >= 1:
            claims.append(
                {
                    "slide": c["slide"],
                    "claim": text,
                    "category": c.get("category") if c.get("category") in CATEGORIES else "company",
                }
            )
    other = out.get("other_names") if isinstance(out.get("other_names"), list) else []
    return DeckReading(
        company_name=str(out.get("company_name", "")).strip(),
        other_names=[str(n).strip() for n in other if str(n).strip()][:5],
        website=str(out.get("website", "")).strip(),
        claims=claims,
        people=[str(n).strip() for n in (out.get("people") or []) if str(n).strip()][:15],
    )


VERIFY_TOOL = {
    "name": "report_verdicts",
    "description": "Report a verdict for every claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "Index of the claim in the list."},
                        "status": {"type": "string", "enum": ["confirmed", "differs", "cant_verify"]},
                        "on_record": {
                            "type": "string",
                            "description": "What the evidence says, in one or two plain sentences. Empty for cant_verify.",
                        },
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Evidence ids you relied on.",
                        },
                    },
                    "required": ["index", "status", "evidence_ids"],
                },
            }
        },
        "required": ["verdicts"],
    },
}

VERIFY_SYSTEM = (
    "You compare pitch-deck claims with sourced evidence from the Cala knowledge graph.\n"
    "- confirmed: the evidence supports the claim (allow rounding and a different wording of the same fact).\n"
    "- differs: the evidence contradicts it or shows something materially different (different amount, date, "
    "or a counter-example such as a competitor for an 'only company' claim). Explain the difference in on_record.\n"
    "- cant_verify: the evidence does not address the claim. That means NOT ON RECORD, never 'false'. Use it "
    "for traction, customers, revenue and market-size claims unless evidence directly covers them.\n"
    "Silence is not contradiction: evidence that does not mention something (an investor, a customer, a role) is "
    "cant_verify, not differs.\n"
    "Past jobs: if the evidence shows the person at the claimed employer, confirm it even when the recorded job "
    "title is worded differently, and give the recorded title in on_record. Use differs only when the record "
    "shows a materially more junior role, a different employer, or conflicting dates.\n"
    "Use only the evidence given; never use outside knowledge. Cite the evidence ids you used. Never sum amounts "
    "in different currencies. Grants are not equity: say so when a claim counts one as 'raised'. Return one "
    "verdict per claim."
)


def verify_claims(company: str, claims: list[dict], evidence: list[Evidence]) -> list[dict]:
    claim_lines = "\n".join(
        f"[{i}] (slide {c['slide']}, {c['category']}) {c['claim']}" for i, c in enumerate(claims)
    )
    ev_lines = "\n".join(f"{e.id}: {e.text}" for e in evidence) or "(no evidence on record)"
    text = f"Company: {company or 'unknown'}\n\nClaims:\n{claim_lines}\n\nEvidence from Cala:\n{ev_lines}"
    out = _call_tool(VERIFY_SYSTEM, text, VERIFY_TOOL)
    verdicts = out.get("verdicts")
    return verdicts if isinstance(verdicts, list) else []


VERIFY_WEB_SYSTEM = (
    "You check pitch-deck claims against excerpts from web pages that Cala found by searching for each claim. "
    "Each claim has its own excerpts: judge a claim only by its own excerpts. Pages vary in quality, can be wrong, "
    "and can be written later than the deck.\n"
    "- confirmed: excerpts state the same fact explicitly (allow rounding and different wording). Cite every "
    "excerpt that states it.\n"
    "- differs: excerpts state something materially different (another amount, date, role or employer). Explain "
    "the difference in on_record and cite every excerpt that shows it.\n"
    "- cant_verify: the excerpts do not address the claim, only hint at it, or disagree with each other.\n"
    "Silence is not contradiction: a list or profile that does not mention something is never grounds for differs. "
    "differs needs an excerpt that states something incompatible with the claim.\n"
    "Excerpts that only repeat this pitch deck's own figures (articles or templates about the deck) are not "
    "independent evidence: ignore them.\n"
    "For estimates such as market size or growth, confirm only if an excerpt reports the same estimate and says who "
    "made it; name them in on_record. A claim that someone 'led' something is confirmed only if an excerpt says they "
    "led it, not merely that they worked on it.\n"
    "Never use outside knowledge. on_record is one or two plain sentences that name the sources' sites."
)


VERIFY_WEB_TOOL = {
    **VERIFY_TOOL,
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        **VERIFY_TOOL["input_schema"]["properties"]["verdicts"]["items"]["properties"],
                        "quote": {
                            "type": "string",
                            "description": "For confirmed or differs: one sentence copied word for word from a cited "
                            "excerpt that on its own states the claim (confirmed) or states something incompatible "
                            "with it (differs). A list that merely omits something does not qualify. Empty for "
                            "cant_verify.",
                        },
                    },
                    "required": ["index", "status", "evidence_ids", "quote"],
                },
            }
        },
        "required": ["verdicts"],
    },
}


def verify_claims_web(company: str, claims: list[dict], found: dict[int, list[Evidence]]) -> list[dict]:
    """Second pass for claims the Cala record could not settle, each with its own searched excerpts."""
    blocks = []
    for i, ev in found.items():
        c = claims[i]
        lines = "\n".join(f"  {e.id} [{e.sources[0]['document'] if e.sources else ''}]: {e.text}" for e in ev)
        blocks.append(f"[{i}] (slide {c['slide']}, {c['category']}) {c['claim']}\n{lines}")
    text = f"Company the deck is about: {company or 'unknown'}\n\n" + "\n\n".join(blocks)
    out = _call_tool(VERIFY_WEB_SYSTEM, text, VERIFY_WEB_TOOL)
    verdicts = out.get("verdicts")
    return verdicts if isinstance(verdicts, list) else []
