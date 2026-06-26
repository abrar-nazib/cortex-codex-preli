"""Stage 5 — deterministic routing rules (backend, the authority).

department and human_review_required are NEVER taken from the LLM. department is
a pure function of case_type (§7.2 routing map); human_review_required is a rule
over case_type / evidence_verdict / whether a transaction was matched. The rule
is derived from the public sample cases' expected_output values:

  - wrong_transfer with a matched transaction       -> review (dispute path)
  - wrong_transfer with no match (insufficient)     -> no review (ask for detail)
  - duplicate_payment / phishing / agent_cash_in    -> review
  - inconsistent evidence                            -> review
  - payment_failed / refund_request / merchant_settlement_delay / other -> no review

Severity and amount are deliberately NOT triggers: sample 3 is severity=high with
review=false, sample 9 is amount=15000 with review=false. Routing follows case
type + evidence, not severity.
"""
from __future__ import annotations

# §7.2 case_type -> department. The LLM picks case_type; the backend owns this
# map so routing is always correct, not model-dependent.
DEPARTMENT_MAP = {
    "wrong_transfer": "dispute_resolution",
    "payment_failed": "payments_ops",
    "refund_request": "customer_support",
    "duplicate_payment": "payments_ops",
    "merchant_settlement_delay": "merchant_operations",
    "agent_cash_in_issue": "agent_operations",
    "phishing_or_social_engineering": "fraud_risk",
    "other": "customer_support",
}

# case types that always force human review (disputes / fraud / agent cash issue).
_REVIEW_CASE_TYPES = {
    "duplicate_payment",
    "phishing_or_social_engineering",
    "agent_cash_in_issue",
}


def department_for(case_type: str) -> str:
    return DEPARTMENT_MAP.get(case_type, "customer_support")


def human_review_required(*, case_type: str, severity: str,
                          evidence_verdict: str, amount: float | None,
                          relevant_transaction_id: str | None) -> bool:
    """Rule-based escalation. Conservative: when in doubt, review.

    `severity` and `amount` are accepted (caller has them) but intentionally not
    used as triggers — see the module docstring for the sample-derived rationale.
    """
    if evidence_verdict == "inconsistent":
        return True
    if case_type == "wrong_transfer" and relevant_transaction_id is not None:
        return True
    if case_type in _REVIEW_CASE_TYPES:
        return True
    return False