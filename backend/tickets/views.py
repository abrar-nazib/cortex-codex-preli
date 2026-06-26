"""Views: GET /health, POST /summarize (legacy), POST /analyze-ticket.

Error handling for POST /analyze-ticket follows the Problem Statement §4.1:

  400  Malformed input — invalid JSON, non-object body, or missing required
       fields (ticket_id / complaint). The harness/agent sent something that
       is not a usable request body.
  422  Schema-valid JSON but semantically/type invalid — wrong data type,
       value too long, null where a non-null value is required, empty
       complaint, enum value outside the §7 taxonomy, etc.
  500  Internal error (not exercised yet — the analysis pipeline is not
       wired in). Reserved for when the normalizer/reasoning path can fail
       in ways the caller should not see details of.

The 200 (successful analysis) path is intentionally NOT implemented in this
scaffold — the deterministic + LLM reasoning pipeline lands next. A valid
request currently returns 501 so the 400/422 contract can be tested in
isolation without a fake 200 leaking through.
"""
from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    AnalyzeTicketOutSerializer,
    HealthOutSerializer,
    SummarizeInSerializer,
    SummarizeOutSerializer,
    TicketWithTransactionSerializer,
)
from .summarize_client import SummarizerError, call_summarize

log = logging.getLogger("backend")


# ─── /analyze-ticket error classification (§4.1) ─────────────────────────────
def _collect_error_codes(errors) -> list[str]:
    """Walk a DRF errors structure and collect every ErrorDetail `.code`.

    `errors` may be a dict (field -> [ErrorDetail]), a list (non_field / nested),
    nested dicts inside lists (transaction_history list items), or a single
    ErrorDetail. Recurse through all of it so a deep required/invalid/max_length
    code is not missed.
    """
    codes: list[str] = []
    if isinstance(errors, dict):
        for value in errors.values():
            codes.extend(_collect_error_codes(value))
    elif isinstance(errors, (list, tuple)):
        for item in errors:
            codes.extend(_collect_error_codes(item))
    else:
        # ErrorDetail (str subclass) or a plain str
        code = getattr(errors, "code", None)
        if code:
            codes.append(code)
    return codes


def _classify_validation_errors(errors) -> tuple[int, dict]:
    """Map a serializer's errors to (HTTP status, body) per §4.1.

    If every error code is `required` (missing required field, including nested
    ones), the body is structurally incomplete -> 400. Anything else
    (wrong type, max_length, null, blank, invalid_choice, invalid) is a
    schema-valid-but-bad value -> 422.
    """
    codes = _collect_error_codes(errors)
    if codes and all(c == "required" for c in codes):
        return status.HTTP_400_BAD_REQUEST, errors
    return status.HTTP_422_UNPROCESSABLE_ENTITY, errors


class HealthView(APIView):
    """Service health. Public, no auth. Must respond within 60s of start."""

    @extend_schema(responses=HealthOutSerializer)
    def get(self, request):
        return Response({"status": "ok"}, status=status.HTTP_200_OK)


class SummarizeView(APIView):
    """Summarize one piece of text via the normalizer. Public, no auth."""

    @extend_schema(
        request=SummarizeInSerializer,
        responses=SummarizeOutSerializer,
        examples=[
            OpenApiExample(
                "Summarize",
                value={"text": "Paste any text here to get a concise summary."},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = SummarizeInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data["text"]

        log.info("POST /summarize len=%d", len(text))
        try:
            summary = call_summarize(text)
        except SummarizerError as exc:
            log.warning("summarize FAILED: %s", exc)
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )

        log.info("summarize OK summary_len=%d", len(summary))
        return Response({"summary": summary}, status=status.HTTP_200_OK)


class AnalyzeTicketView(APIView):
    """POST /analyze-ticket — investigate one complaint + its transactions.

    Only the 400/422 contract is wired here (§4.1). The 200 analysis path
    (evidence-match -> classify -> route -> draft safe reply) is the next
    milestone; until then a valid request returns 501 so the error contract
    can be verified without a fake 200 slipping through.
    """

    @extend_schema(
        request=TicketWithTransactionSerializer,
        responses={
            200: AnalyzeTicketOutSerializer,
            400: OpenApiExample(
                "Malformed input (400)",
                value={"detail": "Malformed JSON body."},
            ),
            422: OpenApiExample(
                "Schema-valid but invalid (422)",
                value={"complaint": ["This field may not be null."]},
            ),
        },
        examples=[
            OpenApiExample(
                "Analyze ticket",
                value={
                    "ticket_id": "TKT-001",
                    "complaint": "I sent 5000 taka to a wrong number around 2pm today...",
                    "language": "en",
                    "channel": "in_app_chat",
                    "user_type": "customer",
                    "campaign_context": "boishakh_bonanza_day_1",
                    "transaction_history": [
                        {
                            "transaction_id": "TXN-9101",
                            "timestamp": "2026-04-14T14:08:22Z",
                            "type": "transfer",
                            "amount": 5000,
                            "counterparty": "+8801719876543",
                            "status": "completed",
                        }
                    ],
                },
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        # Phase 1: the body must be a JSON object. DRF parses request.data
        # lazily; a malformed-JSON body raises here and we turn it into a 400
        # (§4.1 "invalid JSON") rather than letting the global 400->422 handler
        # rewrite it. A valid-JSON non-object (list/number/string) is also 400
        # (the contract is a JSON object).
        try:
            data = request.data
        except Exception:
            return Response(
                {"detail": "Malformed JSON body."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(data, dict):
            return Response(
                {"detail": "Request body must be a JSON object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Phase 2: validate against §5. Missing required -> 400; type/size/
        # null/enum/empty -> 422 (§4.1).
        serializer = TicketWithTransactionSerializer(data=data)
        serializer.is_valid(raise_exception=False)
        if serializer.errors:
            status_code, body = _classify_validation_errors(serializer.errors)
            log.info("POST /analyze-ticket rejected: %d %s", status_code, serializer.errors)
            return Response(body, status=status_code)

        # Phase 3 (200): analysis pipeline — not implemented yet.
        log.info("POST /analyze-ticket accepted ticket_id=%s", serializer.validated_data["ticket_id"])
        return Response(
            {"detail": "Analysis pipeline not implemented."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )