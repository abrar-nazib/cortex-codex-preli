"""Stage 5 — deterministic routing rules (backend, the authority).

department and human_review_required are NEVER taken from the LLM. department is
a pure function of case_type (§7.2 routing map); human_review_required is a rule
over case_type / evidence_verdict / whether a transaction was matched / amount.

The rule is derived from the Problem Statement §6.1 ("True for disputes,
suspicious cases, high value cases, or ambiguous evidence") + §2 ("must
escalate ambiguous or high risk cases for human review"), calibrated against the
public sample cases' expected_output values:

  - wrong_transfer with a matched transaction       -> review (dispute path)
  - wrong_transfer with no match (insufficient)     -> no review (ask detail)
  - duplicate_payment / phishing / agent_cash_in    -> review
  - inconsistent evidence (data contradicts claim)   -> review (never rubber-stamp)
  - contested refund_request                        -> review (caught by the
                                                      inconsistent rule: a
                                                      merchant disputing the
                                                      customer's refund claim
                                                      shows up as inconsistent)
  - high-value at-risk money movement               -> review (§6.1 "high value
                                                      cases"); scoped to
                                                      money-at-risk types so
                                                      routine merchant settlement
                                                      (sample 9, 15000 BDT) does
                                                      NOT trigger.
  - payment_failed / refund_request / merchant_settlement_delay / other / vague
    with consistent evidence and small amount        -> no review (routine flow
                                                      or clarification)

Severity is deliberately NOT a trigger: sample 3 is severity=high with
review=false, sample 9 is amount=15000 with review=false. Routing follows case
type + evidence + (scoped) amount, not severity.
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

# High-value trigger (§6.1 "high value cases"). Scoped to money-at-risk movement
# types only — merchant_settlement_delay (routine even when large, sample 9) and
# other/vague are excluded so a big routine settlement does not auto-escalate.
HIGH_VALUE_BDT = 10000.0
_HIGH_VALUE_AT_RISK_TYPES = {
    "wrong_transfer",
    "duplicate_payment",
    "agent_cash_in_issue",
    "payment_failed",
    "refund_request",
}


def department_for(case_type: str) -> str:
    return DEPARTMENT_MAP.get(case_type, "customer_support")


def human_review_required(*, case_type: str, severity: str,
                          evidence_verdict: str, amount: float | None,
                          relevant_transaction_id: str | None) -> bool:
    """Rule-based escalation. Conservative: when in doubt, review.

    `severity` is accepted (caller has it) but intentionally not a trigger — see
    the module docstring for the sample-derived rationale.
    """
    if evidence_verdict == "inconsistent":
        return True
    if case_type == "wrong_transfer" and relevant_transaction_id is not None:
        return True
    if case_type in _REVIEW_CASE_TYPES:
        return True
    if (amount is not None and amount >= HIGH_VALUE_BDT
            and case_type in _HIGH_VALUE_AT_RISK_TYPES):
        return True
    return False