"""OpenRouter transport using the OpenAI Python SDK.

Per CLAUDE.md §"LLM call pattern": the canonical transport for all four
stages. Every call uses:
  - model:    os.getenv("OPENROUTER_MODEL")
  - base_url: os.getenv("OPENROUTER_BASE_URL")
  - api_key:  os.getenv("OPENROUTER_API_KEY")
  - temperature=0.1
  - response_format={"type":"json_object"}
  - timeout=10s

Tenacity retries transient errors. The `llm_call` function is the public
entry point used by every stage; it raises LLMError on any failure.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI, OpenAIError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import LLMError, LLMProvider

log = logging.getLogger("normalizer.llm")


# ---------------------------------------------------------------------------
# Module-level client (singleton). Reads env at import time.
# ---------------------------------------------------------------------------

def _build_client() -> OpenAI:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    if not api_key:
        # Don't raise at import time — surface on first call so /health works.
        log.warning("OPENROUTER_API_KEY is empty; LLM calls will fail.")
    return OpenAI(api_key=api_key, base_url=base_url)


_CLIENT: OpenAI | None = None


def _client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = _build_client()
    return _CLIENT


def _model() -> str:
    return os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")


# ---------------------------------------------------------------------------
# Retry-wrapped raw completion
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type((OpenAIError, TimeoutError, ConnectionError)),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.3, min=0.3, max=1.0),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)
def _raw_complete(messages: list[dict[str, Any]], max_tokens: int) -> str:
    from .. import config  # local import to read timeout/temp

    response = _client().chat.completions.create(
        model=_model(),
        messages=messages,
        response_format={"type": "json_object"},
        temperature=config.LLM_TEMPERATURE,
        max_tokens=max_tokens,
        timeout=config.LLM_TIMEOUT_S,
    )
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError, KeyError) as exc:
        raise LLMError(f"openrouter returned no message content: {response}") from exc


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def llm_call(messages: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
    """Call the LLM, expect JSON, parse it, return dict. Raises LLMError on failure.

    Stages should catch LLMError and fall back to their deterministic path.
    """
    try:
        text = _raw_complete(messages, max_tokens)
    except LLMError:
        raise
    except (OpenAIError, TimeoutError, ConnectionError) as exc:
        raise LLMError(f"openrouter call failed: {exc}") from exc

    if not text:
        raise LLMError("openrouter returned empty content")

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"openrouter returned non-JSON content: {text[:200]}") from exc


def get_provider() -> LLMProvider:
    """Return an LLMProvider instance for cases that want ABC semantics."""
    return OpenRouterProvider()


class OpenRouterProvider(LLMProvider):
    """LLMProvider ABC implementation wrapping the module-level llm_call."""

    def complete(self, messages: list[dict[str, Any]], max_tokens: int) -> str:
        result = llm_call(messages, max_tokens)
        # ABC returns raw text; serialize the dict for ABC compatibility.
        return json.dumps(result, ensure_ascii=False)