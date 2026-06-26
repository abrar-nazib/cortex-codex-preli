"""HTTP client for the normalizer service.

One place that knows how to reach the normalizer (URL/timeout from settings) and
how to retry (tenacity). Returns plain dicts; the pipeline validates + coerces.
A normalizer call failing is a recoverable pipeline error -> the orchestrator
falls back to a conservative response, never a raw 500 to the customer.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("backend.normalizer_client")


class NormalizerError(Exception):
    """Raised when the normalizer is unreachable or returns a bad response."""


def _base_url() -> str:
    return settings.NORMALIZER_URL.rstrip("/")


def _retry_cfg():
    return retry(
        retry=retry_if_exception_type((httpx.HTTPError, NormalizerError)),
        stop=stop_after_attempt(max(1, getattr(settings, "NORMALIZER_MAX_RETRIES", 2) + 1)),
        wait=wait_exponential(
            multiplier=getattr(settings, "NORMALIZER_RETRY_BACKOFF_S", 0.5),
            min=0.2,
            max=2.0,
        ),
        reraise=True,
    )


@_retry_cfg()
def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """POST /analyze. Returns the normalizer's structured result dict."""
    url = f"{_base_url()}/analyze"
    timeout = getattr(settings, "NORMALIZER_TIMEOUT_S", 20.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise NormalizerError(f"normalizer unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise NormalizerError(
            f"normalizer /analyze returned {resp.status_code}: {resp.text[:200]}"
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise NormalizerError(f"normalizer returned non-JSON: {exc}") from exc


@_retry_cfg()
def rephrase(texts: dict[str, str], violations: list[str], *,
             language: str = "en", user_type: str | None = None,
             complaint_context: str = "") -> dict[str, str]:
    """POST /rephrase. Returns {field_name: safe_text}. Best-effort: on failure
    returns the input texts unchanged so the caller can re-scan + fall back."""
    url = f"{_base_url()}/rephrase"
    timeout = getattr(settings, "NORMALIZER_TIMEOUT_S", 20.0)
    body = {
        "texts": texts,
        "violations": violations,
        "language": language,
        "user_type": user_type,
        "complaint_context": complaint_context,
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body)
    except httpx.HTTPError as exc:
        log.warning("rephrase call failed: %s", exc)
        return dict(texts)
    if resp.status_code >= 400:
        log.warning("rephrase returned %d: %s", resp.status_code, resp.text[:200])
        return dict(texts)
    try:
        data = resp.json()
        out = data.get("rephrased", {})
        if isinstance(out, dict):
            # guarantee every requested field present
            return {k: (out.get(k) or texts.get(k, "")) for k in texts}
        return dict(texts)
    except ValueError:
        return dict(texts)