"""Request/response logging middleware.

Logs every HTTP request on entry (>>) and every response on exit (<<) so
`docker compose logs -f backend` shows the full in/out cycle:

    >> POST /sort-ticket ip=1.2.3.4 ua=curl/8 body={"ticket_id":"T-001",...}
    INFO tickets.pipeline pipeline ticket_id=T-001 stage=persist ...
    << POST /sort-ticket 200 312ms body={"ticket_id":"T-001","case_type":...}

Ported from DamDekho's `apps/core/middleware/request_logger.py`, trimmed for
this stateless API: no auth/user, no agent-key suffix, no skipped paths (we
want every request, including /health). Bodies are capped to MAX_BODY_LOG_SIZE
so a huge payload never floods the log.
"""
from __future__ import annotations

import json
import logging
import time

from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger("backend.request")

# Cap logged bodies so a big payload can't flood the log stream.
MAX_BODY_LOG_SIZE = 4000
# Only capture bodies for these content types (skip form uploads, octet-stream).
LOGGABLE_CONTENT_TYPES = ("application/json", "application/x-www-form-urlencoded")


class RequestResponseLogMiddleware(MiddlewareMixin):
    """Two-phase HTTP logger: `>>` on entry, `<<` on exit, with bodies."""

    # ── entry ───────────────────────────────────────────────────────────────
    def process_request(self, request):
        request._log_start_time = time.monotonic()
        request._log_req_body = self._capture_request_body(request)

        ip = self._client_ip(request)
        ua = request.META.get("HTTP_USER_AGENT", "")[:120]
        body = request._log_req_body or "-"
        logger.info(">> %s %s ip=%s ua=%s body=%s",
                    request.method, request.get_full_path(), ip, ua, body)

    # ── exit ────────────────────────────────────────────────────────────────
    def process_response(self, request, response):
        start = getattr(request, "_log_start_time", None)
        duration_ms = round((time.monotonic() - start) * 1000, 1) if start else 0
        status_code = response.status_code

        if status_code >= 500:
            log_fn = logger.error
        elif status_code >= 400:
            log_fn = logger.warning
        else:
            log_fn = logger.info

        resp_body = self._capture_response_body(response)
        log_fn("<< %s %s %d %dms body=%s",
               request.method, request.get_full_path(), status_code,
               duration_ms, resp_body)
        return response

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _capture_request_body(request) -> str:
        """Read the request body before the view consumes it (Django caches)."""
        content_type = request.content_type or ""
        if not any(ct in content_type for ct in LOGGABLE_CONTENT_TYPES):
            return "-"
        try:
            body = request.body
        except Exception:
            return "-"
        return _clip(body.decode("utf-8", errors="replace"))

    @staticmethod
    def _capture_response_body(response) -> str:
        """Rendered response body for JSON responses."""
        content_type = response.get("Content-Type", "")
        if "application/json" not in content_type:
            return "-"
        try:
            body = response.content
        except Exception:
            return "-"
        if not body:
            return "-"
        return _clip(body.decode("utf-8", errors="replace"))

    @staticmethod
    def _client_ip(request) -> str:
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")


def _clip(text: str) -> str:
    """One-line, size-capped body string for log lines."""
    if not text:
        return "-"
    flat = " ".join(text.split())  # collapse newlines so it stays one line
    if len(flat) <= MAX_BODY_LOG_SIZE:
        return flat
    return flat[:MAX_BODY_LOG_SIZE] + f"…[+{len(flat) - MAX_BODY_LOG_SIZE}b]"