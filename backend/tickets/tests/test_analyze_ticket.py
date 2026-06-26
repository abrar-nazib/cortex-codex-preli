"""POST /analyze-ticket — 400/422 contract tests (Problem Statement §4.1).

The 200 analysis path is not implemented yet (returns 501), so these tests pin
down the error contract only:

  400  malformed JSON body, non-object body, or missing required field
       (ticket_id / complaint, including nested required fields).
  422  schema-valid JSON but a bad value: wrong type, too long, null where
       non-null, empty complaint, enum value outside §7 taxonomy, etc.

The normalizer is never reached (no 200 path), so these are network-free and
run against the compose postgres test DB.
"""
from rest_framework import status
from rest_framework.test import APITestCase

ENDPOINT = "/analyze-ticket"

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

    # ── valid payload escapes the error branches (200 not implemented -> 501)
    def test_valid_payload_not_400_or_422(self):
        r = self._post(VALID_PAYLOAD)
        self.assertNotIn(r.status_code, (status.HTTP_400_BAD_REQUEST,
                                         status.HTTP_422_UNPROCESSABLE_ENTITY))