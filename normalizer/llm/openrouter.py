"""OpenRouter provider — OpenAI-compatible chat completions."""
from __future__ import annotations

from typing import Any

import httpx

from ..config import SETTINGS
from .base import LLMError, LLMProvider


class OpenRouterProvider(LLMProvider):
    """Calls POST {base_url}/chat/completions and returns the message text."""

    def complete(self, messages: list[dict[str, Any]]) -> str:
        if not SETTINGS.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY not configured")

        url = f"{SETTINGS.openrouter_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {SETTINGS.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": SETTINGS.openrouter_model,
            "messages": messages,
            "temperature": 0.2,
        }

        try:
            with httpx.Client(timeout=SETTINGS.openrouter_timeout_s) as client:
                resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"openrouter returned {exc.response.status_code}: "
                f"{exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"openrouter call failed: {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"openrouter returned no summary: {data}") from exc