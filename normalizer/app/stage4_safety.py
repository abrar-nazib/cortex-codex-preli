"""Stage 4 — Safety.

Two parts:
  Part A (code-only, mandatory): pre-sanitize customer_reply and
    recommended_next_action by scanning for SAFETY_BLOCKED_PHRASES and
    THIRD_PARTY_PATTERNS. Replace violations with safe alternatives.
  Part B (LLM, optional): improve the pre-sanitized customer_reply for
    tone and clarity. Code enforcement runs AFTER LLM.

Final code enforcement:
  - human_review_required forced True if any of the listed conditions hold.
  - All enum fields re-validated against allowed sets; invalid → "other"/safe default.
  - ticket_id echoed from the original request.

Fallback: pre-sanitized reply is used directly; code enforcement still runs.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from pydantic import ValidationError

from . import config
from .llm import LLMError, llm_call
from .schema import Stage1Output, Stage2Output, Stage3Output, Stage4Output, TicketRequest

log = logging.getLogger("normalizer.stage4")


SYSTEM_PROMPT = """You are a customer service quality reviewer for a fintech company.
You receive a draft customer reply that has already been safety-checked.
Your job is to improve the clarity, tone, and professionalism of customer_reply only. Do not change any other fields.

Output ONLY a JSON object:
{
  "customer_reply": "improved version of the reply",
  "improvement_notes": "one sentence on what you improved"
}

Rules for customer_reply:
- Professional and empathetic tone
- Reference the ticket or transaction ID if available
- Explain the next step clearly
- Do NOT add any credential requests — if you see any, remove them
- Do NOT add any refund promises — use "official channels" language
- Do NOT add phone numbers or unofficial contact details
- Keep it under 100 words
- Output JSON only."""


# ---------------------------------------------------------------------------
# Code enforcement — runs regardless of LLM
# ---------------------------------------------------------------------------

def _scan_phrase(text: str, phrases: List[str]) -> Optional[str]:
    lc = text.lower()
    for phrase in phrases:
        if phrase in lc:
            return phrase
    return None


def _scan_third_party(text: str) -> List[str]:
    """Return a list of violation labels found: 'phone', 'unofficial_url'."""
    violations: list[str] = []
    if config.PHONE_PATTERN.search(text):
        violations.append("phone")
    for m in config.URL_PATTERN.findall(text):
        low = m.lower()
        if not any(host in low for host in config.ALLOWED_URL_HOSTS):
            violations.append("unofficial_url")
            break
    return violations


def _replace_phrase(text: str, phrase: str, replacement: str) -> str:
    """Case-insensitive substring replace of `phrase` with `replacement`."""
    pattern = re.compile(re.escape(phrase), re.IGNORECASE)
    return pattern.sub(replacement, text)


def _pre_sanitize(text: str) -> tuple[str, list[str], list[str]]:
    """Strip credential requests, unauthorized promises, and third-party redirects.

    Returns (cleaned_text, violations_found, overrides_applied).

    Iterates until no more matches, so a single sentence containing both
    "card number" and "CVV" (or "guaranteed" and "your money will be returned")
    has every violation stripped, not just the first one found.

    The credential scan skips bare "your pin"/"your otp" when preceded by
    a negation like "do not share" / "never share" / "do not provide" — these
    are legitimate safety boilerplate, not requests.
    """
    violations: list[str] = []
    overrides: list[str] = []
    out = text

    # Credential requests — loop until clean.
    # Skip phrases that are part of a safety warning like "do not share your PIN".
    # The negation word ("do not", "never") precedes the matched credential
    # phrase; we look at the 40 chars BEFORE the match start.
    safe_negations = ("do not ", "never ", "please do not ", "do not ever ")

    def _is_negated(text_lower: str, idx: int) -> bool:
        # idx is the start index of the matched phrase in the lowercased text.
        window_start = max(0, idx - 40)
        window = text_lower[window_start:idx]
        return any(neg in window for neg in safe_negations)

    changed = True
    while changed:
        changed = False
        for phrase in config.CREDENTIAL_REQUEST_PHRASES:
            pat = re.compile(re.escape(phrase), re.IGNORECASE)
            m = pat.search(out)
            if not m:
                continue
            if _is_negated(out.lower(), m.start()):
                # Legitimate safety boilerplate, not a request. Leave it.
                continue
            out = pat.sub(config.SAFETY_REPLACEMENT_CREDENTIAL, out)
            violations.append(f"credential_request:{phrase}")
            overrides.append("replaced_credential_request")
            changed = True
            break  # restart scan

    # Unauthorized promises — loop until clean.
    changed = True
    while changed:
        changed = False
        hit = _scan_phrase(out, config.UNAUTHORIZED_PROMISE_PHRASES)
        if hit:
            out = _replace_phrase(out, hit, config.SAFETY_REPLACEMENT_PROMISE)
            violations.append(f"unauthorized_promise:{hit}")
            overrides.append("replaced_unauthorized_promise")
            changed = True

    # Third-party phone / URLs
    tp = _scan_third_party(out)
    if tp:
        # Replace the entire sentence containing the violation
        for v in tp:
            violations.append(f"third_party:{v}")
        # Conservative: replace phone-shaped substrings and unofficial URLs.
        out = config.PHONE_PATTERN.sub(config.SAFETY_REPLACEMENT_THIRD_PARTY, out)
        out = config.URL_PATTERN.sub(config.SAFETY_REPLACEMENT_THIRD_PARTY, out)
        overrides.append("replaced_third_party_redirect")

    return out, violations, overrides


def _force_human_review(case_type: str, severity: str, verdict: str,
                        confidence: Optional[float],
                        violations: List[str]) -> bool:
    """Code-authoritative human_review_required override."""
    if case_type == "phishing_or_social_engineering":
        return True
    if severity == "critical":
        return True
    if verdict == "inconsistent":
        return True
    if confidence is not None and confidence < 0.5:
        return True
    if violations:
        return True
    return False


def _coerce_enum(value: str, allowed: set, fallback: str) -> str:
    v = (value or "").strip().lower()
    return v if v in allowed else fallback


def _enforce_reply_language(reply: str, language: str, case_type: str, tx_id: Optional[str]) -> str:
    """If the LLM drifted language, replace with the canonical bilingual template."""
    # Heuristic: if bn detected but reply has no Bengali script, replace.
    is_bn_script = bool(re.search(r"[\u0980-\u09FF]", reply))
    if language == "bn" and not is_bn_script:
        return config.safe_reply_for(config.CaseType(case_type), "bn", tx_id)
    if language == "en" and is_bn_script:
        return config.safe_reply_for(config.CaseType(case_type), "en", tx_id)
    return reply


# ---------------------------------------------------------------------------
# LLM improve (optional)
# ---------------------------------------------------------------------------

def _improve_with_llm(request: TicketRequest, stage3: Stage3Output,
                      pre_sanitized_reply: str) -> Optional[str]:
    """Returns the improved reply text, or None on any LLM failure."""
    try:
        user_msg = (
            f"ticket_id: {request.ticket_id}\n"
            f"case_type: {stage3.case_type}\n"
            f"department: {stage3.department}\n"
            f"relevant_transaction_id: {stage3.relevant_transaction_id}\n\n"
            f"Draft customer_reply to improve:\n{pre_sanitized_reply}"
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]
        raw = llm_call(messages, max_tokens=config.MAX_TOKENS_STAGE4)
        improved = raw.get("customer_reply")
        if not isinstance(improved, str) or not improved.strip():
            return None
        return improved.strip()[:1000]
    except LLMError as exc:
        log.warning("stage4 improve fallback: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

async def run(request: TicketRequest, stage1: Stage1Output,
              stage2: Stage2Output, stage3: Stage3Output) -> Stage4Output:
    """Run Stage 4. Never raises — always returns a Stage4Output."""
    case_type = stage3.case_type
    language = stage1.detected_language or "en"
    tx_id = stage3.relevant_transaction_id
    overrides: list[str] = []   # collected across all enforcement steps

    # ─── Stage 4 phishing short-circuit (defensive) ──────────────────────
    # If Stage 3 missed phishing but Stage 1 detected phishing-shaped
    # content (cleaned_complaint / possible_issues / claimed_case_type),
    # override case_type to phishing_or_social_engineering. The floor
    # below will then set severity=critical, department=fraud_risk,
    # human_review=True.
    _phishing_signals = [
        (stage1.cleaned_complaint or "").lower(),
        " ".join(stage1.possible_issues or []).lower(),
        (stage1.claimed_case_type or "").lower(),
        (stage3.agent_summary or "").lower(),
        (stage3.recommended_next_action or "").lower(),
    ]
    _phishing_blob = " | ".join(s for s in _phishing_signals if s)
    if any(kw in _phishing_blob for kw in config.PHISHING_KEYWORDS):
        if case_type != "phishing_or_social_engineering":
            case_type = "phishing_or_social_engineering"
            overrides.append("stage4_phishing_short_circuit")

    # ─── Part A: pre-sanitize customer_reply AND recommended_next_action ──
    pre_reply, v1, o1 = _pre_sanitize(stage3.customer_reply)
    pre_action, v2, o2 = _pre_sanitize(stage3.recommended_next_action)
    violations = v1 + v2
    overrides = o1 + o2

    # ─── Part B: LLM improve customer_reply (optional) ───────────────────
    improved = _improve_with_llm(request, stage3, pre_reply)
    final_reply = improved if improved is not None else pre_reply

    # ─── Post-LLM code enforcement (always runs) ────────────────────────
    final_reply, v3, o3 = _pre_sanitize(final_reply)
    violations.extend(v3)
    overrides.extend(o3)
    final_reply = _enforce_reply_language(final_reply, language, case_type, tx_id)

    # If everything got stripped, fall back to safe template.
    if not final_reply.strip():
        final_reply = config.safe_reply_for(config.CaseType(case_type), language, tx_id)
        overrides.append("empty_after_sanitize_replaced_template")

    # ─── human_review_required (code-authoritative) ─────────────────────
    human_review = _force_human_review(
        case_type=case_type,
        severity=stage3.severity,
        verdict=stage3.evidence_verdict,
        confidence=stage3.confidence,
        violations=violations,
    )
    # OR with stage3's own setting (Stage 3 may have set it conservatively)
    human_review = human_review or stage3.human_review_required

    # ─── Enum validation ────────────────────────────────────────────────
    case_type = _coerce_enum(case_type, {
        "wrong_transfer", "payment_failed", "refund_request", "duplicate_payment",
        "merchant_settlement_delay", "agent_cash_in_issue",
        "phishing_or_social_engineering", "other",
    }, "other")
    severity = _coerce_enum(stage3.severity, {"low", "medium", "high", "critical"}, "medium")
    department = _coerce_enum(stage3.department, {
        "customer_support", "dispute_resolution", "payments_ops",
        "merchant_operations", "agent_operations", "fraud_risk",
    }, "customer_support")
    verdict = _coerce_enum(stage3.evidence_verdict,
                           {"consistent", "inconsistent", "insufficient_data"},
                           "insufficient_data")

    # Severity floor only — never downgrade.
    if case_type == "phishing_or_social_engineering":
        severity = "critical"
        department = "fraud_risk"
    if (stage1.amount_in_complaint or 0) >= config.CRITICAL_VALUE_THRESHOLD:
        severity = "critical"

    return Stage4Output(
        ticket_id=request.ticket_id,
        relevant_transaction_id=tx_id,
        evidence_verdict=verdict,
        case_type=case_type,
        severity=severity,
        department=department,
        agent_summary=stage3.agent_summary,
        recommended_next_action=pre_action,
        customer_reply=final_reply,
        human_review_required=human_review,
        confidence=stage3.confidence,
        reason_codes=stage3.reason_codes,
        safety_violations_found=violations,
        safety_overrides_applied=overrides,
    )