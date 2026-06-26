"""Stage 2 — Investigator.

Pre-LLM (deterministic, pure functions — unit-tested):
  For each transaction, compute:
    - amount_match           (2% tolerance vs Stage 1 amount)
    - status_contradiction   (claim vs tx status mismatch)
    - amount_in_text_vs_history_discrepancy  (claim larger than any tx)
    - time_hard_mismatch     (claim time vs tx timestamp > N days)

LLM:
  Receives the pre-computed signals + raw transactions. Returns verdict
  and relevant_transaction_id. Empty history short-circuits to
  insufficient_data with no LLM call.

Fallback (any error):
  relevant_transaction_id=None, evidence_verdict="insufficient_data".
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from pydantic import ValidationError

from . import config
from .llm import LLMError, llm_call
from .schema import Stage1Output, Stage2Output, TransactionHistoryEntry

log = logging.getLogger("normalizer.stage2")


SYSTEM_PROMPT = """You are an evidence analyst for a fintech support system.
You receive a customer complaint, transaction signals computed by the system, and the raw transaction list.
Your job is to identify which transaction the complaint refers to and determine the evidence verdict.

Output ONLY a JSON object:
{
  "relevant_transaction_id": "TXN-XXXX or null",
  "evidence_verdict": "consistent or inconsistent or insufficient_data",
  "match_score": 0.0 to 1.0,
  "match_reasoning": "one sentence explaining why this transaction matches",
  "flags": ["high_value", "status_failed", "large_amount", "pending", "time_mismatch", "amount_in_text_vs_history_discrepancy"]
}

Evidence verdict rules:
- consistent: the transaction data supports what the customer says (e.g. customer says sent 5000, TXN shows 5000 transfer completed)
- inconsistent: the transaction data contradicts the customer (e.g. customer says payment failed, TXN shows completed) (e.g. customer says sent 5000, TXN shows 3000)
- insufficient_data: no transaction matches or history is empty

Matching rules:
- Use the pre-computed amount_match signal as primary evidence
- A status_contradiction=True is strong evidence of inconsistency
- If no transaction has amount_match=True, verdict is insufficient_data
- If multiple transactions match, pick the one with the closest amount and most recent timestamp
- If amount_in_text_vs_history_discrepancy=True, the customer's claim has no support in history — verdict should be insufficient_data
- If time_hard_mismatch=True, still consider amount match but lower confidence

relevant_transaction_id must be null if no match found.
Output JSON only."""


# ---------------------------------------------------------------------------
# Pure signal computation (unit-tested)
# ---------------------------------------------------------------------------

FAILED_KEYWORDS = ("failed", "didn't go through", "did not go through",
                   "not processed", "ব্যর্থ", "ব্যালেন্স কাটা", "কাটেনি")


def _detect_failed_claim(cleaned_complaint: str) -> bool:
    lc = cleaned_complaint.lower()
    return any(kw in lc for kw in FAILED_KEYWORDS)


def compute_amount_match(tx_amount: float, claim_amount: Optional[float]) -> bool:
    """2% tolerance match."""
    if claim_amount is None:
        return False
    denom = max(tx_amount, 1.0)
    return abs(tx_amount - claim_amount) / denom < config.AMOUNT_TOLERANCE


def compute_status_contradiction(claim_failed: bool, tx_status: str) -> bool:
    """Customer says 'failed' but tx completed (or vice versa)."""
    status = (tx_status or "").lower()
    if claim_failed and status == "completed":
        return True
    if (not claim_failed) and status == "failed":
        return True
    return False


def compute_amount_in_text_vs_history(claim_amount: Optional[float], tx_amounts: Iterable[float]) -> bool:
    """True if the customer claims an amount clearly larger than any tx amount."""
    if claim_amount is None:
        return False
    amounts = [float(a) for a in tx_amounts]
    if not amounts:
        return True
    return claim_amount > max(amounts) * 1.5  # claim > 1.5× the largest tx


def _parse_ts(ts: str) -> Optional[datetime]:
    """Best-effort ISO 8601 parse."""
    if not ts:
        return None
    s = ts.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_time_hard_mismatch(claim_time_reference: Optional[str], tx_timestamp: str,
                               now: Optional[datetime] = None) -> bool:
    """True if the claim's time reference is wildly inconsistent with tx timestamp.

    Heuristic: if the complaint mentions 'today'/'yesterday'/bn equivalents and
    the tx is more than TIME_HARD_MISMATCH_DAYS old, flag it.
    """
    if not claim_time_reference:
        return False
    tx_dt = _parse_ts(tx_timestamp)
    if not tx_dt:
        return False
    if tx_dt.tzinfo is None:
        tx_dt = tx_dt.replace(tzinfo=timezone.utc)

    now = now or datetime.now(timezone.utc)
    delta_days = abs((now - tx_dt).days)

    ref_lower = claim_time_reference.lower()
    is_recent_phrase = any(phrase in ref_lower for phrase in config.RELATIVE_TIME_OFFSETS.keys())
    if is_recent_phrase and delta_days > config.TIME_HARD_MISMATCH_DAYS:
        return True
    # Also: any "today" mention vs >7 days old = mismatch
    if "today" in ref_lower and delta_days > config.TIME_HARD_MISMATCH_DAYS:
        return True
    return False


# ---------------------------------------------------------------------------
# Build signals summary string for the LLM
# ---------------------------------------------------------------------------

def _build_signals_summary(
    stage1: Stage1Output,
    transactions: List[TransactionHistoryEntry],
) -> List[dict[str, Any]]:
    claim_failed = _detect_failed_claim(stage1.cleaned_complaint)
    tx_amounts = [t.amount for t in transactions]
    cross_field_discrepancy = compute_amount_in_text_vs_history(stage1.amount_in_complaint, tx_amounts)

    rows = []
    for tx in transactions:
        am = compute_amount_match(tx.amount, stage1.amount_in_complaint)
        sc = compute_status_contradiction(claim_failed, tx.status)
        tm = compute_time_hard_mismatch(stage1.time_reference, tx.timestamp)
        rows.append({
            "transaction_id": tx.transaction_id,
            "amount": tx.amount,
            "type": tx.type,
            "status": tx.status,
            "amount_match": am,
            "status_contradiction": sc,
            "time_hard_mismatch": tm,
            "amount_in_text_vs_history_discrepancy": cross_field_discrepancy,
        })
    return rows


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm(stage1: Stage1Output, transactions: List[TransactionHistoryEntry]) -> dict[str, Any]:
    signals = _build_signals_summary(stage1, transactions)
    user_msg = (
        f"Complaint: {stage1.cleaned_complaint}\n"
        f"Claimed amount: {stage1.amount_in_complaint}\n\n"
        f"Transaction signals (computed by system):\n"
        f"{json.dumps(signals, indent=2)}\n\n"
        f"Raw transactions:\n"
        f"{json.dumps([t.model_dump() for t in transactions], indent=2)}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    return llm_call(messages, max_tokens=config.MAX_TOKENS_STAGE2)


def _normalize_verdict(value: Any) -> str:
    v = str(value or "").strip().lower()
    if v in ("consistent", "inconsistent", "insufficient_data"):
        return v
    return "insufficient_data"


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

async def run(stage1: Stage1Output, transactions: List[TransactionHistoryEntry]) -> Stage2Output:
    """Run Stage 2. Never raises — fallback on any error."""
    if not transactions:
        return Stage2Output(
            relevant_transaction_id=None,
            evidence_verdict="insufficient_data",
            matched_transaction=None,
            match_score=0.0,
            amount_match=False,
            status_contradiction=False,
            flags=["empty_history"],
        )

    try:
        raw = _call_llm(stage1, transactions)
        verdict = _normalize_verdict(raw.get("evidence_verdict"))
        rel_id = raw.get("relevant_transaction_id")
        rel_id = str(rel_id).strip() if rel_id not in (None, "", "null") else None

        # Find the matched transaction object (if any).
        matched = None
        if rel_id:
            for t in transactions:
                if t.transaction_id == rel_id:
                    matched = t.model_dump()
                    break

        # Recompute local flags from the chosen transaction.
        am_flag = False
        sc_flag = False
        tm_flag = False
        if matched:
            am_flag = compute_amount_match(float(matched["amount"]), stage1.amount_in_complaint)
            sc_flag = compute_status_contradiction(
                _detect_failed_claim(stage1.cleaned_complaint),
                str(matched["status"]),
            )
            tm_flag = compute_time_hard_mismatch(stage1.time_reference, str(matched["timestamp"]))

        flags: list[str] = []
        if am_flag:
            flags.append("amount_match")
        if sc_flag:
            flags.append("status_contradiction")
        if tm_flag:
            flags.append("time_hard_mismatch")
        # Cross-field discrepancy persists regardless of matched tx.
        if compute_amount_in_text_vs_history(stage1.amount_in_complaint, [t.amount for t in transactions]):
            flags.append("amount_in_text_vs_history_discrepancy")
        # Large amount / pending / failed carried from raw flags
        for f in (raw.get("flags") or []):
            if f not in flags:
                flags.append(str(f))

        try:
            match_score = float(raw.get("match_score", 0.0))
        except (TypeError, ValueError):
            match_score = 0.0
        match_score = max(0.0, min(1.0, match_score))

        return Stage2Output(
            relevant_transaction_id=rel_id,
            evidence_verdict=verdict,
            matched_transaction=matched,
            match_score=match_score,
            amount_match=am_flag,
            status_contradiction=sc_flag,
            flags=flags,
        )
    except (LLMError, ValidationError, ValueError, KeyError, TypeError) as exc:
        log.warning("stage2 fallback: %s", exc)
        return Stage2Output(
            relevant_transaction_id=None,
            evidence_verdict="insufficient_data",
            matched_transaction=None,
            match_score=0.0,
            amount_match=False,
            status_contradiction=False,
            flags=["llm_error"],
        )