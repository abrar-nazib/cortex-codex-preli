"""Pipeline — orchestrates Stages 1 → 2 → 3 → 4.

`run_pipeline` is the single public entry. It never raises to the caller;
every stage has a fallback that produces valid output. The final return is
always a TicketResponse that matches the public schema.
"""
from __future__ import annotations

import logging
from typing import List

from . import config
from .schema import (
    Stage1Output,
    Stage2Output,
    Stage3Output,
    Stage4Output,
    TicketRequest,
    TicketResponse,
    TransactionHistoryEntry,
)
from . import stage1_cleaner, stage2_investigator, stage3_reasoner, stage4_safety

log = logging.getLogger("normalizer.pipeline")


def _ensure_transactions(tx: list | None) -> List[TransactionHistoryEntry]:
    if not tx:
        return []
    out = []
    for item in tx:
        try:
            out.append(TransactionHistoryEntry.model_validate(item))
        except Exception:
            # Skip malformed entries rather than fail the whole request.
            continue
    return out


def _stage4_to_response(stage4: Stage4Output) -> TicketResponse:
    return TicketResponse(
        ticket_id=stage4.ticket_id,
        relevant_transaction_id=stage4.relevant_transaction_id,
        evidence_verdict=stage4.evidence_verdict,
        case_type=stage4.case_type,
        severity=stage4.severity,
        department=stage4.department,
        agent_summary=stage4.agent_summary,
        recommended_next_action=stage4.recommended_next_action,
        customer_reply=stage4.customer_reply,
        human_review_required=stage4.human_review_required,
        confidence=stage4.confidence,
        reason_codes=stage4.reason_codes,
    )


async def run_pipeline(request: TicketRequest) -> TicketResponse:
    """Run all 4 stages. Never raises; always returns a TicketResponse."""
    if not request.complaint or not request.complaint.strip():
        # Defensive — main.py also validates, but pipeline is total.
        return TicketResponse(
            ticket_id=request.ticket_id,
            relevant_transaction_id=None,
            evidence_verdict="insufficient_data",
            case_type="other",
            severity="low",
            department="customer_support",
            agent_summary="Empty complaint received.",
            recommended_next_action="Ask customer to provide complaint details.",
            customer_reply=(
                "Thank you for reaching out. Please share the transaction ID, "
                "the amount involved, and a short description of what went wrong. "
                "Please do not share your PIN or OTP with anyone."
            ),
            human_review_required=False,
            confidence=0.3,
            reason_codes=["empty_complaint"],
        )

    transactions = _ensure_transactions(request.transaction_history or [])

    # ─── Stage 1 ─────────────────────────────────────────────────────────
    try:
        stage1: Stage1Output = await stage1_cleaner.run(request.complaint)
    except Exception as exc:                                # noqa: BLE001
        log.exception("stage1 unexpected: %s", exc)
        stage1 = Stage1Output(
            cleaned_complaint=request.complaint[: config.MAX_CLEANED_LENGTH],
            detected_language="en",
            extracted_keywords=[],
            possible_issues=[],
            claimed_case_type=None,
            amount_in_complaint=None,
            time_reference=None,
            injection_detected=False,
            was_truncated=False,
            original_length=len(request.complaint),
        )

    # ─── Stage 2 ─────────────────────────────────────────────────────────
    try:
        stage2: Stage2Output = await stage2_investigator.run(stage1, transactions)
    except Exception as exc:                                # noqa: BLE001
        log.exception("stage2 unexpected: %s", exc)
        stage2 = Stage2Output(
            relevant_transaction_id=None,
            evidence_verdict="insufficient_data",
            matched_transaction=None,
            match_score=0.0,
            amount_match=False,
            status_contradiction=False,
            flags=["pipeline_error"],
        )

    # ─── Stage 3 ─────────────────────────────────────────────────────────
    try:
        stage3: Stage3Output = await stage3_reasoner.run(request, stage1, stage2, transactions)
    except Exception as exc:                                # noqa: BLE001
        log.exception("stage3 unexpected: %s", exc)
        stage3 = Stage3Output(
            relevant_transaction_id=stage2.relevant_transaction_id,
            evidence_verdict=stage2.evidence_verdict,
            case_type="other",
            severity="medium",
            department="customer_support",
            agent_summary="Automated classification. Manual review required.",
            recommended_next_action="Route to appropriate team for review.",
            customer_reply=config.STAGE3_FALLBACK_REPLY,
            human_review_required=True,
            confidence=0.3,
            reason_codes=["pipeline_error_stage3"],
        )

    # ─── Stage 4 ─────────────────────────────────────────────────────────
    try:
        stage4: Stage4Output = await stage4_safety.run(request, stage1, stage2, stage3)
    except Exception as exc:                                # noqa: BLE001
        log.exception("stage4 unexpected: %s", exc)
        # Last-resort: a hand-built safe response echoing the request.
        safe_reply = config.safe_reply_for(
            config.CaseType(stage3.case_type if stage3.case_type in {ct.value for ct in config.CaseType} else "other"),
            stage1.detected_language or "en",
            stage3.relevant_transaction_id,
        )
        stage4 = Stage4Output(
            ticket_id=request.ticket_id,
            relevant_transaction_id=stage3.relevant_transaction_id,
            evidence_verdict=stage3.evidence_verdict,
            case_type=stage3.case_type if stage3.case_type in {ct.value for ct in config.CaseType} else "other",
            severity=stage3.severity if stage3.severity in {s.value for s in config.Severity} else "medium",
            department=stage3.department if stage3.department in {d.value for d in config.Department} else "customer_support",
            agent_summary=stage3.agent_summary,
            recommended_next_action="Escalate to appropriate team for investigation and resolution.",
            customer_reply=safe_reply,
            human_review_required=True,
            confidence=stage3.confidence,
            reason_codes=(stage3.reason_codes or []) + ["pipeline_error_stage4"],
            safety_violations_found=[],
            safety_overrides_applied=["pipeline_error_fallback"],
        )

    return _stage4_to_response(stage4)