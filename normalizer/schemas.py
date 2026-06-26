"""Pydantic schemas for the normalizer reasoning contract.

Mirrors the Problem Statement enums (§7) so the LLM is constrained to the exact
taxonomy. These enums must match backend/tickets/serializers.py exactly — the
backend re-validates every field it receives from here (the normalizer is an
untrusted upstream from the backend's trust boundary).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ─── Enum taxonomy (§7 + §5.2 + §6) — exact, lowercase ─────────────────────────
Language = Literal["en", "bn", "mixed"]
Channel = Literal["in_app_chat", "call_center", "email", "merchant_portal", "field_agent"]
UserType = Literal["customer", "merchant", "agent", "unknown"]
TxnType = Literal["transfer", "payment", "cash_in", "cash_out", "settlement", "refund"]
TxnStatus = Literal["completed", "failed", "pending", "reversed"]
EvidenceVerdict = Literal["consistent", "inconsistent", "insufficient_data"]
CaseType = Literal[
    "wrong_transfer",
    "payment_failed",
    "refund_request",
    "duplicate_payment",
    "merchant_settlement_delay",
    "agent_cash_in_issue",
    "phishing_or_social_engineering",
    "other",
]
Severity = Literal["low", "medium", "high", "critical"]
Department = Literal[
    "customer_support",
    "dispute_resolution",
    "payments_ops",
    "merchant_operations",
    "agent_operations",
    "fraud_risk",
]

ENUMS = {
    "evidence_verdict": ("consistent", "inconsistent", "insufficient_data"),
    "case_type": (
        "wrong_transfer", "payment_failed", "refund_request", "duplicate_payment",
        "merchant_settlement_delay", "agent_cash_in_issue",
        "phishing_or_social_engineering", "other",
    ),
    "severity": ("low", "medium", "high", "critical"),
}


# ─── Request (backend -> normalizer) ─────────────────────────────────────────
class TxnIn(BaseModel):
    transaction_id: str
    timestamp: str
    type: TxnType
    amount: float
    counterparty: str
    status: TxnStatus


class AnalyzeRequest(BaseModel):
    ticket_id: str
    complaint: str
    language: Optional[Language] = None
    channel: Optional[Channel] = None
    user_type: Optional[UserType] = None
    campaign_context: Optional[str] = None
    transaction_history: list[TxnIn] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


# ─── Internal pipeline stages ────────────────────────────────────────────────
class NormalizedComplaint(BaseModel):
    clean_complaint: str
    language_detected: Language
    # entities preserved verbatim (amounts, phone/merchant/agent ids, txn ids)
    preserved_entities: dict = Field(default_factory=dict)


class ScoredTxn(BaseModel):
    transaction_id: str
    timestamp: str
    type: TxnType
    amount: float
    counterparty: str
    status: TxnStatus
    score: float
    reasons: list[str] = Field(default_factory=list)


class EvidencePass(BaseModel):
    """Deterministic stage-2 output handed to the LLM as grounding."""

    scored: list[ScoredTxn]
    # signals the LLM should weigh when assigning evidence_verdict
    signals: list[str] = Field(default_factory=list)
    top_transaction_id: Optional[str] = None
    ambiguous: bool = False


# ─── Response (normalizer -> backend) ────────────────────────────────────────
class AnalyzeResult(BaseModel):
    """Stage-3 LLM output. The backend applies safety rails + routing rules
    + enum re-validation on top of this; nothing here is trusted blindly."""

    clean_complaint: str
    relevant_transaction_id: Optional[str] = None
    evidence_verdict: EvidenceVerdict
    case_type: CaseType
    severity: Severity
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Internal-only (not part of §6): the deterministic evidence signals + top
    # candidate from stage 2, passed through so the backend can apply
    # deterministic overrides. The backend strips these before emitting §6.
    signals: list[str] = Field(default_factory=list)
    top_transaction_id: Optional[str] = None


class RephraseRequest(BaseModel):
    """Backend asks the normalizer to rephrase text that tripped a safety rail."""

    texts: dict[str, str]  # field_name -> offending text
    violations: list[str]  # human-readable violation descriptions
    language: Language = "en"
    user_type: Optional[UserType] = None
    complaint_context: str = ""


class RephraseResult(BaseModel):
    rephrased: dict[str, str]  # field_name -> safe text