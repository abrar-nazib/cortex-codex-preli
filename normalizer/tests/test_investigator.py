"""TDD tests for Stage 2 deterministic signal helpers.

These tests cover the pure functions in `app.stage2_investigator`:
  - compute_amount_match
  - compute_status_contradiction
  - compute_amount_in_text_vs_history
  - compute_time_hard_mismatch
  - empty history → insufficient_data short-circuit
  - LLM fallback → insufficient_data
"""
from __future__ import annotations

import asyncio

import pytest

from app.schema import Stage1Output
from app.stage2_investigator import (
    compute_amount_match,
    compute_amount_in_text_vs_history,
    compute_status_contradiction,
    compute_time_hard_mismatch,
    run,
)


# ---------------------------------------------------------------------------
# compute_amount_match — 2% tolerance
# ---------------------------------------------------------------------------

def test_amount_match_exact():
    assert compute_amount_match(5000.0, 5000.0) is True


def test_amount_match_within_2_percent():
    assert compute_amount_match(5000.0, 5050.0) is True


def test_amount_match_outside_2_percent():
    assert compute_amount_match(5000.0, 6000.0) is False


def test_amount_match_claim_none():
    assert compute_amount_match(5000.0, None) is False


def test_amount_match_zero_claim():
    assert compute_amount_match(5000.0, 0.0) is False


# ---------------------------------------------------------------------------
# compute_status_contradiction
# ---------------------------------------------------------------------------

def test_status_contradiction_claim_failed_tx_completed():
    assert compute_status_contradiction(True, "completed") is True


def test_status_contradiction_claim_ok_tx_failed():
    assert compute_status_contradiction(False, "failed") is True


def test_status_contradiction_consistent():
    assert compute_status_contradiction(True, "failed") is False
    assert compute_status_contradiction(False, "completed") is False


def test_status_contradiction_pending_no_match():
    assert compute_status_contradiction(True, "pending") is False
    assert compute_status_contradiction(False, "pending") is False


# ---------------------------------------------------------------------------
# compute_amount_in_text_vs_history
# ---------------------------------------------------------------------------

def test_amount_in_text_vs_history_no_txs():
    assert compute_amount_in_text_vs_history(5000.0, []) is True


def test_amount_in_text_vs_history_claim_too_large():
    assert compute_amount_in_text_vs_history(10000.0, [100.0, 200.0, 300.0]) is True


def test_amount_in_text_vs_history_consistent():
    assert compute_amount_in_text_vs_history(100.0, [100.0, 200.0]) is False


def test_amount_in_text_vs_history_no_claim():
    assert compute_amount_in_text_vs_history(None, [100.0]) is False


# ---------------------------------------------------------------------------
# compute_time_hard_mismatch
# ---------------------------------------------------------------------------

def test_time_mismatch_today_but_old_tx():
    """Complaint says 'today' but tx is 30 days old → mismatch."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    old_ts = (now - timedelta(days=30)).isoformat()
    assert compute_time_hard_mismatch("today", old_ts, now=now) is True


def test_time_match_today_recent_tx():
    from datetime import datetime, timezone
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    recent_ts = now.isoformat()
    assert compute_time_hard_mismatch("today", recent_ts, now=now) is False


def test_time_mismatch_no_reference():
    from datetime import datetime, timezone
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    assert compute_time_hard_mismatch(None, now.isoformat(), now=now) is False


def test_time_mismatch_invalid_ts():
    assert compute_time_hard_mismatch("today", "not-a-date") is False


def test_time_mismatch_bangla_today_old_tx():
    """আজ (today in Bangla) vs 30-day-old tx → mismatch."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    old_ts = (now - timedelta(days=30)).isoformat()
    assert compute_time_hard_mismatch("আজ", old_ts, now=now) is True


# ---------------------------------------------------------------------------
# run() — empty history short-circuit (no LLM call)
# ---------------------------------------------------------------------------

def test_run_empty_history_returns_insufficient_data():
    stage1 = Stage1Output(
        cleaned_complaint="some complaint",
        detected_language="en",
        extracted_keywords=[],
        possible_issues=[],
        claimed_case_type=None,
        amount_in_complaint=5000.0,
        time_reference="today",
        injection_detected=False,
        was_truncated=False,
        original_length=14,
    )
    out = asyncio.run(run(stage1, []))
    assert out.relevant_transaction_id is None
    assert out.evidence_verdict == "insufficient_data"
    assert "empty_history" in out.flags
    assert out.match_score == 0.0


# ---------------------------------------------------------------------------
# run() — LLM fallback (simulated via monkeypatch)
# ---------------------------------------------------------------------------

def test_run_llm_error_returns_insufficient_data(monkeypatch):
    """If the LLM call raises, run() must fall back to insufficient_data."""
    from app import stage2_investigator

    def boom(messages, max_tokens):
        raise RuntimeError("simulated LLM failure")

    monkeypatch.setattr(stage2_investigator, "llm_call", boom)

    stage1 = Stage1Output(
        cleaned_complaint="I sent 5000 to wrong number",
        detected_language="en",
        extracted_keywords=["wrong number"],
        possible_issues=[],
        claimed_case_type="wrong_transfer",
        amount_in_complaint=5000.0,
        time_reference="today",
        injection_detected=False,
        was_truncated=False,
        original_length=30,
    )
    tx = [{
        "transaction_id": "TXN-1",
        "timestamp": "2026-04-14T14:08:22Z",
        "type": "transfer",
        "amount": 5000.0,
        "counterparty": "+8801719876543",
        "status": "completed",
    }]
    from app.schema import TransactionHistoryEntry
    txs = [TransactionHistoryEntry.model_validate(tx)]
    out = asyncio.run(run(stage1, txs))
    assert out.evidence_verdict == "insufficient_data"
    assert out.relevant_transaction_id is None
    assert "llm_error" in out.flags