"""Gemini structured-output call for market analysis, with token usage. Server-side only."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass

from django.conf import settings
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

logger = logging.getLogger(__name__)

RETRIES = 2


class LLMError(Exception):
    pass


@dataclass
class TokenUsage:
    input: int = 0
    output: int = 0
    thinking: int = 0
    total: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def model() -> str:
    return settings.MARKET_LLM_MODEL


def configured() -> bool:
    return bool(settings.GEMINI_API_KEY)


def generate_json(system: str, prompt: str, schema: dict) -> tuple[dict, TokenUsage]:
    """
    Asks Gemini for JSON matching `schema`. Low thinking keeps latency at a few seconds: the work is
    classifying and summarising given facts, not open-ended reasoning. Retries rate limits and server errors.
    """
    if not configured():
        raise LLMError("GEMINI_API_KEY is not set")
    # Keep the client referenced for the whole call (a temporary client can be collected mid-request).
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    config = genai_types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_json_schema=schema,
        temperature=0.2,
        thinking_config=genai_types.ThinkingConfig(thinking_level="low"),
        http_options=genai_types.HttpOptions(timeout=120_000),
        automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
    )
    for attempt in range(RETRIES + 1):
        try:
            res = client.models.generate_content(model=model(), contents=[prompt], config=config)
            break
        except genai_errors.APIError as exc:
            retryable = exc.code == 429 or (exc.code or 0) >= 500
            if retryable and attempt < RETRIES:
                time.sleep(2 * (attempt + 1))
                continue
            raise LLMError(f"Gemini returned {exc.code}: {exc.message}") from exc

    meta = res.usage_metadata
    usage = TokenUsage(
        input=(meta and meta.prompt_token_count) or 0,
        output=(meta and meta.candidates_token_count) or 0,
        thinking=(meta and meta.thoughts_token_count) or 0,
        total=(meta and meta.total_token_count) or 0,
    )
    logger.info(
        "market llm %s tokens in=%s out=%s thinking=%s", model(), usage.input, usage.output, usage.thinking
    )
    try:
        data = json.loads(res.text or "")
    except json.JSONDecodeError as exc:
        reason = res.candidates[0].finish_reason if res.candidates else "unknown"
        raise LLMError(f"Gemini did not return valid JSON (finish reason: {reason})") from exc
    if not isinstance(data, dict):
        raise LLMError("Gemini returned no structured result.")
    return data, usage
