"""Skip guard for live 200-path tests.

The mocked pipeline/routing/safety tests in test_pipeline.py and the 400/422/500
contract tests never touch the real normalizer (they patch normalizer_client),
so they always run. Only the LIVE 200-path tests (test_live_200.py) hit the real
normalizer — these skip when the normalizer service is off (e.g. a local
`manage.py test` run without docker compose) so the suite stays green without an
LLM/backend stack.

Controls:
  - SKIP_LIVE_200_TESTS=1  -> always skip, even if the normalizer is reachable
                            (use for fast CI runs that should not make LLM calls)
"""
from __future__ import annotations

import os

import httpx
from django.conf import settings
from unittest import skipUnless


def normalizer_reachable(timeout: float = 1.5) -> bool:
    """True if the configured normalizer /health responds with ok."""
    if os.getenv("SKIP_LIVE_200_TESTS") == "1":
        return False
    base = getattr(settings, "NORMALIZER_URL", "")
    if not base:
        return False
    try:
        resp = httpx.get(base.rstrip("/") + "/health", timeout=timeout)
    except Exception:  # noqa: BLE001 — any transport error means "not on"
        return False
    if resp.status_code != 200:
        return False
    try:
        return resp.json().get("status") == "ok"
    except ValueError:
        return False


skipUnlessNormalizer = skipUnless(
    normalizer_reachable(),
    "normalizer service not reachable (or SKIP_LIVE_200_TESTS=1) — skipping live 200 tests",
)