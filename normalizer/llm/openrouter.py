"""OpenRouter provider — OpenAI-compatible chat completions."""
from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import SETTINGS
from .base import LLMError, LLMProvider


class OpenRouterProvider(LLMProvider):
    """Calls POST {base_url}/chat/completions.

    `complete` returns the message text. `complete_json` requests JSON-mode
    output (`response_format: {"type":"json_object"}`) and parses the content;
    if the upstream rejects the json_object param it falls back to a plain call
    so a provider quirk never breaks the pipeline.
    """

    def complete(self, messages: list[dict[str, Any]]) -> str:
        return self._post(messages, json_mode=False)

    def complete_json(
        self, messages: list[dict[str, Any]], schema: dict | None = None
    ) -> dict:
        # Reinforce the schema in-prompt; JSON mode just guarantees parseability.
        if schema is not None:
            messages = list(messages) + [
                {
                    "role": "system",
                    "content": (
                        "Respond with a SINGLE JSON object matching this schema "
                        f"and NOTHING else:\n{json.dumps(schema, ensure_ascii=False)}"
                    ),
                }
            ]
        raw = self._post(messages, json_mode=True)
        return _parse_json(raw)

    # ── internals ─────────────────────────────────────────────────────────────
    def _post(self, messages: list[dict[str, Any]], json_mode: bool) -> str:
        if not SETTINGS.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY not configured")

        url = f"{SETTINGS.openrouter_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {SETTINGS.openrouter_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": SETTINGS.openrouter_model,
            "messages": messages,
            "temperature": 0.2,
            # Headroom for the full structured JSON (agent_summary + customer_reply
            # can run long on phishing/duplicate cases). The provider default cap
            # truncated the JSON mid-string and forced a fallback.
            "max_tokens": 2048,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=SETTINGS.openrouter_timeout_s) as client:
                resp = client.post(url, headers=headers, json=payload)
            # Some providers 400 on response_format; retry once without it.
            if resp.status_code == 400 and json_mode:
                payload.pop("response_format", None)
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
            raise LLMError(f"openrouter returned no message content: {data}") from exc


def _parse_json(raw: str) -> dict:
    """Parse LLM JSON output tolerantly: strip code fences + trailing prose."""
    text = raw.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = text.split("```", 2)
        text = text[1] if len(text) >= 2 else text[0]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # grab the outermost {...} if prose leaked in
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        # strict=False tolerates raw control chars (newlines/tabs) inside string
        # values — Gemini sometimes emits multi-line strings without escaping.
        parsed = json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"LLM did not return valid JSON: {exc}; rawlen={len(raw)}; raw={raw[:400]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"LLM JSON was not an object: {type(parsed).__name__}")
    return parsed