"""POST /analyze-ticket — 400/422/500 contract tests (Problem Statement §4.1).

The 200 analysis path is now implemented (delegates to tickets.pipeline, which
calls the normalizer). The 400/422 tests never reach the pipeline; the valid-
payload + still-reachable tests mock the normalizer so they stay network-free
and deterministic. The 200-path logic itself (enum coercion, safety rail,
routing, fallback) is covered in test_pipeline.py.

  400  malformed JSON body, non-object body, or missing required field
       (ticket_id / complaint, including nested required fields).
  422  schema-valid JSON but a bad value: wrong type, too long, null where
       non-null, empty complaint, enum value outside §7 taxonomy, etc.
  500  internal error -> sanitized {"detail": "Internal server error."}.
"""
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from tickets.views import AnalyzeTicketView

ENDPOINT = "/analyze-ticket"

# Canned normalizer reply used to keep the valid-payload / still-reachable
# tests network-free (no real LLM call). Mirrors the AnalyzeResult shape.
CANNED_NORMALIZER_RESULT = {
    "clean_complaint": "I sent 5000 taka to a wrong number around 2pm today.",
    "relevant_transaction_id": "TXN-9101",
    "evidence_verdict": "consistent",
    "case_type": "wrong_transfer",
    "severity": "high",
    "agent_summary": "Customer reports a wrong transfer of 5000 BDT via TXN-9101.",
    "recommended_next_action": "Verify TXN-9101 and initiate the wrong-transfer dispute workflow.",
    "customer_reply": "We have noted your concern about TXN-9101. Please do not share your PIN or OTP with anyone. Our dispute team will review the case.",
    "reason_codes": ["wrong_transfer", "transaction_match"],
    "confidence": 0.9,
}


def _mock_normalizer(result=CANNED_NORMALIZER_RESULT):
    """Patch the normalizer client so no HTTP/LLM call happens."""
    return patch("tickets.normalizer_client.analyze", return_value=result)

# A minimal valid payload; only used as the base to corrupt for 422 cases and
# to assert the valid case escapes the 400/422 branches (returns 501).
VALID_PAYLOAD = {
    "ticket_id": "TKT-001",
    "complaint": "I sent 5000 taka to a wrong number around 2pm today.",
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
}


def _txn(**overrides):
    txn = {
        "transaction_id": "TXN-9101",
        "timestamp": "2026-04-14T14:08:22Z",
        "type": "transfer",
        "amount": 5000,
        "counterparty": "+8801719876543",
        "status": "completed",
    }
    txn.update(overrides)
    return txn


class AnalyzeTicket400Tests(APITestCase):
    """§4.1 400 — malformed input / missing required fields."""

    def test_malformed_json_returns_400(self):
        # Raw invalid JSON body with application/json content type.
        r = self.client.post(ENDPOINT, data="{not valid json", content_type="application/json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", r.json())

    def test_empty_body_returns_400(self):
        r = self.client.post(ENDPOINT, data="", content_type="application/json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_object_body_returns_400(self):
        # Valid JSON but not an object — the contract is a JSON object.
        r = self.client.post(ENDPOINT, data="[1, 2, 3]", content_type="application/json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_ticket_id_returns_400(self):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "ticket_id"}
        r = self.client.post(ENDPOINT, payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ticket_id", r.json())

    def test_missing_complaint_returns_400(self):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "complaint"}
        r = self.client.post(ENDPOINT, payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("complaint", r.json())

    def test_empty_object_returns_400(self):
        r = self.client.post(ENDPOINT, {}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        body = r.json()
        # Both required fields flagged.
        self.assertIn("ticket_id", body)
        self.assertIn("complaint", body)

    def test_nested_missing_required_returns_400(self):
        # transaction_history entry missing `amount` — a nested required field.
        payload = {
            "ticket_id": "TKT-001",
            "complaint": "complaint text",
            "transaction_history": [
                {
                    "transaction_id": "TXN-9101",
                    "timestamp": "2026-04-14T14:08:22Z",
                    "type": "transfer",
                    # amount omitted
                    "counterparty": "+8801719876543",
                    "status": "completed",
                }
            ],
        }
        r = self.client.post(ENDPOINT, payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


class AnalyzeTicket422Tests(APITestCase):
    """§4.1 422 — schema-valid JSON but a semantically/type-invalid value."""

    def _post(self, payload):
        return self.client.post(ENDPOINT, payload, format="json")

    # ── complaint (empty / null) ─────────────────────────────────────────────
    def test_empty_complaint_returns_422(self):
        r = self._post({"ticket_id": "TKT-001", "complaint": "   "})
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("complaint", r.json())

    def test_null_complaint_returns_422(self):
        r = self._post({"ticket_id": "TKT-001", "complaint": None})
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("complaint", r.json())

    # ── ticket_id (null / size) ───────────────────────────────────────────────
    def test_null_ticket_id_returns_422(self):
        r = self._post({"ticket_id": None, "complaint": "valid complaint"})
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("ticket_id", r.json())

    def test_ticket_id_too_long_returns_422(self):
        r = self._post({"ticket_id": "X" * 65, "complaint": "valid complaint"})
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("ticket_id", r.json())

    # ── enum violations (§7 taxonomy — exact match required) ──────────────────
    def test_invalid_language_returns_422(self):
        r = self._post(
            {"ticket_id": "TKT-001", "complaint": "c", "language": "english"}
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("language", r.json())

    def test_invalid_channel_returns_422(self):
        r = self._post(
            {"ticket_id": "TKT-001", "complaint": "c", "channel": "whatsapp"}
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("channel", r.json())

    def test_invalid_user_type_returns_422(self):
        r = self._post(
            {"ticket_id": "TKT-001", "complaint": "c", "user_type": "admin"}
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("user_type", r.json())

    # ── transaction_history type / enum / size / null ────────────────────────
    def test_transaction_history_wrong_type_returns_422(self):
        # Should be a list of objects; a string is a type violation.
        r = self._post(
            {"ticket_id": "TKT-001", "complaint": "c", "transaction_history": "TXN-9101"}
        )
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertIn("transaction_history", r.json())

    def test_transaction_amount_wrong_type_returns_422(self):
        payload = {
            "ticket_id": "TKT-001",
            "complaint": "c",
            "transaction_history": [_txn(amount="not-a-number")],
        }
        r = self._post(payload)
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_transaction_amount_null_returns_422(self):
        payload = {
            "ticket_id": "TKT-001",
            "complaint": "c",
            "transaction_history": [_txn(amount=None)],
        }
        r = self._post(payload)
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_transaction_invalid_type_enum_returns_422(self):
        payload = {
            "ticket_id": "TKT-001",
            "complaint": "c",
            "transaction_history": [_txn(type="withdrawal")],
        }
        r = self._post(payload)
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_transaction_invalid_status_enum_returns_422(self):
        payload = {
            "ticket_id": "TKT-001",
            "complaint": "c",
            "transaction_history": [_txn(status="cancelled")],
        }
        r = self._post(payload)
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_transaction_id_too_long_returns_422(self):
        payload = {
            "ticket_id": "TKT-001",
            "complaint": "c",
            "transaction_history": [_txn(transaction_id="X" * 65)],
        }
        r = self._post(payload)
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # ── mixed: a required-missing AND a type error -> 422 wins (there is a
    # type error, not purely missing required). ─────────────────────────────
    def test_mixed_required_and_type_error_returns_422(self):
        # complaint missing (required) AND language invalid (type/enum).
        r = self._post({"ticket_id": "TKT-001", "language": "english"})
        self.assertEqual(r.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    # ── valid payload escapes the error branches (mocked normalizer -> 200)
    def test_valid_payload_not_400_or_422(self):
        with _mock_normalizer():
            r = self._post(VALID_PAYLOAD)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        body = r.json()
        self.assertEqual(body["ticket_id"], "TKT-001")
        self.assertEqual(body["case_type"], "wrong_transfer")
        self.assertEqual(body["department"], "dispute_resolution")


class AnalyzeTicket500Tests(APITestCase):
    """§4.1 500 — an internal error must return a non-sensitive JSON message.

    The Problem Statement §4.1 + the rubric "Secret handling" require: the 500
    body includes a non-sensitive error message and must NOT expose stack
    traces, tokens, or secrets. The view wraps the analysis step and returns a
    sanitized ``{"detail": "Internal server error."}``, logging the real
    exception server-side only.

    We force an internal error by patching ``AnalyzeTicketView._analyze`` to
    raise a RuntimeError whose message deliberately contains a fake API key
    and a fake traceback — then assert none of that leaks into the response.
    """

    FAKE_SECRET = "sk-live-OPENROUTER_API_KEY-1234567890abcdef"
    FAKE_TRACEBACK = (
        'Traceback (most recent call last):\n'
        '  File "/app/tickets/pipeline.py", line 42, in _analyze\n'
        '    raise RuntimeError("boom")\n'
        'RuntimeError: connection reset'
    )

    def _force_internal_error(self):
        msg = f"boom: {self.FAKE_SECRET}\n{self.FAKE_TRACEBACK}"
        return patch.object(
            AnalyzeTicketView, "_analyze", side_effect=RuntimeError(msg)
        )

    def test_internal_error_returns_500_json(self):
        with self._force_internal_error():
            r = self.client.post(ENDPOINT, VALID_PAYLOAD, format="json")
        self.assertEqual(r.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("application/json", r["content-type"])
        body = r.json()
        self.assertIn("detail", body)
        # Generic, non-sensitive message (not the exception text).
        self.assertNotIn("boom", body["detail"])

    def test_internal_error_body_has_no_secret(self):
        with self._force_internal_error():
            r = self.client.post(ENDPOINT, VALID_PAYLOAD, format="json")
        text = r.content.decode("utf-8", errors="replace")
        # The planted secret must not appear verbatim.
        self.assertNotIn(self.FAKE_SECRET, text)
        # And none of these sensitive token markers.
        for marker in ("api_key", "API_KEY", "sk-live", "Bearer ", "password"):
            self.assertNotIn(marker, text)

    def test_internal_error_body_has_no_traceback(self):
        with self._force_internal_error():
            r = self.client.post(ENDPOINT, VALID_PAYLOAD, format="json")
        text = r.content.decode("utf-8", errors="replace")
        for marker in (
            "Traceback",
            "most recent call last",
            "/app/",
            '.py", line',
            "ErrorDetail",
            "RuntimeError",
        ):
            self.assertNotIn(marker, text)

    def test_internal_error_still_reachable_after_one_failure(self):
        # A 500 must not crash the process — the next request still works.
        with self._force_internal_error():
            self.client.post(ENDPOINT, VALID_PAYLOAD, format="json")
        with _mock_normalizer():
            r = self.client.post(ENDPOINT, VALID_PAYLOAD, format="json")
        # Process survived: a clean JSON 200 (no leaked 500, no crash).
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn("application/json", r["content-type"])

    def test_internal_error_log_has_no_secret_or_traceback(self):
        # Rubric "Secret handling": no stack traces / tokens / secrets in logs
        # either, not just responses. The view logs only a sanitized line
        # (ticket_id + exception class name) — assert the planted secret and
        # traceback never reach the log stream.
        with self.assertLogs("backend", level="ERROR") as captured:
            with self._force_internal_error():
                self.client.post(ENDPOINT, VALID_PAYLOAD, format="json")

        logged = "\n".join(captured.output)
        self.assertNotIn(self.FAKE_SECRET, logged)
        for marker in ("api_key", "API_KEY", "sk-live", "Traceback",
                        "most recent call last", "RuntimeError: boom"):
            self.assertNotIn(marker, logged)
        # But the sanitized error line is present (proves it was logged).
        self.assertIn("internal error", logged)
        self.assertIn("exc=RuntimeError", logged)