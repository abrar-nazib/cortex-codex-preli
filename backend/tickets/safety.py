"""Stage 4 — deterministic safety rail (backend, never trusts the LLM).

The §8 safety rules are enforced deterministically here, NOT by asking the LLM
to self-police. We scan customer-facing text (customer_reply + recommended_next_
action) sentence-by-sentence and flag violations. The orchestrator then asks
the normalizer to rephrase, re-scans, and on a second failure either fails loud
(500) or falls back to a templated safe reply (per SAFETY_FAIL_LOUD).

Detection is deliberately conservative on the credential axis: a sentence that
tells the customer NOT to share a PIN/OTP is safe (negated). Only affirmative
requests to share/enter/send a credential are violations.
"""
from __future__ import annotations

import re

# credential nouns that must never be requested
_CREDENTIAL_RE = re.compile(
    r"\b(pin|otp|o\.t\.p|password|passcode|cvv|cvc|full\s+card|card\s+number|"
    r"16[-\s]?digit)\b",
    re.IGNORECASE,
)
# verbs that, combined with a credential in the same sentence, make a request
_REQUEST_VERB_RE = re.compile(
    r"\b(share|provide|send|enter|confirm|give|type|tell|enter|message)\b",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(
    r"\b(not|never|don(?:'|)t|do\s+not|without|no\s+one|anyone|avoid|refuse)\b",
    re.IGNORECASE,
)

# refund/reversal/unblock promises without authority
_REFUND_RE = re.compile(
    r"\b("
    r"we\s+(?:will|can|shall|'ll|are\s+going\s+to)\s+(?:refund|reverse|unblock|"
    r"return\s+your\s+money|send\s+(?:back|you))|"
    r"refund\s+you|will\s+be\s+refunded|unblock\s+your\s+account|"
    r"your\s+(?:money|funds)\s+will\s+be\s+(?:refund|return|sent\s+back)"
    r")\b",
    re.IGNORECASE,
)
# safe-hedge words that turn a refund mention into an authorized, conditional one
_REFUND_SAFE_RE = re.compile(
    r"\b(eligible|official\s+channel|if\s+(?:you\s+are\s+)?eligible|may|might|"
    r"where\s+applicable|policy)\b",
    re.IGNORECASE,
)

# instructing the customer to contact a third party / unknown number
_THIRD_PARTY_RE = re.compile(
    r"\b(contact|call|reach|message|whatsapp|sms)\s+"
    r"(?:him|her|them|that\s+(?:person|number|agent)|the\s+(?:number|person|agent|caller))\b",
    re.IGNORECASE,
)

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _sentences(text: str) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def find_safety_violations(text: str) -> list[str]:
    """Return a list of human-readable violation descriptions for `text`.

    Empty list = safe. Each violation names the rule breached; these strings are
    forwarded to the normalizer /rephrase endpoint as context.
    """
    violations: list[str] = []
    for sent in _sentences(text):
        low = sent.lower()
        # ── credential request ──────────────────────────────────────────────
        if _CREDENTIAL_RE.search(sent) and _REQUEST_VERB_RE.search(sent):
            if not _NEGATION_RE.search(sent):
                violations.append(
                    "Sentence appears to request a credential (PIN/OTP/password/"
                    "card number): " + sent
                )
        # ── unauthorized refund/reversal/unblock promise ─────────────────────
        m = _REFUND_RE.search(sent)
        if m and not _REFUND_SAFE_RE.search(sent):
            violations.append(
                "Sentence promises a refund/reversal/unblock without authority: " + sent
            )
        # ── third-party contact instruction ──────────────────────────────────
        if _THIRD_PARTY_RE.search(sent):
            violations.append(
                "Sentence instructs the customer to contact a third party / unknown "
                "number: " + sent
            )
    return violations


def is_safe(text: str) -> bool:
    return not find_safety_violations(text)


# Templated safe fallback used when rephrase also fails. Guaranteed safe.
SAFE_FALLBACK_REPLY = (
    "Thank you for reaching out. Our team will review your case and contact you "
    "through official channels. Please do not share your PIN or OTP with anyone."
)