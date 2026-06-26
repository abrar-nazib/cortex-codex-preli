"""Single source of truth for constants, enums, routing tables, safety phrases.

Everything that might change lives here. No other module should hard-code
these values. The rest of the package imports from this file.
"""
from __future__ import annotations

import enum
import re
from typing import Dict, List, Pattern


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class _StrEnum(str, enum.Enum):
    """str-mixin Enum so values serialize as plain strings in JSON."""
    pass


class CaseType(_StrEnum):
    WRONG_TRANSFER = "wrong_transfer"
    PAYMENT_FAILED = "payment_failed"
    REFUND_REQUEST = "refund_request"
    DUPLICATE_PAYMENT = "duplicate_payment"
    MERCHANT_SETTLEMENT_DELAY = "merchant_settlement_delay"
    AGENT_CASH_IN_ISSUE = "agent_cash_in_issue"
    PHISHING_OR_SOCIAL_ENGINEERING = "phishing_or_social_engineering"
    OTHER = "other"


class Severity(_StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Severity ordering for upward-flooring checks.
SEVERITY_ORDER: Dict[Severity, int] = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class Department(_StrEnum):
    CUSTOMER_SUPPORT = "customer_support"
    DISPUTE_RESOLUTION = "dispute_resolution"
    PAYMENTS_OPS = "payments_ops"
    MERCHANT_OPERATIONS = "merchant_operations"
    AGENT_OPERATIONS = "agent_operations"
    FRAUD_RISK = "fraud_risk"


class EvidenceVerdict(_StrEnum):
    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    INSUFFICIENT_DATA = "insufficient_data"


class Channel(_StrEnum):
    IN_APP_CHAT = "in_app_chat"
    CALL_CENTER = "call_center"
    EMAIL = "email"
    MERCHANT_PORTAL = "merchant_portal"
    FIELD_AGENT = "field_agent"


class Language(_StrEnum):
    EN = "en"
    BN = "bn"
    MIXED = "mixed"


class UserType(_StrEnum):
    CUSTOMER = "customer"
    MERCHANT = "merchant"
    AGENT = "agent"
    UNKNOWN = "unknown"


class TransactionType(_StrEnum):
    TRANSFER = "transfer"
    PAYMENT = "payment"
    CASH_IN = "cash_in"
    CASH_OUT = "cash_out"
    SETTLEMENT = "settlement"
    REFUND = "refund"


class TransactionStatus(_StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING = "pending"
    REVERSED = "reversed"


# ---------------------------------------------------------------------------
# Routing & severity tables
# ---------------------------------------------------------------------------

CASE_TYPE_TO_DEPARTMENT: Dict[CaseType, Department] = {
    CaseType.WRONG_TRANSFER: Department.DISPUTE_RESOLUTION,
    CaseType.PAYMENT_FAILED: Department.PAYMENTS_OPS,
    CaseType.REFUND_REQUEST: Department.CUSTOMER_SUPPORT,
    CaseType.DUPLICATE_PAYMENT: Department.PAYMENTS_OPS,
    CaseType.MERCHANT_SETTLEMENT_DELAY: Department.MERCHANT_OPERATIONS,
    CaseType.AGENT_CASH_IN_ISSUE: Department.AGENT_OPERATIONS,
    CaseType.PHISHING_OR_SOCIAL_ENGINEERING: Department.FRAUD_RISK,
    CaseType.OTHER: Department.CUSTOMER_SUPPORT,
}


CASE_TYPE_TO_BASE_SEVERITY: Dict[CaseType, Severity] = {
    CaseType.WRONG_TRANSFER: Severity.HIGH,
    CaseType.PAYMENT_FAILED: Severity.HIGH,
    CaseType.REFUND_REQUEST: Severity.LOW,
    CaseType.DUPLICATE_PAYMENT: Severity.HIGH,
    CaseType.MERCHANT_SETTLEMENT_DELAY: Severity.MEDIUM,
    CaseType.AGENT_CASH_IN_ISSUE: Severity.HIGH,
    CaseType.PHISHING_OR_SOCIAL_ENGINEERING: Severity.CRITICAL,
    CaseType.OTHER: Severity.LOW,
}


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

HIGH_VALUE_THRESHOLD: int = 10000           # BDT — bumps severity one level
CRITICAL_VALUE_THRESHOLD: int = 50000        # BDT — forces critical
TIME_HARD_MISMATCH_DAYS: int = 7             # flag if complaint time vs tx timestamp > N days
AMOUNT_TOLERANCE: float = 0.02              # 2% amount-match tolerance


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_COMPLAINT_LENGTH: int = 1000            # truncate raw complaint before LLM
MAX_CLEANED_LENGTH: int = 300                # truncate cleaned complaint in Stage 3 prompt
MAX_TOKENS_STAGE1: int = 600
MAX_TOKENS_STAGE2: int = 400
MAX_TOKENS_STAGE3: int = 1200
MAX_TOKENS_STAGE4: int = 400


# ---------------------------------------------------------------------------
# Network / runtime
# ---------------------------------------------------------------------------

PORT: int = 9000                            # locked — see CLAUDE.md
LLM_TIMEOUT_S: float = 10.0                 # per-call hard timeout
LLM_TEMPERATURE: float = 0.1                # per CLAUDE.md v3 LLM call pattern


# ---------------------------------------------------------------------------
# Injection patterns (Stage 1 pre-LLM scan)
# ---------------------------------------------------------------------------

INJECTION_PATTERNS: List[str] = [
    "IGNORE",
    "SYSTEM:",
    "[INST]",
    "ignore previous",
    "ignore all",
    "you are now",
    "new instruction",
    "override",
    "forget your",
    "disregard",
    "</system>",
    "respond only with",
    "ignore the above",
    "disregard previous",
]


# ---------------------------------------------------------------------------
# Safety blocked phrases (Stage 4 code enforcement)
# ---------------------------------------------------------------------------

CREDENTIAL_REQUEST_PHRASES: List[str] = [
    "your pin",
    "your otp",
    "your password",
    "card number",
    "cvv",
    "share your",
    "provide your",
    "enter your pin",
    "verify your pin",
    "for verification please",
    "send your otp",
    "tell me your",
]


UNAUTHORIZED_PROMISE_PHRASES: List[str] = [
    "we will refund",
    "you will receive",
    "guaranteed",
    "we promise",
    "confirmed refund",
    "your money will be returned",
    "we can recover",
    "we'll refund",
    "will be refunded",
    "definitely get back",
    "sure to receive",
]


SAFETY_BLOCKED_PHRASES: List[str] = (
    CREDENTIAL_REQUEST_PHRASES + UNAUTHORIZED_PROMISE_PHRASES
)


SAFETY_REPLACEMENT_CREDENTIAL: str = (
    "Please contact official bKash support for assistance."
)
SAFETY_REPLACEMENT_PROMISE: str = (
    "Any eligible amount will be processed through official channels."
)
SAFETY_REPLACEMENT_THIRD_PARTY: str = (
    "Please use official bKash support channels only."
)
SAFETY_REPLACEMENT_GUARANTEE_ACTION: str = (
    "Escalate to {department} team for investigation and resolution."
)


# ---------------------------------------------------------------------------
# Third-party patterns (phone numbers, unofficial URLs)
# ---------------------------------------------------------------------------

ALLOWED_URL_HOSTS: tuple = ("bkash.com",)

PHONE_PATTERN: Pattern[str] = re.compile(
    r"(?:(?:\+?88)?01[3-9]\d{8})|(?:\+?\d{1,3}[-.\s]?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4})"
)

URL_PATTERN: Pattern[str] = re.compile(
    r"(?:https?://|www\.)[^\s,;'\"]+",
    re.IGNORECASE,
)

THIRD_PARTY_PATTERNS: Dict[str, Pattern[str]] = {
    "phone": PHONE_PATTERN,
    "url": URL_PATTERN,
}


# ---------------------------------------------------------------------------
# Time reference detection (used by Stage 2 to compute time_hard_mismatch)
# ---------------------------------------------------------------------------

RELATIVE_TIME_OFFSETS: Dict[str, int] = {
    "today": 0,
    "yesterday": 1,
    "day before yesterday": 2,
    "last night": 0,
    "this morning": 0,
    "this afternoon": 0,
    "this evening": 0,
    "সকালে": 0,
    "গতকাল": 1,
    "আজ": 0,
}


# ---------------------------------------------------------------------------
# Phishing keyword list
# ---------------------------------------------------------------------------

PHISHING_KEYWORDS: List[str] = [
    "otp",
    "pin",
    "password",
    "blocked my account",
    "share your otp",
    "share your pin",
    "verify your account",
    "customer service representative",
    "called me asking",
    "asked for my otp",
    "asked for my pin",
    "social engineering",
    "phishing",
    "fraud call",
    "fake sms",
]


# ---------------------------------------------------------------------------
# Case-type keywords (used by Stage 3 fallback and Stage 1 keyword extraction)
# ---------------------------------------------------------------------------

CASE_TYPE_KEYWORDS: Dict[CaseType, List[str]] = {
    CaseType.WRONG_TRANSFER: ["wrong", "wrong number", "wrong person", "incorrect", "mistaken", "ভুল নম্বর", "ভুল"],
    CaseType.PAYMENT_FAILED: ["failed", "didn't go through", "not processed", "balance deducted", "ব্যালেন্স কাটা", "ব্যর্থ"],
    CaseType.REFUND_REQUEST: ["refund", "money back", "return my money", "ফেরত", "টাকা ফেরত"],
    CaseType.DUPLICATE_PAYMENT: ["twice", "duplicate", "charged twice", "double charged", "দুইবার"],
    CaseType.MERCHANT_SETTLEMENT_DELAY: ["settlement", "merchant", "payout", "সেটেলমেন্ট", "মার্চেন্ট"],
    CaseType.AGENT_CASH_IN_ISSUE: ["cash in", "agent", "deposit", "ক্যাশ ইন", "এজেন্ট"],
    CaseType.PHISHING_OR_SOCIAL_ENGINEERING: [
        "otp", "pin", "phishing", "blocked my account", "share your", "asked for my",
    ],
}


# ---------------------------------------------------------------------------
# Bilingual safe-phrase templates for customer_reply
# ---------------------------------------------------------------------------

def _safe_reply_en(case_type: CaseType, tx_id: str | None) -> str:
    if case_type == CaseType.PHISHING_OR_SOCIAL_ENGINEERING:
        return (
            "Thank you for reaching out before sharing any information. "
            "We never ask for your PIN, OTP, or password under any circumstances. "
            "Our fraud team has been notified of this incident. "
            "Please do not share these with anyone, even if they claim to be from us."
        )
    if case_type == CaseType.PAYMENT_FAILED:
        return (
            f"We have noted that transaction {tx_id} may have caused an unexpected "
            "balance deduction. Our payments team will review the case and any eligible "
            "amount will be processed through official channels. Please do not share your "
            "PIN or OTP with anyone."
        )
    if case_type == CaseType.WRONG_TRANSFER:
        return (
            f"We have noted your concern about transaction {tx_id}. "
            "Our dispute team will review the case and contact you through official "
            "support channels. Please do not share your PIN or OTP with anyone."
        )
    if case_type == CaseType.DUPLICATE_PAYMENT:
        return (
            f"We have noted the possible duplicate payment for transaction {tx_id}. "
            "Our payments team will verify with the biller and any eligible amount will be "
            "processed through official channels. Please do not share your PIN or OTP with anyone."
        )
    if case_type == CaseType.AGENT_CASH_IN_ISSUE:
        return (
            f"We have noted your concern about transaction {tx_id}. "
            "Our agent operations team will verify this quickly and update you through "
            "official channels. Please do not share your PIN or OTP with anyone."
        )
    if case_type == CaseType.REFUND_REQUEST:
        return (
            "Thank you for reaching out. Refunds for completed merchant payments depend "
            "on the merchant's own policy. We recommend contacting the merchant directly. "
            "If you need help reaching them, please reply and we will guide you."
        )
    if case_type == CaseType.MERCHANT_SETTLEMENT_DELAY:
        return (
            f"We have noted your concern about settlement {tx_id}. "
            "Our merchant operations team will check the batch status and update you on "
            "the expected settlement time through official channels."
        )
    return (
        "We have received your complaint and will investigate through official channels. "
        "Any eligible amount will be processed accordingly."
    )


def _safe_reply_bn(case_type: CaseType, tx_id: str | None) -> str:
    tx_phrase = f" {tx_id}" if tx_id else ""
    if case_type == CaseType.PHISHING_OR_SOCIAL_ENGINEERING:
        return (
            "তথ্য শেয়ার না করে আমাদের জানানোর জন্য ধন্যবাদ। "
            "আমরা কখনো আপনার পিন, ওটিপি বা পাসওয়ার্ড চাই না। "
            "আমাদের ফ্রড টিম এই ঘটনা সম্পর্কে অবহিত হয়েছে। "
            "অনুগ্রহ করে কারো সাথে এগুলো শেয়ার করবেন না, এমনকি তারা নিজেদের আমাদের বললেও।"
        )
    if case_type == CaseType.PAYMENT_FAILED:
        return (
            f"আপনার লেনদেন{tx_phrase} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের পেমেন্টস টিম "
            "এটি পর্যালোচনা করবে এবং কোনো যোগ্য পরিমাণ অফিসিয়াল চ্যানেলে প্রক্রিয়া করা হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        )
    if case_type == CaseType.WRONG_TRANSFER:
        return (
            f"আপনার লেনদেন{tx_phrase} সংক্রান্ত অভিযোগ আমরা পেয়েছি। "
            "আমাদের ডিসপিউট টিম এটি পর্যালোচনা করবে এবং অফিসিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        )
    if case_type == CaseType.DUPLICATE_PAYMENT:
        return (
            f"সম্ভাব্য ডুপ্লিকেট পেমেন্ট{tx_phrase} আমরা লক্ষ্য করেছি। "
            "আমাদের পেমেন্টস টিম বিলারের সাথে যাচাই করবে এবং কোনো যোগ্য পরিমাণ অফিসিয়াল চ্যানেলে প্রক্রিয়া করা হবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        )
    if case_type == CaseType.AGENT_CASH_IN_ISSUE:
        return (
            f"আপনার লেনদেন{tx_phrase} এর বিষয়ে আমরা অবগত হয়েছি। "
            "আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে। "
            "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        )
    if case_type == CaseType.REFUND_REQUEST:
        return (
            "যোগাযোগ করার জন্য ধন্যবাদ। সম্পন্ন মার্চেন্ট পেমেন্টের রিফান্ড মার্চেন্টের নিজস্ব নীতির উপর নির্ভর করে। "
            "আমরা সরাসরি মার্চেন্টের সাথে যোগাযোগের পরামর্শ দিই।"
        )
    if case_type == CaseType.MERCHANT_SETTLEMENT_DELAY:
        return (
            f"আপনার সেটেলমেন্ট{tx_phrase} এর বিষয়ে আমরা অবগত হয়েছি। "
            "আমাদের মার্চেন্ট অপারেশন্স টিম ব্যাচের স্ট্যাটাস পরীক্ষা করে অফিসিয়াল চ্যানেলে আপনাকে জানাবে।"
        )
    return (
        "আমরা আপনার অভিযোগ পেয়েছি এবং অফিসিয়াল চ্যানেলে তদন্ত করব। "
        "কোনো যোগ্য পরিমাণ সেই অনুযায়ী প্রক্রিয়া করা হবে।"
    )


SAFE_REPLY_BUILDERS: Dict[CaseType, Dict[str, callable]] = {
    ct: {"en": _safe_reply_en, "bn": _safe_reply_bn} for ct in CaseType
}


def safe_reply_for(case_type: CaseType, language: str, tx_id: str | None = None) -> str:
    by_lang = SAFE_REPLY_BUILDERS.get(case_type, SAFE_REPLY_BUILDERS[CaseType.OTHER])
    builder = by_lang.get(language) or by_lang["en"]
    return builder(case_type, tx_id)


STAGE3_FALLBACK_REPLY: str = (
    "We have received your complaint and will investigate through official channels. "
    "Any eligible amount will be processed accordingly."
)
STAGE3_FALLBACK_REPLY_BN: str = (
    "আমরা আপনার অভিযোগ পেয়েছি এবং অফিসিয়াল চ্যানেলে তদন্ত করব। "
    "কোনো যোগ্য পরিমাণ সেই অনুযায়ী প্রক্রিয়া করা হবে।"
)