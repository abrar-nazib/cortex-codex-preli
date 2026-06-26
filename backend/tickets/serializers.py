"""DRF serializers — public contract for the QueueStorm Investigator API.

Three input serializers mirror the Problem Statement request schema (§5):

  TransactionSerializer            — one transaction_history entry (§5.2).
  TicketSerializer                 — a standalone ticket (no transaction_history).
  TicketWithTransactionSerializer  — ticket + nested transaction_history list
                                      (the full POST /analyze-ticket body).

One output serializer mirrors the response schema (§6):

  AnalyzeTicketOutSerializer        — the structured investigator response.

The enum tuples below are the single source of truth for the wire contract;
the §7 taxonomy must match EXACTLY (case/plural/spelling variants are scored as
schema violations), so every ChoiceField draws from these lists.
"""
from __future__ import annotations

from rest_framework import serializers

# ─── Enum taxonomy (§7 + §5.2 + §6) ──────────────────────────────────────────
# Single source of truth. Keep values lowercase, exact, as the statement spells
# them. Do NOT "fix" spacing/underscores — they are part of the contract.
LANGUAGE_CHOICES = ("en", "bn", "mixed")
CHANNEL_CHOICES = (
    "in_app_chat",
    "call_center",
    "email",
    "merchant_portal",
    "field_agent",
)
USER_TYPE_CHOICES = ("customer", "merchant", "agent", "unknown")
TXN_TYPE_CHOICES = (
    "transfer",
    "payment",
    "cash_in",
    "cash_out",
    "settlement",
    "refund",
)
TXN_STATUS_CHOICES = ("completed", "failed", "pending", "reversed")

EVIDENCE_VERDICT_CHOICES = ("consistent", "inconsistent", "insufficient_data")
CASE_TYPE_CHOICES = (
    "wrong_transfer",
    "payment_failed",
    "refund_request",
    "duplicate_payment",
    "merchant_settlement_delay",
    "agent_cash_in_issue",
    "phishing_or_social_engineering",
    "other",
)
SEVERITY_CHOICES = ("low", "medium", "high", "critical")
DEPARTMENT_CHOICES = (
    "customer_support",
    "dispute_resolution",
    "payments_ops",
    "merchant_operations",
    "agent_operations",
    "fraud_risk",
)


# ─── Input serializers (§5) ───────────────────────────────────────────────────
class HealthOutSerializer(serializers.Serializer):
    status = serializers.CharField()


class SummarizeInSerializer(serializers.Serializer):
    """Inbound text to summarize (legacy /summarize placeholder)."""

    text = serializers.CharField(min_length=1, trim_whitespace=True)


class SummarizeOutSerializer(serializers.Serializer):
    """Summary returned to the caller (legacy /summarize placeholder)."""

    summary = serializers.CharField()


class TransactionSerializer(serializers.Serializer):
    """One transaction_history entry (§5.2). Standalone — no ticket context."""

    transaction_id = serializers.CharField(max_length=64)
    timestamp = serializers.CharField(max_length=32)
    type = serializers.ChoiceField(choices=TXN_TYPE_CHOICES)
    amount = serializers.FloatField()
    counterparty = serializers.CharField(max_length=64)
    status = serializers.ChoiceField(choices=TXN_STATUS_CHOICES)


class TicketSerializer(serializers.Serializer):
    """Standalone ticket — the request fields WITHOUT transaction_history.

    Use this when you only want to validate the complaint + routing context
    in isolation (e.g. a sub-test, or a safety-only case with no history).
    """

    ticket_id = serializers.CharField(max_length=64)
    complaint = serializers.CharField(trim_whitespace=True)
    language = serializers.ChoiceField(
        choices=LANGUAGE_CHOICES, required=False, allow_blank=True
    )
    channel = serializers.ChoiceField(
        choices=CHANNEL_CHOICES, required=False, allow_blank=True
    )
    user_type = serializers.ChoiceField(
        choices=USER_TYPE_CHOICES, required=False, allow_blank=True
    )
    campaign_context = serializers.CharField(
        required=False, allow_blank=True, max_length=128
    )
    metadata = serializers.JSONField(required=False, default=dict)


class TicketWithTransactionSerializer(TicketSerializer):
    """Full POST /analyze-ticket body (§5): ticket + nested transaction_history.

    Inherits every TicketSerializer field, then layers the optional
    transaction_history list. `transaction_history` is optional and may be
    empty (safety-only cases per §5.1); when present, each entry is validated
    by TransactionSerializer.
    """

    transaction_history = TransactionSerializer(
        many=True, required=False, default=list
    )


# ─── Output serializer (§6) ──────────────────────────────────────────────────
class AnalyzeTicketOutSerializer(serializers.Serializer):
    """Structured investigator response (§6).

    Required fields are the contract the judge scores against (§14 API Contract
    & Schema). `confidence` (0–1) and `reason_codes` are optional per §6.1.
    `relevant_transaction_id` is string-or-null — null when no transaction in
    the supplied history matches the complaint.
    """

    ticket_id = serializers.CharField(max_length=64)
    relevant_transaction_id = serializers.CharField(max_length=64, allow_null=True)
    evidence_verdict = serializers.ChoiceField(choices=EVIDENCE_VERDICT_CHOICES)
    case_type = serializers.ChoiceField(choices=CASE_TYPE_CHOICES)
    severity = serializers.ChoiceField(choices=SEVERITY_CHOICES)
    department = serializers.ChoiceField(choices=DEPARTMENT_CHOICES)
    agent_summary = serializers.CharField()
    recommended_next_action = serializers.CharField()
    customer_reply = serializers.CharField()
    human_review_required = serializers.BooleanField()
    confidence = serializers.FloatField(
        required=False, min_value=0.0, max_value=1.0
    )
    reason_codes = serializers.ListField(
        child=serializers.CharField(), required=False
    )