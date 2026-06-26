"""Pydantic v2 models for every stage boundary and the public response.

Public surface (request/response, exact field names from spec):
    TransactionHistoryEntry, TicketRequest, TicketResponse

Inter-stage contracts (pipeline internal):
    Stage1Output, Stage2Output, Stage3Output, Stage4Output
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Public: request
# ---------------------------------------------------------------------------

class TransactionHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    transaction_id: str
    timestamp: str
    type: str
    amount: float
    counterparty: str
    status: str


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    complaint: str
    language: Optional[str] = None
    channel: Optional[str] = None
    user_type: Optional[str] = None
    campaign_context: Optional[str] = None
    transaction_history: Optional[List[TransactionHistoryEntry]] = Field(default_factory=list)
    metadata: Optional[dict] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public: response
# ---------------------------------------------------------------------------

class TicketResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    relevant_transaction_id: Optional[str] = None
    evidence_verdict: str
    case_type: str
    severity: str
    department: str
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: Optional[float] = None
    reason_codes: Optional[List[str]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal: Stage 1 (cleaner) output
# ---------------------------------------------------------------------------

class Stage1Output(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cleaned_complaint: str
    detected_language: str                       # en | bn | mixed
    extracted_keywords: List[str]
    possible_issues: List[str]
    claimed_case_type: Optional[str] = None
    amount_in_complaint: Optional[float] = None
    time_reference: Optional[str] = None
    injection_detected: bool
    was_truncated: bool
    original_length: int


# ---------------------------------------------------------------------------
# Internal: Stage 2 (investigator) output
# ---------------------------------------------------------------------------

class Stage2Output(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relevant_transaction_id: Optional[str] = None
    evidence_verdict: str                        # consistent | inconsistent | insufficient_data
    matched_transaction: Optional[dict] = None
    match_score: float
    amount_match: bool
    status_contradiction: bool
    flags: List[str]


# ---------------------------------------------------------------------------
# Internal: Stage 3 (reasoner) output
# ---------------------------------------------------------------------------

class Stage3Output(BaseModel):
    model_config = ConfigDict(extra="ignore")

    relevant_transaction_id: Optional[str] = None
    evidence_verdict: str
    case_type: str
    severity: str
    department: str
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: float
    reason_codes: List[str]


# ---------------------------------------------------------------------------
# Internal: Stage 4 (safety) output = final
# ---------------------------------------------------------------------------

class Stage4Output(BaseModel):
    """Final shape. Mirrors TicketResponse plus internal audit fields."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    relevant_transaction_id: Optional[str] = None
    evidence_verdict: str
    case_type: str
    severity: str
    department: str
    agent_summary: str
    recommended_next_action: str
    customer_reply: str
    human_review_required: bool
    confidence: Optional[float] = None
    reason_codes: List[str] = Field(default_factory=list)

    # Audit trail (internal — not part of TicketResponse)
    safety_violations_found: List[str] = Field(default_factory=list)
    safety_overrides_applied: List[str] = Field(default_factory=list)
