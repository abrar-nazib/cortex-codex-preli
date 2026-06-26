"""Stage 2 — deterministic evidence matching (no LLM).

Scores every transaction against the normalized complaint by amount, type,
counterparty, recency and status, and emits signals the LLM should weigh when
assigning evidence_verdict. This grounds the LLM; it does NOT make the final
verdict (the LLM does, in stage 3, with this block in context).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .schemas import AnalyzeRequest, EvidencePass, ScoredTxn

_AMOUNT_RE = re.compile(r"\b(\d{1,3}(?:[,\d]{0,9})|\d{4,7})\b")
_PHONE_RE = re.compile(r"\+?\d[\d\s-]{6,}")
_ID_RE = re.compile(r"\b(TXN|MERCHANT|AGENT|BILLER|DESCO)[-\w]*\b", re.IGNORECASE)

# complaint verb -> transaction type affinity
_TYPE_KEYWORDS = {
    "transfer": ("send", "sent", "transfer", "wrong number", "wrong person", "brother"),
    "payment": ("pay", "paid", "recharge", "bill", "electricity", "merchant"),
    "cash_in": ("cash in", "cash-in", "deposit", "load", "balance"),
    "cash_out": ("cash out", "withdraw"),
    "settlement": ("settle", "settlement", "sales"),
    "refund": ("refund", "reverse", "back my money", "money back", "deducted"),
}


def _parse_ts(ts: str) -> datetime | None:
    # ISO-8601 with trailing Z. Be tolerant.
    s = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _amounts_in(text: str) -> set[str]:
    out = set()
    for m in _AMOUNT_RE.finditer(text):
        out.add(m.group(1).replace(",", ""))
    return out


def _phones_in(text: str) -> set[str]:
    return {m.group(0).replace(" ", "").replace("-", "") for m in _PHONE_RE.finditer(text)}


def _ids_in(text: str) -> set[str]:
    return {m.group(0).upper() for m in _ID_RE.finditer(text)}


def score_evidence(req: AnalyzeRequest, clean_complaint: str) -> EvidencePass:
    comp = clean_complaint.lower()
    comp_amounts = _amounts_in(clean_complaint)
    comp_phones = _phones_in(clean_complaint)
    comp_ids = _ids_in(clean_complaint)

    scored: list[ScoredTxn] = []
    now = datetime.now(timezone.utc)
    for t in req.transaction_history:
        reasons: list[str] = []
        score = 0.0

        # amount match (strongest signal)
        amt_str = str(int(t.amount)) if float(t.amount).is_integer() else str(t.amount)
        if comp_amounts and amt_str in comp_amounts:
            score += 3.0
            reasons.append("amount_match")
        # type affinity
        kws = _TYPE_KEYWORDS.get(t.type, ())
        if any(k in comp for k in kws):
            score += 1.5
            reasons.append("type_affinity")
        # counterparty (phone or id) appears in complaint
        cp = t.counterparty
        cp_norm = cp.replace(" ", "").replace("-", "")
        if cp_norm and (cp_norm in comp_phones or cp.upper() in comp_ids or cp in clean_complaint):
            score += 2.0
            reasons.append("counterparty_match")
        # status alignment: complaint about failure -> failed txn
        if "fail" in comp or "deduct" in comp:
            if t.status == "failed":
                score += 1.0
                reasons.append("status_failed")
        if "pending" in comp or "not received" in comp or "didn't get" in comp or "didn't get" in comp:
            if t.status == "pending":
                score += 1.0
                reasons.append("status_pending")
        # recency: prefer recent (last 2 days)
        ts = _parse_ts(t.timestamp)
        if ts and now:
            age_days = (now - ts).days
            if age_days <= 2:
                score += 0.5
                reasons.append("recent")

        scored.append(ScoredTxn(
            transaction_id=t.transaction_id, timestamp=t.timestamp, type=t.type,
            amount=t.amount, counterparty=t.counterparty, status=t.status,
            score=score, reasons=reasons,
        ))

    # signals for the LLM
    signals: list[str] = []
    scored_sorted = sorted(scored, key=lambda s: s.score, reverse=True)
    if not scored_sorted:
        signals.append("no_transaction_history")
    else:
        top = scored_sorted[0]
        second = scored_sorted[1] if len(scored_sorted) > 1 else None
        if top.score == 0:
            signals.append("no_transaction_matches_complaint")
        if (second is not None and abs(top.score - second.score) < 0.01
                and top.score >= 3.0 and top.counterparty != second.counterparty):
            # genuine recipient ambiguity (different counterparties, e.g.
            # "sent to my brother" with two same-amount transfers to two
            # numbers). Same-counterparty ties are duplicates, NOT ambiguous.
            signals.append("multiple_plausible_matches_ambiguous")
        # established-recipient pattern: same counterparty appears 3+ times
        from collections import Counter
        cp_counts = Counter(s.counterparty for s in scored)
        for cp, c in cp_counts.items():
            if c >= 3:
                signals.append(f"established_recipient_pattern:{cp}({c}x)")
        # duplicate pattern: identical amount + counterparty within short window
        seen: dict[tuple, list[str]] = {}
        for s in scored:
            key = (s.amount, s.counterparty)
            seen.setdefault(key, []).append(s.transaction_id)
        for key, ids in seen.items():
            if len(ids) >= 2:
                signals.append(f"duplicate_pattern:{key[0]}_to_{key[1]}({len(ids)}x)")

    top_id = scored_sorted[0].transaction_id if scored_sorted else None
    ambiguous = any("ambiguous" in s for s in signals)
    return EvidencePass(scored=scored_sorted, signals=signals,
                        top_transaction_id=top_id, ambiguous=ambiguous)