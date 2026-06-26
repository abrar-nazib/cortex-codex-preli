"""Reasoning pipeline orchestrator (normalizer side).

  Stage 1  Normalize complaint (LLM, skipped for clean English) -> clean_complaint
  Stage 2  Deterministic evidence scoring (no LLM) -> EvidencePass
  Stage 3  Classify + route + draft (ONE LLM call, constrained JSON) -> AnalyzeResult

Each stage validates its output; on validation failure the LLM stage gets one
self-correction retry (error fed back), then a conservative fallback so a bad
upstream reply never poisons the whole response. Every hop is logged.
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from .evidence import score_evidence
from .llm import LLMError, get_provider
from .prompts import (
    build_classify_messages,
    build_normalize_messages,
    build_rephrase_messages,
    classify_schema,
)
from .schemas import (
    AnalyzeRequest,
    AnalyzeResult,
    EvidencePass,
    NormalizedComplaint,
    RephraseRequest,
    RephraseResult,
)

log = logging.getLogger("normalizer.pipeline")

_MAX_RETRIES = 1


def _is_clean_english(req: AnalyzeRequest) -> bool:
    """Skip the normalize LLM call when the complaint is already clean English."""
    if req.language and req.language != "en":
        return False
    try:
        req.complaint.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _passthrough_normalized(req: AnalyzeRequest) -> NormalizedComplaint:
    return NormalizedComplaint(
        clean_complaint=req.complaint, language_detected="en", preserved_entities={}
    )


def stage_normalize(req: AnalyzeRequest) -> NormalizedComplaint:
    if _is_clean_english(req):
        log.info("stage=normalize ticket=%s action=passthrough_en", req.ticket_id)
        return _passthrough_normalized(req)
    provider = get_provider()
    messages = build_normalize_messages(req)
    schema = {
        "type": "object",
        "required": ["clean_complaint", "language_detected", "preserved_entities"],
        "properties": {
            "clean_complaint": {"type": "string"},
            "language_detected": {"enum": ["en", "bn", "mixed"]},
            "preserved_entities": {"type": "object"},
        },
    }
    raw = _call_with_retry(provider, messages, schema)
    try:
        return NormalizedComplaint(**raw)
    except ValidationError as exc:
        log.warning("stage=normalize ticket=%s validation_failed fallback", req.ticket_id)
        # cheap guarantee: never lose the original text
        return _passthrough_normalized(req)


def stage_evidence(req: AnalyzeRequest, clean_complaint: str) -> EvidencePass:
    return score_evidence(req, clean_complaint)


def stage_classify(req: AnalyzeRequest, clean_complaint: str,
                   evidence: EvidencePass) -> AnalyzeResult:
    provider = get_provider()
    messages = build_classify_messages(req, clean_complaint, evidence)
    raw = _call_with_retry(provider, messages, classify_schema)
    # coerce: relevant_transaction_id must be a known id or null
    valid_ids = {t.transaction_id for t in req.transaction_history}
    if raw.get("relevant_transaction_id") not in (None, *valid_ids):
        log.warning(
            "stage=classify ticket=%s coerced unknown txn_id=%s -> null",
            req.ticket_id, raw.get("relevant_transaction_id"),
        )
        raw["relevant_transaction_id"] = None
        raw["evidence_verdict"] = "insufficient_data"
    try:
        return AnalyzeResult(**raw)
    except ValidationError as exc:
        log.warning("stage=classify ticket=%s validation_failed fallback err=%s",
                    req.ticket_id, exc.errors()[:3])
        return _fallback_result(req, clean_complaint)


def _fallback_result(req: AnalyzeRequest, clean_complaint: str) -> AnalyzeResult:
    """Conservative default when the LLM reply is unusable. Backend will still
    apply safety rails + human_review rule on top."""
    return AnalyzeResult(
        clean_complaint=clean_complaint or req.complaint,
        relevant_transaction_id=None,
        evidence_verdict="insufficient_data",
        case_type="other",
        severity="low",
        agent_summary="Automated analysis was inconclusive; the complaint could not "
                      "be confidently classified.",
        recommended_next_action="Route to a human agent for manual review.",
        customer_reply=(
            "Thank you for reaching out. We have received your message and a support "
            "agent will review it shortly. Please do not share your PIN or OTP with anyone."
        ),
        reason_codes=["fallback", "needs_review"],
        confidence=0.3,
    )


def _call_with_retry(provider, messages, schema) -> dict:
    """Call provider.complete_json; on JSON/LLM error feed it back once."""
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return provider.complete_json(messages, schema)
        except (LLMError, ValueError, TypeError) as exc:
            last_err = exc
            if attempt < _MAX_RETRIES:
                log.warning("llm_call failed attempt=%d err=%s — retrying", attempt + 1, exc)
                messages = messages + [
                    {"role": "assistant", "content": "(previous reply was invalid)"},
                    {"role": "user", "content": f"Your last reply was invalid: {exc}. Reply again with ONLY the JSON object."},
                ]
            else:
                log.error("llm_call exhausted retries err=%s", exc)
    raise LLMError(f"llm call failed after retries: {last_err}")


def run_analyze(req: AnalyzeRequest) -> AnalyzeResult:
    """Full 1-3 stage pipeline. Never raises on bad LLM output — falls back."""
    log.info("pipeline start ticket=%s lang=%s history=%d",
             req.ticket_id, req.language, len(req.transaction_history))
    try:
        normalized = stage_normalize(req)
    except Exception as exc:  # noqa: BLE001
        log.error("stage=normalize crashed ticket=%s err=%s", req.ticket_id, type(exc).__name__)
        normalized = _passthrough_normalized(req)
    clean = normalized.clean_complaint or req.complaint

    evidence = stage_evidence(req, clean)

    try:
        result = stage_classify(req, clean, evidence)
    except Exception as exc:  # noqa: BLE001
        log.error("stage=classify crashed ticket=%s err=%s", req.ticket_id, type(exc).__name__)
        result = _fallback_result(req, clean)

    # pass the deterministic stage-2 signals through to the backend so it can
    # apply authoritative overrides (ambiguous -> null, established recipient
    # -> inconsistent, duplicate -> later txn).
    result.signals = evidence.signals
    result.top_transaction_id = evidence.top_transaction_id

    log.info("pipeline done ticket=%s verdict=%s case=%s severity=%s",
             req.ticket_id, result.evidence_verdict, result.case_type, result.severity)
    return result


def rephrase(req: RephraseRequest) -> RephraseResult:
    """Stage 4b — rephrase text that tripped the backend safety rail. Falls
    back to a templated safe line per field if the LLM reply is unusable, so the
    backend never receives empty customer-facing text."""
    provider = get_provider()
    messages = build_rephrase_messages(req)
    schema = {
        "type": "object",
        "required": ["rephrased"],
        "properties": {
            "rephrased": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }
    try:
        raw = _call_with_retry(provider, messages, schema)
        rephrased = raw.get("rephrased", {})
        if not isinstance(rephrased, dict):
            raise LLMError("rephrased not an object")
    except Exception as exc:  # noqa: BLE001
        log.error("stage=rephrase crashed err=%s", type(exc).__name__)
        rephrased = {}
    # guarantee every requested field is present + safe-ish
    safe_default = (
        "Thank you for reaching out. Our team will review this and contact you through "
        "official channels. Please do not share your PIN or OTP with anyone."
    )
    out: dict[str, str] = {}
    for field, text in req.texts.items():
        out[field] = rephrased.get(field) or safe_default
    return RephraseResult(rephrased=out)