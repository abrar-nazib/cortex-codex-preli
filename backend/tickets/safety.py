"""Stage 4 — deterministic safety rail (backend, never trusts the LLM).

The §8 safety rules are enforced deterministically here, NOT by asking the LLM
to self-police. We scan customer-facing text (customer_reply + recommended_next_
action) sentence-by-sentence and flag violations. The orchestrator then asks
the normalizer to rephrase, re-scans, and on a second failure either fails loud
(500) or falls back to a templated safe reply (per SAFETY_FAIL_LOUD).

Detection is deliberately conservative on the credential axis: a sentence that
tells the customer NOT to share a PIN/OTP is safe (negated). Only affirmative
requests to share/enter/send a credential are violations.

Multilingual: customer_reply may be in English, Bangla (Bengali script), or
Banglish. The credential/refund/third-party patterns carry Latin AND Bangla
tokens, and the sentence splitter recognizes the Bengali danda (। / ॥). The
prompt also instructs the model to keep PIN/OTP/password/card number as Latin
terms in any language so they are always recognizable here.
"""
from __future__ import annotations

import re

# credential nouns that must never be requested. Latin terms first; the prompt
# instructs the model to keep PIN/OTP/password/card number as Latin in any
# language, but we also catch common Bangla transliterations (পিন/ওটিপি/...).
_CREDENTIAL_RE = re.compile(
    r"\b(pin|otp|o\.t\.p|password|passcode|cvv|cvc|full\s+card|card\s+number|"
    r"16[-\s]?digit|"
    r"পিন|ওটিপি|পাসওয়ার্ড|পাসকোড|কার্ড\s*নম্বর|কার্ড\s*নাম্বার|সিভিভি|সিভিসি)\b",
    re.IGNORECASE,
)
# verbs that, combined with a credential in the same sentence, make a request.
# Latin + Bangla request verbs (দিন/পাঠান/বলুন/জানান/লিখুন/শেয়ার করুন).
_REQUEST_VERB_RE = re.compile(
    r"\b(share|provide|send|enter|confirm|give|type|tell|message|"
    r"দিন|দেও|পাঠান|পাঠাবেন|বলুন|জানান|লিখুন|শেয়ার\s*করুন|শেয়ার\s*করো|জানাবেন)\b",
    re.IGNORECASE,
)
# negation that turns a credential mention into a safe "do not share" reminder.
# Latin + Bangla (না / করবেন না / দয়ে করে / নয়).
_NEGATION_RE = re.compile(
    r"\b(not|never|don(?:'|)t|do\s+not|without|no\s+one|anyone|avoid|refuse|"
    r"না|করবেন\s+না|করবেননা|দয়ে\s*করে|দয়ে\s*করে|নয়|নই)\b",
    re.IGNORECASE,
)

# refund/reversal/unblock promises without authority. Latin + Bangla
# (ফেরত দেব / রিফান্ড করে দেব / আনব্লক / ফেরত পাবেন).
_REFUND_RE = re.compile(
    r"\b("
    r"we\s+(?:will|can|shall|'ll|are\s+going\s+to)\s+(?:refund|reverse|unblock|"
    r"return\s+your\s+money|send\s+(?:back|you))|"
    r"refund\s+you|will\s+be\s+refunded|unblock\s+your\s+account|"
    r"your\s+(?:money|funds)\s+will\s+be\s+(?:refund|return|sent\s+back)|"
    r"ফেরত\s+দেব|ফেরত\s+দেও|রিফান্ড\s+করে?\s*দেব|রিফান্ড\s+পাবেন|"
    r"আনব্লক\s+করে?\s*দেব|টাকা\s+ফেরত\s+দেব|ফেরত\s+পাবেন"
    r")\b",
    re.IGNORECASE,
)
# safe-hedge words that turn a refund mention into an authorized, conditional one.
# Latin + Bangla (অফিশিয়াল চ্যানেল / যোগ্য / নীতি / পারে / হতে পারে).
_REFUND_SAFE_RE = re.compile(
    r"\b(eligible|official\s+channel|if\s+(?:you\s+are\s+)?eligible|may|might|"
    r"where\s+applicable|policy|"
    r"অফিশিয়াল\s*চ্যানেল|অফিসিয়াল\s*চ্যানেল|যোগ্য|নীতি|হতে\s*পারে|পেতে\s*পারে|পারে)\b",
    re.IGNORECASE,
)

# instructing the customer to contact a third party / unknown number.
# Latin + Bangla (যোগাযোগ করুন / কল করুন / তাকে কথা বলুন).
_THIRD_PARTY_RE = re.compile(
    r"\b(contact|call|reach|message|whatsapp|sms)\s+"
    r"(?:him|her|them|that\s+(?:person|number|agent)|the\s+(?:number|person|agent|caller))\b|"
    r"(?:তাকে|তার\s+সাথে|ওই\s+নম্বরে|সেই\s+নম্বরে)\s+(?:কল\s*করুন|যোগাযোগ\s*করুন|কথা\s*বলুন)|"
    r"(?:কল\s*করুন|যোগাযোগ\s*করুন)\s+(?:তাকে|তার\s+সাথে|ওই\s+নম্বরে|সেই\s+নম্বরে)",
    re.IGNORECASE,
)

# Sentence splitter. Splits on latin [.!?] + whitespace (keeps decimals like
# 3.14 intact) and on the Bengali danda । / ॥ with optional following space
# (Bangla sentences often have no space after ।).
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|(?<=[।॥])\s*")


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