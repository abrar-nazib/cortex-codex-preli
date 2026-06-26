"""Pipeline logic tests (network-free) for tickets.pipeline + routing + safety.

The normalizer is mocked everywhere so these run fast and deterministically —
no real LLM, no HTTP. Covers the parts the backend owns as authority: the
§7.2 department map, the human-review rule, enum coercion, relevant_transaction_
id coercion, the deterministic safety rail + rephrase loop, and the normalizer-
unreachable fallback. The 400/422/500 HTTP contract is in test_analyze_ticket.py.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from tickets import normalizer_client
from tickets.pipeline import build_response
from tickets.routing import department_for, human_review_required
from tickets.safety import find_safety_violations, is_safe

ENDPOINT = "/analyze-ticket"

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


def _result(**over):
    base = {
        "clean_complaint": "I sent 5000 taka to a wrong number around 2pm today.",
        "relevant_transaction_id": "TXN-9101",
        "evidence_verdict": "consistent",
        "case_type": "wrong_transfer",
        "severity": "high",
        "agent_summary": "Customer reports a wrong transfer of 5000 BDT via TXN-9101.",
        "recommended_next_action": "Verify TXN-9101 and initiate the dispute workflow.",
        "customer_reply": "We have noted your concern about TXN-9101. Please do not share your PIN or OTP with anyone. Our dispute team will review the case.",
        "reason_codes": ["wrong_transfer", "transaction_match"],
        "confidence": 0.9,
    }
    base.update(over)
    return base


# ─── routing rules (pure) ───────────────────────────────────────────────────
class RoutingTests(TestCase):
    def test_department_map_covers_every_case_type(self):
        expected = {
            "wrong_transfer": "dispute_resolution",
            "payment_failed": "payments_ops",
            "refund_request": "customer_support",
            "duplicate_payment": "payments_ops",
            "merchant_settlement_delay": "merchant_operations",
            "agent_cash_in_issue": "agent_operations",
            "phishing_or_social_engineering": "fraud_risk",
            "other": "customer_support",
        }
        for case_type, dept in expected.items():
            self.assertEqual(department_for(case_type), dept, case_type)

    def test_department_unknown_case_falls_back(self):
        self.assertEqual(department_for("nonsense"), "customer_support")

    def test_human_review_always_for_disputes_and_fraud(self):
        for ct in ("wrong_transfer", "duplicate_payment", "phishing_or_social_engineering"):
            self.assertTrue(human_review_required(
                case_type=ct, severity="low", evidence_verdict="consistent",
                amount=100.0, relevant_transaction_id="TXN-1"), ct)

    def test_human_review_for_inconsistent_evidence(self):
        self.assertTrue(human_review_required(
            case_type="wrong_transfer", severity="medium",
            evidence_verdict="inconsistent", amount=2000.0,
            relevant_transaction_id="TXN-1"))

    def test_no_human_review_for_high_severity_payment_failed(self):
        # SAMPLE-03: severity=high but review=false — severity is not a trigger.
        self.assertFalse(human_review_required(
            case_type="payment_failed", severity="high",
            evidence_verdict="consistent", amount=1200.0,
            relevant_transaction_id="TXN-1"))

    def test_no_human_review_for_high_value_merchant_settlement(self):
        # SAMPLE-09: amount=15000 but review=false — amount is not a trigger.
        self.assertFalse(human_review_required(
            case_type="merchant_settlement_delay", severity="medium",
            evidence_verdict="consistent", amount=15000.0,
            relevant_transaction_id="TXN-1"))

    def test_no_human_review_for_simple_refund(self):
        # SAMPLE-04 style: low severity, consistent, small amount, no dispute.
        self.assertFalse(human_review_required(
            case_type="refund_request", severity="low",
            evidence_verdict="consistent", amount=500.0,
            relevant_transaction_id="TXN-1"))

    def test_no_human_review_for_vague_insufficient(self):
        # SAMPLE-06 style: no matched txn, insufficient, low.
        self.assertFalse(human_review_required(
            case_type="other", severity="low",
            evidence_verdict="insufficient_data", amount=None,
            relevant_transaction_id=None))

    def test_no_human_review_for_wrong_transfer_without_match(self):
        # SAMPLE-08: wrong_transfer but no matched txn -> no review, ask detail.
        self.assertFalse(human_review_required(
            case_type="wrong_transfer", severity="medium",
            evidence_verdict="insufficient_data", amount=1000.0,
            relevant_transaction_id=None))

    # ── high-value trigger (§6.1 "high value cases", scoped to at-risk types) ─
    def test_high_value_payment_failed_triggers_review(self):
        # doc: high value -> review. No sample; doc-faithful extrapolation.
        self.assertTrue(human_review_required(
            case_type="payment_failed", severity="medium",
            evidence_verdict="consistent", amount=12000.0,
            relevant_transaction_id="TXN-1"))

    def test_high_value_refund_request_triggers_review(self):
        self.assertTrue(human_review_required(
            case_type="refund_request", severity="medium",
            evidence_verdict="consistent", amount=12000.0,
            relevant_transaction_id="TXN-1"))

    def test_high_value_wrong_transfer_no_match_triggers_review(self):
        # high-value + no matched txn: money at risk + can't confirm -> review
        # (overrides the clarify path a small-value no-match would take).
        self.assertTrue(human_review_required(
            case_type="wrong_transfer", severity="medium",
            evidence_verdict="insufficient_data", amount=12000.0,
            relevant_transaction_id=None))

    def test_high_value_merchant_settlement_does_not_trigger(self):
        # SAMPLE-09: 15000 BDT but routine -> NOT reviewed. merchant_settlement
        # is excluded from the high-value at-risk scope.
        self.assertFalse(human_review_required(
            case_type="merchant_settlement_delay", severity="medium",
            evidence_verdict="consistent", amount=15000.0,
            relevant_transaction_id="TXN-1"))

    def test_high_value_other_vague_does_not_trigger(self):
        # "other"/vague excluded from high-value scope -> clarify, not review.
        self.assertFalse(human_review_required(
            case_type="other", severity="low",
            evidence_verdict="insufficient_data", amount=12000.0,
            relevant_transaction_id=None))

    def test_below_threshold_payment_failed_no_review(self):
        # 9999 < 10000 threshold, consistent -> routine auto-reversal, no review.
        self.assertFalse(human_review_required(
            case_type="payment_failed", severity="high",
            evidence_verdict="consistent", amount=9999.0,
            relevant_transaction_id="TXN-1"))

    def test_contested_refund_via_inconsistent_triggers_review(self):
        # §7.2 contested refund -> dispute_resolution -> review. Contested shows
        # up as inconsistent evidence (merchant disputes the claim).
        self.assertTrue(human_review_required(
            case_type="refund_request", severity="low",
            evidence_verdict="inconsistent", amount=500.0,
            relevant_transaction_id="TXN-1"))


# ─── safety rail (pure) ─────────────────────────────────────────────────────
class SafetyRailTests(TestCase):
    def test_safe_sample_replies_pass(self):
        # All ten sample customer_reply strings must clear the rail.
        replies = [
            "We have noted your concern about transaction TXN-9101. Please do not share your PIN or OTP with anyone. Our dispute team will review the case and contact you through official support channels.",
            "We have received your request regarding transaction TXN-9202. Please do not share your PIN or OTP with anyone. Our dispute team will review the case carefully and contact you through official support channels.",
            "We have noted that transaction TXN-9301 may have caused an unexpected balance deduction. Our payments team will review the case and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone.",
            "Thank you for reaching out before sharing any information. We never ask for your PIN, OTP, or password under any circumstances. Please do not share these with anyone, even if they claim to be from us. Our fraud team has been notified of this incident.",
            "Thank you for reaching out. To help you faster, please share the transaction ID, the amount involved, and a short description of what went wrong. Please do not share your PIN or OTP with anyone.",
            "Thank you for reaching out. We see multiple transactions of 1000 BDT on that date. Could you share your brother's number so we can identify the right transaction? Please do not share your PIN or OTP with anyone.",
        ]
        for r in replies:
            self.assertTrue(is_safe(r), f"false positive: {r[:60]}")

    def test_blocks_credential_request(self):
        self.assertFalse(is_safe("Please share your OTP to verify."))
        self.assertFalse(is_safe("Enter your PIN to continue."))
        self.assertFalse(is_safe("Tell me your password."))

    def test_blocks_refund_promise(self):
        self.assertFalse(is_safe("We will refund your 5000 taka immediately."))
        self.assertFalse(is_safe("We will reverse the transaction now."))
        self.assertFalse(is_safe("We will unblock your account right away."))

    def test_allows_eligible_hedge(self):
        self.assertTrue(is_safe("any eligible amount will be returned through official channels."))
        self.assertTrue(is_safe("If you are eligible, a refund may be processed per policy."))

    def test_blocks_third_party_contact(self):
        self.assertFalse(is_safe("Please call that number to resolve it."))
        self.assertFalse(is_safe("Contact him directly for the refund."))


# ─── build_response orchestration (mocked normalizer) ───────────────────────
class BuildResponseTests(APITestCase):
    def _post(self, normalizer_result, rephrase_result=None, payload=None):
        payload = payload or VALID_PAYLOAD
        with patch("tickets.normalizer_client.analyze", return_value=normalizer_result):
            rp = patch("tickets.normalizer_client.rephrase", return_value=rephrase_result or {})
            with rp:
                r = self.client.post(ENDPOINT, payload, format="json")
        return r

    def test_happy_path_200_with_overrides(self):
        r = self._post(_result())
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        b = r.json()
        self.assertEqual(b["ticket_id"], "TKT-001")
        self.assertEqual(b["relevant_transaction_id"], "TXN-9101")
        self.assertEqual(b["evidence_verdict"], "consistent")
        self.assertEqual(b["case_type"], "wrong_transfer")
        self.assertEqual(b["department"], "dispute_resolution")  # rule, not LLM
        self.assertTrue(b["human_review_required"])  # wrong_transfer forces review
        self.assertIn("confidence", b)

    def test_coerces_bad_case_type_enum(self):
        r = self._post(_result(case_type="not_a_real_case", severity="low",
                                evidence_verdict="consistent"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        b = r.json()
        self.assertEqual(b["case_type"], "other")  # coerced fallback
        self.assertEqual(b["department"], "customer_support")

    def test_coerces_unknown_transaction_id_to_null(self):
        r = self._post(_result(relevant_transaction_id="TXN-DOES-NOT-EXIST",
                                evidence_verdict="consistent"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        b = r.json()
        self.assertIsNone(b["relevant_transaction_id"])
        self.assertEqual(b["evidence_verdict"], "insufficient_data")

    def test_safety_rephrase_when_unsafe(self):
        unsafe = _result(
            customer_reply="Please share your OTP and we will refund you instantly.",
            recommended_next_action="Ask the customer for their PIN.",
        )
        safe_rephrase = {
            "customer_reply": "Please do not share your OTP with anyone. Our team will review this through official channels.",
            "recommended_next_action": "Verify identity through official channels.",
        }
        r = self._post(unsafe, rephrase_result=safe_rephrase)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        b = r.json()
        # The affirmative credential request must be gone; a negated safety
        # reminder ("do not share your OTP") is fine and expected.
        self.assertNotIn("Please share your OTP", b["customer_reply"])
        self.assertIn("do not share", b["customer_reply"].lower())
        self.assertTrue(is_safe(b["customer_reply"]))
        self.assertTrue(is_safe(b["recommended_next_action"]))

    def test_safety_fail_loud_returns_500_when_still_unsafe(self):
        unsafe = _result(customer_reply="Please share your OTP now.",
                          recommended_next_action="Get the PIN.")
        # rephrase returns the SAME unsafe text -> still unsafe -> SafetyError -> 500
        bad_rephrase = {
            "customer_reply": "Please share your OTP now.",
            "recommended_next_action": "Get the PIN.",
        }
        r = self._post(unsafe, rephrase_result=bad_rephrase)
        self.assertEqual(r.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn("OTP", r.content.decode())

    def test_normalizer_unreachable_returns_200_fallback(self):
        with patch("tickets.normalizer_client.analyze",
                   side_effect=normalizer_client.NormalizerError("down")):
            r = self.client.post(ENDPOINT, VALID_PAYLOAD, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        b = r.json()
        self.assertEqual(b["case_type"], "other")
        self.assertEqual(b["department"], "customer_support")
        self.assertIsNone(b["relevant_transaction_id"])
        self.assertEqual(b["evidence_verdict"], "insufficient_data")
        self.assertTrue(b["human_review_required"])

    def test_department_never_came_from_normalizer(self):
        # The normalizer result has no 'department' key; the backend must set it.
        raw = _result()
        raw.pop("department", None)  # ensure absent
        r = self._post(raw)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.json()["department"], "dispute_resolution")


# ─── sample-case routing sanity (deterministic, no LLM) ────────────────────
class SampleCaseRoutingTests(TestCase):
    """For each public sample case, assert the backend's deterministic routing
    (department + human_review) matches the expected_output. No LLM involved."""

    SAMPLES = [
        ("wrong_transfer", "high", "consistent", 5000.0, "TXN-9101", "dispute_resolution", True),
        ("wrong_transfer", "medium", "inconsistent", 2000.0, "TXN-9202", "dispute_resolution", True),
        ("payment_failed", "high", "consistent", 1200.0, "TXN-9301", "payments_ops", False),
        ("refund_request", "low", "consistent", 500.0, "TXN-9401", "customer_support", False),
        ("phishing_or_social_engineering", "critical", "insufficient_data", None, None, "fraud_risk", True),
        ("other", "low", "insufficient_data", None, None, "customer_support", False),
        ("agent_cash_in_issue", "high", "consistent", 2000.0, "TXN-9701", "agent_operations", True),
        ("wrong_transfer", "medium", "insufficient_data", 1000.0, None, "dispute_resolution", False),
        ("merchant_settlement_delay", "medium", "consistent", 15000.0, "TXN-9901", "merchant_operations", False),
        ("duplicate_payment", "high", "consistent", 850.0, "TXN-10002", "payments_ops", True),
    ]

    def test_routing_matches_expected_output(self):
        for (case, sev, verdict, amount, rel_id, exp_dept, exp_review) in self.SAMPLES:
            with self.subTest(case=case):
                self.assertEqual(department_for(case), exp_dept)
                self.assertEqual(
                    human_review_required(case_type=case, severity=sev,
                                           evidence_verdict=verdict, amount=amount,
                                           relevant_transaction_id=rel_id),
                    exp_review,
                )