"""Live 200-path tests — real normalizer, real LLM, real §6 response.

Skipped automatically when the normalizer service is not reachable (or when
SKIP_LIVE_200_TESTS=1), via _normalizer.skipUnlessNormalizer. The mocked unit
tests in test_pipeline.py cover the deterministic logic without the normalizer;
this class covers the end-to-end 200 path through the full stack.

The 10 public sample cases (from docs/SUST_Preli_Sample_Cases.json) are embedded
here so the test is self-contained (the docs/ file lives at the repo root and is
not copied into the backend image). Per the sample pack's "functionally
equivalent" rule we assert the exact-match fields: relevant_transaction_id,
evidence_verdict, case_type, department, plus that customer_reply clears the
safety rail. Severity/confidence are soft ("comparable") and not asserted.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.test import APITestCase

from ._normalizer import skipUnlessNormalizer
from tickets.safety import is_safe

ENDPOINT = "/analyze-ticket"


def _txn(tid, ts, ttype, amount, cp, st):
    return {
        "transaction_id": tid,
        "timestamp": ts,
        "type": ttype,
        "amount": amount,
        "counterparty": cp,
        "status": st,
    }


# (id, input, expected exact-match fields) for the 10 public sample cases.
SAMPLE_CASES = [
    {
        "id": "SAMPLE-01",
        "input": {
            "ticket_id": "TKT-001",
            "complaint": "I sent 5000 taka to a wrong number around 2pm today. The number was supposed to be 01712345678 but I think I typed it wrong. The person isn't responding to my call. Please help me get my money back.",
            "language": "en", "channel": "in_app_chat", "user_type": "customer",
            "campaign_context": "boishakh_bonanza_day_1",
            "transaction_history": [
                _txn("TXN-9101", "2026-04-14T14:08:22Z", "transfer", 5000, "+8801719876543", "completed"),
                _txn("TXN-9087", "2026-04-13T18:12:00Z", "cash_in", 10000, "AGENT-512", "completed"),
            ],
        },
        "expected": {"relevant_transaction_id": "TXN-9101", "evidence_verdict": "consistent",
                      "case_type": "wrong_transfer", "department": "dispute_resolution"},
    },
    {
        "id": "SAMPLE-02",
        "input": {
            "ticket_id": "TKT-002",
            "complaint": "I sent 2000 to the wrong person by mistake. Please reverse it.",
            "language": "en", "channel": "in_app_chat", "user_type": "customer",
            "transaction_history": [
                _txn("TXN-9202", "2026-04-14T11:30:00Z", "transfer", 2000, "+8801812345678", "completed"),
                _txn("TXN-9180", "2026-04-10T09:15:00Z", "transfer", 2500, "+8801812345678", "completed"),
                _txn("TXN-9145", "2026-04-05T17:45:00Z", "transfer", 1500, "+8801812345678", "completed"),
            ],
        },
        "expected": {"relevant_transaction_id": "TXN-9202", "evidence_verdict": "inconsistent",
                      "case_type": "wrong_transfer", "department": "dispute_resolution"},
    },
    {
        "id": "SAMPLE-03",
        "input": {
            "ticket_id": "TKT-003",
            "complaint": "I tried to pay 1200 taka for my mobile recharge but the app showed failed. But my balance was deducted! Please refund my money.",
            "language": "en", "channel": "in_app_chat", "user_type": "customer",
            "transaction_history": [
                _txn("TXN-9301", "2026-04-14T16:00:00Z", "payment", 1200, "MERCHANT-MOBILE-OP", "failed"),
            ],
        },
        "expected": {"relevant_transaction_id": "TXN-9301", "evidence_verdict": "consistent",
                      "case_type": "payment_failed", "department": "payments_ops"},
    },
    {
        "id": "SAMPLE-04",
        "input": {
            "ticket_id": "TKT-004",
            "complaint": "I paid 500 to a merchant for a product but I changed my mind and don't want it anymore. Please refund my 500 taka.",
            "language": "en", "channel": "in_app_chat", "user_type": "customer",
            "transaction_history": [
                _txn("TXN-9401", "2026-04-14T13:00:00Z", "payment", 500, "MERCHANT-7821", "completed"),
            ],
        },
        "expected": {"relevant_transaction_id": "TXN-9401", "evidence_verdict": "consistent",
                      "case_type": "refund_request", "department": "customer_support"},
    },
    {
        "id": "SAMPLE-05",
        "input": {
            "ticket_id": "TKT-005",
            "complaint": "Someone called me saying they are from bKash and asked for my OTP. They said my account will be blocked if I don't share it. Is this real? I haven't shared anything yet.",
            "language": "en", "channel": "call_center", "user_type": "customer",
            "transaction_history": [],
        },
        "expected": {"relevant_transaction_id": None, "evidence_verdict": "insufficient_data",
                      "case_type": "phishing_or_social_engineering", "department": "fraud_risk"},
    },
    {
        "id": "SAMPLE-06",
        "input": {
            "ticket_id": "TKT-006",
            "complaint": "Something is wrong with my money. Please check.",
            "language": "en", "channel": "in_app_chat", "user_type": "customer",
            "transaction_history": [
                _txn("TXN-9601", "2026-04-13T10:00:00Z", "cash_in", 3000, "AGENT-220", "completed"),
                _txn("TXN-9602", "2026-04-12T15:30:00Z", "transfer", 800, "+8801911223344", "completed"),
            ],
        },
        "expected": {"relevant_transaction_id": None, "evidence_verdict": "insufficient_data",
                      "case_type": "other", "department": "customer_support"},
    },
    {
        "id": "SAMPLE-07",
        "input": {
            "ticket_id": "TKT-007",
            "complaint": "আমি আজ সকালে এজেন্টের কাছে ২০০০ টাকা ক্যাশ ইন করেছি কিন্তু আমার ব্যালেন্সে টাকা আসেনি। এজেন্ট বলছে টাকা পাঠিয়েছে কিন্তু আমি দেখছি না।",
            "language": "bn", "channel": "call_center", "user_type": "customer",
            "transaction_history": [
                _txn("TXN-9701", "2026-04-14T09:30:00Z", "cash_in", 2000, "AGENT-318", "pending"),
            ],
        },
        "expected": {"relevant_transaction_id": "TXN-9701", "evidence_verdict": "consistent",
                      "case_type": "agent_cash_in_issue", "department": "agent_operations"},
    },
    {
        "id": "SAMPLE-08",
        "input": {
            "ticket_id": "TKT-008",
            "complaint": "I sent 1000 to my brother yesterday but he says he didn't get it. Please check.",
            "language": "en", "channel": "in_app_chat", "user_type": "customer",
            "transaction_history": [
                _txn("TXN-9801", "2026-04-13T11:20:00Z", "transfer", 1000, "+8801712001122", "completed"),
                _txn("TXN-9802", "2026-04-13T19:45:00Z", "transfer", 1000, "+8801812334455", "completed"),
                _txn("TXN-9803", "2026-04-13T20:10:00Z", "transfer", 1000, "+8801712001122", "failed"),
            ],
        },
        "expected": {"relevant_transaction_id": None, "evidence_verdict": "insufficient_data",
                      "case_type": "wrong_transfer", "department": "dispute_resolution"},
    },
    {
        "id": "SAMPLE-09",
        "input": {
            "ticket_id": "TKT-009",
            "complaint": "I am a merchant. My yesterday's sales of 15000 taka have not been settled to my account. Settlement usually happens by 11am next day. Please check.",
            "language": "en", "channel": "merchant_portal", "user_type": "merchant",
            "transaction_history": [
                _txn("TXN-9901", "2026-04-13T18:00:00Z", "settlement", 15000, "MERCHANT-SELF", "pending"),
            ],
        },
        "expected": {"relevant_transaction_id": "TXN-9901", "evidence_verdict": "consistent",
                      "case_type": "merchant_settlement_delay", "department": "merchant_operations"},
    },
    {
        "id": "SAMPLE-10",
        "input": {
            "ticket_id": "TKT-010",
            "complaint": "I paid my electricity bill 850 taka but it deducted twice from my account. Please check, I only paid once.",
            "language": "en", "channel": "in_app_chat", "user_type": "customer",
            "transaction_history": [
                _txn("TXN-10001", "2026-04-14T08:15:30Z", "payment", 850, "BILLER-DESCO", "completed"),
                _txn("TXN-10002", "2026-04-14T08:15:42Z", "payment", 850, "BILLER-DESCO", "completed"),
            ],
        },
        "expected": {"relevant_transaction_id": "TXN-10002", "evidence_verdict": "consistent",
                      "case_type": "duplicate_payment", "department": "payments_ops"},
    },
]


@skipUnlessNormalizer
class AnalyzeTicket200LiveTests(APITestCase):
    """End-to-end 200 path through the real normalizer + LLM. Skipped unless the
    normalizer is reachable (or SKIP_LIVE_200_TESTS=1 forces a skip)."""

    def test_sample_cases_200_path(self):
        for case in SAMPLE_CASES:
            with self.subTest(case=case["id"]):
                r = self.client.post(ENDPOINT, case["input"], format="json")
                self.assertEqual(r.status_code, status.HTTP_200_OK,
                                 f"{case['id']}: expected 200, got {r.status_code}")
                b = r.json()
                self.assertEqual(b["ticket_id"], case["input"]["ticket_id"])
                exp = case["expected"]
                self.assertEqual(b["relevant_transaction_id"], exp["relevant_transaction_id"])
                self.assertEqual(b["evidence_verdict"], exp["evidence_verdict"])
                self.assertEqual(b["case_type"], exp["case_type"])
                self.assertEqual(b["department"], exp["department"])
                self.assertTrue(
                    is_safe(b["customer_reply"]),
                    f"{case['id']}: unsafe customer_reply: {b['customer_reply']}",
                )