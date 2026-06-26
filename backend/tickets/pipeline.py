"""Stage 5 orchestrator — turns a validated ticket into the §6 response.

Flow:
  1. Call normalizer /analyze (stages 1-3: normalize + evidence + classify+draft).
  2. Re-validate every enum from the untrusted normalizer reply against §7.
  3. Coerce relevant_transaction_id to a known id or null.
  4. Deterministic safety rail on customer_reply + recommended_next_action.
     Violation -> normalizer /rephrase -> re-scan. Still unsafe -> fall back to a
     templated safe reply (+ human_review_required=true), or 500 if SAFETY_FAIL_LOUD.
  5. Deterministic overrides: department = map(case_type); human_review_required
     = rule(...). The LLM's values for these two are discarded.
  6. Assemble + validate AnalyzeTicketOutSerializer -> 200.

A normalizer failure is recoverable: we emit a conservative 200 response, not a
500. Only an unexpected internal error escapes to the view's 500 wrapper.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.response import Response
from rest_framework import status as http

from . import normalizer_client
from .routing import department_for, human_review_required
from .safety import SAFE_FALLBACK_REPLY, find_safety_violations, is_safe
from .serializers import (
    CASE_TYPE_CHOICES,
    EVIDENCE_VERDICT_CHOICES,
    SEVERITY_CHOICES,
    AnalyzeTicketOutSerializer,
)

log = logging.getLogger("backend.pipeline")

_ENUM = {
    "evidence_verdict": set(EVIDENCE_VERDICT_CHOICES),
    "case_type": set(CASE_TYPE_CHOICES),
    "severity": set(SEVERITY_CHOICES),
}


def _coerce_enum(field: str, value: str | None, default: str) -> str:
    if value in _ENUM[field]:
        return value  # type: ignore[return-value]
    log.warning("pipeline coerce enum field=%s bad=%s default=%s", field, value, default)
    return default


def _fallback_response(ticket_id: str, *, reason: str) -> dict:
    log.info("pipeline fallback ticket=%s reason=%s", ticket_id, reason)
    case_type = "other"
    return {
        "ticket_id": ticket_id,
        "relevant_transaction_id": None,
        "evidence_verdict": "insufficient_data",
        "case_type": case_type,
        "severity": "low",
        "department": department_for(case_type),
        "agent_summary": (
            "Automated analysis could not be completed; the case has been queued "
            "for manual review."
        ),
        "recommended_next_action": "Route to a human agent for manual review.",
        "customer_reply": SAFE_FALLBACK_REPLY,
        "human_review_required": True,
        "reason_codes": ["fallback", reason],
        "confidence": 0.3,
    }


def _matched_amount(validated_data: dict, txn_id: str | None) -> float | None:
    if not txn_id:
        return None
    for t in validated_data.get("transaction_history") or []:
        if t.get("transaction_id") == txn_id:
            try:
                return float(t.get("amount"))
            except (TypeError, ValueError):
                return None
    return None


def _later_duplicate_id(history: list[dict]) -> str | None:
    """For a duplicate-payment pattern, return the id of the LATER transaction
    in the first (amount, counterparty) group that has >=2 entries. ISO-8601
    timestamps sort lexically when same format."""
    groups: dict[tuple, list[dict]] = {}
    for t in history:
        groups.setdefault((t.get("amount"), t.get("counterparty")), []).append(t)
    for _, txns in groups.items():
        if len(txns) >= 2:
            ordered = sorted(txns, key=lambda x: x.get("timestamp", ""))
            return ordered[-1].get("transaction_id")
    return None


def _apply_evidence_overrides(signals: list[str], case_type: str, rel_id: str | None,
                               verdict: str, history: list[dict]) -> tuple[str | None, str]:
    """Deterministic, signal-driven overrides on the LLM's evidence pick. The
    backend is the authority here so evidence_verdict + relevant_transaction_id
    follow the deterministic signals, not the model's guess.

      ambiguous                       -> null + insufficient_data
      wrong_transfer + established    -> keep rel_id + inconsistent
      duplicate_payment + duplicate    -> later txn + consistent
    """
    if any("ambiguous" in s for s in signals):
        return None, "insufficient_data"
    if case_type == "wrong_transfer" and any(
        s.startswith("established_recipient_pattern") for s in signals
    ):
        return rel_id, "inconsistent"
    if case_type == "duplicate_payment" and any(
        s.startswith("duplicate_pattern") for s in signals
    ):
        later = _later_duplicate_id(history)
        if later:
            return later, "consistent"
    return rel_id, verdict


def _sanitize_texts(result: dict, validated_data: dict) -> dict:
    """Run the safety rail on customer_reply + recommended_next_action; rephrase
    on violation and re-scan. Mutates `result` in place, returns it."""
    customer_text = result.get("customer_reply", "")
    action_text = result.get("recommended_next_action", "")
    texts = {"customer_reply": customer_text, "recommended_next_action": action_text}
    all_violations = []
    for field in texts:
        all_violations.extend(find_safety_violations(texts[field]))

    if not all_violations:
        return result

    log.warning("pipeline safety violation ticket=%s violations=%d",
                validated_data.get("ticket_id"), len(all_violations))
    language = validated_data.get("language") or "en"
    rephrased = normalizer_client.rephrase(
        texts, all_violations, language=language,
        user_type=validated_data.get("user_type"),
        complaint_context=validated_data.get("complaint", "")[:500],
    )
    result["customer_reply"] = rephrased.get("customer_reply", customer_text)
    result["recommended_next_action"] = rephrased.get("recommended_next_action", action_text)

    # re-scan
    still_unsafe = (find_safety_violations(result["customer_reply"])
                    + find_safety_violations(result["recommended_next_action"]))
    if still_unsafe:
        log.error("pipeline safety still unsafe after rephrase ticket=%s",
                  validated_data.get("ticket_id"))
        if getattr(settings, "SAFETY_FAIL_LOUD", True):
            raise SafetyError("customer-facing text remained unsafe after rephrase")
        # soft fail: templated safe reply, force human review
        result["customer_reply"] = SAFE_FALLBACK_REPLY
        result["recommended_next_action"] = (
            "Route to a human agent for manual review before sending any reply."
        )
        result["_force_review"] = True
    return result


class SafetyError(Exception):
    """Raised when safety rail cannot be satisfied and SAFETY_FAIL_LOUD=true."""


def build_response(validated_data: dict) -> Response:
    """Run the full pipeline and return a DRF Response (200)."""
    ticket_id = validated_data["ticket_id"]
    log.info("pipeline start ticket=%s", ticket_id)

    payload = {
        "ticket_id": ticket_id,
        "complaint": validated_data["complaint"],
        "language": validated_data.get("language") or None,
        "channel": validated_data.get("channel") or None,
        "user_type": validated_data.get("user_type") or None,
        "campaign_context": validated_data.get("campaign_context") or None,
        "transaction_history": validated_data.get("transaction_history") or [],
        "metadata": validated_data.get("metadata") or {},
    }

    try:
        raw = normalizer_client.analyze(payload)
    except Exception as exc:  # noqa: BLE001 — normalizer down/unreachable
        log.error("pipeline normalizer call failed ticket=%s err=%s",
                  ticket_id, type(exc).__name__)
        out = _fallback_response(ticket_id, reason="normalizer_unreachable")
        return _finalize(out, validated_data)

    # ── re-validate + coerce the untrusted normalizer reply ─────────────────
    txn_ids = {t.get("transaction_id") for t in validated_data.get("transaction_history") or []}
    rel_id = raw.get("relevant_transaction_id")
    if rel_id not in (None, *txn_ids):
        log.warning("pipeline coerce rel_id=%s not in history -> null", rel_id)
        rel_id = None
        raw["evidence_verdict"] = "insufficient_data"

    case_type = _coerce_enum("case_type", raw.get("case_type"), "other")
    severity = _coerce_enum("severity", raw.get("severity"), "low")
    evidence_verdict = _coerce_enum(
        "evidence_verdict", raw.get("evidence_verdict"), "insufficient_data"
    )

    # Deterministic evidence overrides (backend authority) from stage-2 signals.
    signals = raw.get("signals") if isinstance(raw.get("signals"), list) else []
    history = validated_data.get("transaction_history") or []
    rel_id, evidence_verdict = _apply_evidence_overrides(
        signals, case_type, rel_id, evidence_verdict, history
    )

    if rel_id is None and evidence_verdict == "consistent":
        evidence_verdict = "insufficient_data"

    amount = _matched_amount(validated_data, rel_id)
    out = {
        "ticket_id": ticket_id,
        "relevant_transaction_id": rel_id,
        "evidence_verdict": evidence_verdict,
        "case_type": case_type,
        "severity": severity,
        "department": department_for(case_type),
        "agent_summary": (raw.get("agent_summary") or "").strip()
            or "Summary unavailable; case queued for review.",
        "recommended_next_action": (raw.get("recommended_next_action") or "").strip()
            or "Route to a human agent for review.",
        "customer_reply": (raw.get("customer_reply") or "").strip()
            or SAFE_FALLBACK_REPLY,
        "human_review_required": human_review_required(
            case_type=case_type, severity=severity, evidence_verdict=evidence_verdict,
            amount=amount, relevant_transaction_id=rel_id,
        ),
        "reason_codes": raw.get("reason_codes") if isinstance(raw.get("reason_codes"), list) else [],
        "confidence": _coerce_confidence(raw.get("confidence")),
    }

    # ── stage 4 safety rail ──────────────────────────────────────────────────
    out = _sanitize_texts(out, validated_data)
    if out.pop("_force_review", False):
        out["human_review_required"] = True

    return _finalize(out, validated_data)


def _coerce_confidence(v) -> float:
    try:
        c = float(v)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, c))


def _finalize(out: dict, validated_data: dict) -> Response:
    # echo ticket_id defensively (normalizer could have echoed a different one)
    out["ticket_id"] = validated_data["ticket_id"]
    serializer = AnalyzeTicketOutSerializer(data=out)
    serializer.is_valid(raise_exception=True)
    log.info("pipeline done ticket=%s case=%s dept=%s verdict=%s review=%s",
             out["ticket_id"], out["case_type"], out["department"],
             out["evidence_verdict"], out["human_review_required"])
    return Response(serializer.validated_data, status=http.HTTP_200_OK)