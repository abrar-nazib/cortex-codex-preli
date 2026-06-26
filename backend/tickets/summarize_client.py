"""HTTP client for the normalizer's /summarize endpoint.

Contract: POST {NORMALIZER_URL}/summarize with {"text": "..."}.
Returns the summary string on success; raises SummarizerError on failure.
Retries on 5xx / network / timeout; 4xx is a caller error (no retry).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger("tickets.summarize_client")


class SummarizerError(Exception):
    """Raised when the normalizer can't produce a summary in time."""


class _RetryableHTTPError(Exception):
    """Internal signal: status code warrants a retry."""


def _build_retry_decorator() -> Any:
    return retry(
        reraise=True,
        retry=retry_if_exception_type(
            (_RetryableHTTPError, httpx.TimeoutException, httpx.NetworkError)
        ),
        stop=stop_after_attempt(max(1, settings.NORMALIZER_MAX_RETRIES + 1)),
        wait=wait_exponential(
            multiplier=settings.NORMALIZER_RETRY_BACKOFF_S,
            min=settings.NORMALIZER_RETRY_BACKOFF_S,
            max=2.0,
        ),
        before_sleep=before_sleep_log(log, logging.WARNING),
    )


@_build_retry_decorator()
def _post_summarize(url: str, payload: dict[str, Any], timeout_s: float) -> str:
    """One HTTP attempt. Raises _RetryableHTTPError on 5xx / network / timeout."""
    try:
        resp = httpx.post(url, json=payload, timeout=timeout_s)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise exc

    if 500 <= resp.status_code < 600:
        raise _RetryableHTTPError(f"normalizer {resp.status_code}: {resp.text[:200]}")

    if resp.status_code >= 400:
        # 4xx: do not retry, the caller is wrong.
        raise SummarizerError(f"normalizer {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise SummarizerError(f"normalizer returned non-JSON: {resp.text[:200]}") from exc

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise SummarizerError(f"normalizer returned no summary: {str(data)[:200]}")
    return summary


def call_summarize(text: str) -> str:
    """Public entry. Returns the summary string from the normalizer."""
    url = settings.NORMALIZER_URL.rstrip("/") + "/summarize"
    payload = {"text": text}
    log.info("-> normalizer POST %s len=%d timeout=%.1fs",
             url, len(text), float(settings.NORMALIZER_TIMEOUT_S))
    summary = _post_summarize(url, payload, timeout_s=float(settings.NORMALIZER_TIMEOUT_S))
    log.info("<- normalizer 200 summary_len=%d", len(summary))
    return summary