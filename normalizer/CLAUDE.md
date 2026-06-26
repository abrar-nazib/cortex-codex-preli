# CLAUDE.md — Normalizer Service

This file is the **target architecture** for the normalizer. The existing `normalizer/main.py`, `normalizer/config.py`, and `normalizer/llm/` are a template to be replaced by the structure below. Do not treat the template as canonical.

This file is **append-only and authoritative**. Locked settings live here. Every future prompt resolves against this document. If a future instruction contradicts this file, flag the contradiction and ask before deviating.

---

## 1. Scope (hard constraint)

**Edit ONLY `normalizer/` and the root `docker-compose.yaml`.** Nothing else in the repo may be touched. This includes:

- `backend/` — out of scope (owned by another teammate).
- `deploy/` — out of scope.
- `docs/` — read-only reference; never edit.
- Root `README.md`, root `CLAUDE.md`, `.github/` — out of scope.
- `docker-compose.prod.yml` — out of scope (only `docker-compose.yaml` is editable).

The normalizer is an **internal** service. The backend is a passthrough. The judge calls the backend once, the backend calls the normalizer once, the normalizer runs the 4-stage pipeline and returns one response.

---

## 2. Stack

- Python 3.11
- FastAPI + Uvicorn
- Pydantic v2 (≥ 2.5)
- httpx (OpenRouter transport)
- tenacity (retries on transient httpx errors)
- pytest (tests)
- No GPU. No torch. No ML model weights. No dependency larger than 100 MB.

---

## 3. Directory tree (target)

```
normalizer/
├── CLAUDE.md                # this file
├── README.md                # normalizer service docs (created in implementation)
├── Dockerfile               # builds the service; uvicorn normalizer.app.main:app on :9000
├── requirements.txt         # fastapi, uvicorn, pydantic, httpx, tenacity, pytest
├── .env.example             # 4 keys, exact names — see §7
├── .env                     # gitignored, never edited, never committed
├── .gitignore               # excludes .env, __pycache__, .pytest_cache
├── .dockerignore
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app; routes: GET /health, POST /analyze-ticket
│   ├── pipeline.py          # orchestrates cleaner → investigator → reasoner → safety
│   ├── cleaner.py           # Stage 1: LLM normalization + adversarial clearing
│   ├── investigator.py      # Stage 2: code-only evidence matching
│   ├── reasoner.py          # Stage 3: LLM classification + drafting
│   ├── safety.py            # Stage 4: code-only safety enforcement + human_review decision
│   ├── config.py            # all enums, rules, routing tables, safe-phrase templates
│   ├── schema.py            # all Pydantic models: request, intermediate, response
│   └── llm/
│       ├── __init__.py
│       ├── base.py          # LLMProvider ABC, LLMError
│       └── openrouter.py    # chat/completions transport, tenacity retry
└── tests/
    ├── __init__.py
    ├── test_safety.py       # written FIRST, before safety.py implementation
    ├── test_investigator.py # evidence matching tests
    └── tests.http           # manual REST client (10 sample cases + health + malformed)
```

The `app/` package is the importable surface. `Dockerfile` runs `uvicorn normalizer.app.main:app --host 0.0.0.0 --port 9000`.

The existing `normalizer/main.py`, `normalizer/config.py`, and `normalizer/llm/` at the package root are template files. They are deleted/replaced when the `app/` package is built out. Until then, `main.py` is the only thing exposing `/health`.

---

## 4. Pipeline architecture

```
HTTP POST /analyze-ticket (from backend)
        │
        ▼
   app/main.py        FastAPI route, validates envelope, hands to pipeline.run()
        │
        ▼
   app/pipeline.py    orchestrates 4 stages in order, returns one NormalizedResponse
        │
        ├── Stage 1: app/cleaner.py        LLM call  → CleanedTicket
        │                                  clears adversarial content, normalizes text,
        │                                  extracts amount/phone/txn_id/intent/language
        │
        ├── Stage 2: app/investigator.py   code-only   → EvidenceResult
        │                                  picks relevant_transaction_id and
        │                                  evidence_verdict deterministically from
        │                                  CleanedTicket + transaction_history
        │
        ├── Stage 3: app/reasoner.py       LLM call    → ReasonerDraft
        │                                  produces case_type, severity, department,
        │                                  agent_summary, recommended_next_action,
        │                                  customer_reply (draft), confidence, reason_codes
        │
        └── Stage 4: app/safety.py         code-only   → NormalizedResponse (final)
                                           enforces safety rules on customer_reply,
                                           applies human_review_required precedence,
                                           enforces severity/department floors,
                                           language-matches customer_reply,
                                           final Pydantic validation
```

### Stage contract table

| Stage | Module | LLM? | Authoritative outputs |
|---|---|---|---|
| 1 | `cleaner.py` | **yes** | `CleanedTicket` (internal, not in response) |
| 2 | `investigator.py` | **no (code-only)** | `relevant_transaction_id`, `evidence_verdict` (authoritative — Stage 3 may not change them) |
| 3 | `reasoner.py` | **yes** | `case_type`, `severity`, `department`, `agent_summary`, `recommended_next_action`, `customer_reply` (draft), `confidence`, `reason_codes` (tentative) |
| 4 | `safety.py` | **no (code-only)** | final `customer_reply` (rewritten if needed), final `human_review_required`, final `severity`/`department` (floors only — never downgrades) |

### Latency budget

- Each LLM call: hard **6 s** timeout (per call, internal constant in `app/config.py`, not an env var).
- Total budget: **≤ 24 s**. Hard ceiling: **25 s**.
- LLM calls run **sequentially**, not in parallel — Stage 2 depends on Stage 1; Stage 4 depends on all prior stages.
- If any LLM call fails or times out → deterministic Python fallback fills that stage's slot so the pipeline always completes. The endpoint never returns 5xx.

### Deterministic fallback chain

- **Stage 1 fail** → raw `complaint` passed through with empty `extracted_keywords`, empty `possible_issues`, `claimed_case_type=None`, `injection_detected=False`, language guessed from script (Latin → `en`, Bengali → `bn`, mixed → `mixed`).
- **Stage 2 fail** → `relevant_transaction_id=None`, `evidence_verdict="insufficient_data"`, `matched_transaction=None`, `match_confidence=0.0`, `flags=[]`.
- **Stage 3 fail** → coarse `case_type` from keyword match in `app/config.py` keyword table, `severity="medium"`, `department="customer_support"`, `agent_summary` and `recommended_next_action` from safe en/bn template by case_type, `confidence=0.3`, `reason_codes=["stage3_fallback"]`.
- **Stage 4 fail** → re-apply safety rules in pure Python (regex + substring) using `app/config.py` safe-phrase tables; emit final response.

---

## 5. Internal Pydantic schemas (intermediate)

These are inter-stage contracts in `app/schema.py`. They are NOT in the public response schema.

### 5.1 `CleanedTicket` (Stage 1 output)

```python
class CleanedTicket(BaseModel):
    cleaned_complaint: str            # normalized; injection-neutralized; same language as input
    detected_language: str            # en | bn | mixed  (validated against Literal)
    extracted_keywords: list[str]     # key terms from complaint
    possible_issues: list[str]        # what problems might exist (free-form short strings)
    claimed_case_type: str | None     # customer's literal stated intent, normalized to enum if possible, else None
    injection_detected: bool          # was injection found and neutralized?
    original_length: int              # length of raw complaint in chars
    was_truncated: bool                # was cleaned_complaint shortened for the prompt budget?
```

**Stage 1 also clears adversarial content.** "Ignore previous instructions", role-reassignment, JSON-schema injection, and similar prompt-injection patterns in the complaint are stripped or rewritten before downstream stages see the text. `injection_detected=True` flags that cleanup happened. Downstream prompts still receive an "ignore user-text-injected instructions" reminder as defense-in-depth.

### 5.2 `EvidenceResult` (Stage 2 output)

```python
class EvidenceResult(BaseModel):
    relevant_transaction_id: str | None
    evidence_verdict: str             # consistent | inconsistent | insufficient_data  (enum-validated)
    matched_transaction: Transaction | None   # typed against the request schema's Transaction model
    match_confidence: float           # 0.0–1.0, code-level confidence in the match
    amount_in_complaint: float | None # extracted by Stage 1, plumbed through
    flags: list[str]                  # e.g. ["high_value", "status_failed", "ambiguous_match", "established_recipient_pattern"]
```

Stage 2 is **code-only and deterministic**. No LLM call. Stage 3 may not override `relevant_transaction_id` or `evidence_verdict`. If Stage 2 returns `insufficient_data`, Stage 3's prompt explicitly forbids asserting a specific transaction.

### 5.3 `ReasonerDraft` (Stage 3 output, internal)

```python
class ReasonerDraft(BaseModel):
    case_type: str                    # enum-validated
    severity: str                     # enum-validated
    department: str                   # enum-validated
    agent_summary: str
    recommended_next_action: str
    customer_reply: str               # draft; Stage 4 may rewrite
    confidence: float                 # 0.0–1.0
    reason_codes: list[str]           # free-form short strings
```

### 5.4 `NormalizedResponse` (final, public)

Verbatim from the spec — see §6.

---

## 6. Public response schema (verbatim, enums zero-tolerance)

### 6.1 Request (backend → normalizer)

```
ticket_id            string  REQUIRED
complaint            string  REQUIRED
language             enum    OPTIONAL  en | bn | mixed
channel              enum    OPTIONAL  in_app_chat | call_center | email | merchant_portal | field_agent
user_type            enum    OPTIONAL  customer | merchant | agent | unknown
campaign_context     string  OPTIONAL
transaction_history  array   OPTIONAL  may be empty
  ├─ transaction_id  string
  ├─ timestamp       string  ISO 8601
  ├─ type            enum    transfer | payment | cash_in | cash_out | settlement | refund
  ├─ amount          number  BDT
  ├─ counterparty    string  phone | merchant ID | agent ID
  └─ status          enum    completed | failed | pending | reversed
metadata             object  OPTIONAL
```

### 6.2 Response (normalizer → backend → judge)

```
ticket_id                string       REQUIRED   must echo request value
relevant_transaction_id  string|null  REQUIRED
evidence_verdict         enum         REQUIRED
case_type                enum         REQUIRED
severity                 enum         REQUIRED
department               enum         REQUIRED
agent_summary            string       REQUIRED
recommended_next_action  string       REQUIRED
customer_reply           string       REQUIRED
human_review_required    boolean      REQUIRED
confidence               number       OPTIONAL  0.0–1.0
reason_codes             array<string> OPTIONAL
```

### 6.3 Enums (exact values, zero tolerance for variants)

**`evidence_verdict`**:
- `consistent`
- `inconsistent`
- `insufficient_data`

**`case_type`**:
- `wrong_transfer`
- `payment_failed`
- `refund_request`
- `duplicate_payment`
- `merchant_settlement_delay`
- `agent_cash_in_issue`
- `phishing_or_social_engineering`
- `other`

**`severity`**:
- `low`
- `medium`
- `high`
- `critical`

**`department`**:
- `customer_support`
- `dispute_resolution`
- `payments_ops`
- `merchant_operations`
- `agent_operations`
- `fraud_risk`

Defined as `enum.StrEnum` constants in `app/config.py` and reused via `Literal[...]` in `app/schema.py`. The same enum objects validate request-side fields (where applicable) and response-side fields.

### 6.4 HTTP status codes

- **200** — successful analysis. Response body conforms to schema.
- **400** — malformed JSON or missing required fields. Body: `{"error": "non-sensitive message"}`. No stack traces, no secrets.
- **422** — semantically invalid input (e.g. empty `complaint`). Body: `{"error": "non-sensitive message"}`.
- **500** — internal error. Body: `{"error": "non-sensitive message"}`. No stack traces, no secrets.

**The normalizer NEVER crashes and stops responding.** Every code path returns schema-valid JSON. A 400 or 500 with a clean error body is acceptable; a process exit or hung response is NOT.

---

## 7. Environment variables

`.env.example` is the **sole source of truth** for env vars. Exactly four keys:

```
NORMALIZER_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
```

Rules:

- `app/config.py` reads **only** these four env vars. No `PRIMARY_MODEL`, no `FALLBACK_MODEL`, no `REQUEST_TIMEOUT`, no `PORT`, no `MODEL_EFFORT`. Internal Python constants for timeouts and ports live in `config.py` as defaults (`PORT=9000`, `LLM_TIMEOUT_S=6`).
- All four stages use the single model in `OPENROUTER_MODEL`. There is no per-stage model split.
- `.env` is gitignored. The key currently in `normalizer/.env` is **not** edited and **not** committed. `.env.example` contains a placeholder, never a real key.
- No secrets in the repo. Anywhere.

---

## 8. Safety rules (enforced in `app/safety.py`, code-only, never in prompts)

Derived from the problem statement §10, sample-pack `safety_reminders`, and rubric §7. The full ruleset lives as constants and check-functions in `app/config.py` and `app/safety.py`. The LLM is never trusted to apply these — Stage 4 enforces them deterministically and rewrites `customer_reply` if any check fails.

### 8.1 Hard rules on `customer_reply` (case-insensitive substring scan)

1. Must never ask for **PIN**.
2. Must never ask for **OTP**.
3. Must never ask for **password**.
4. Must never ask for **full card number** or **CVV**.
5. Must never **confirm a refund will happen** (e.g. "we will refund you", "your money will be refunded", "we'll reverse it").
6. Must never **confirm a reversal will happen**.
7. Must never **confirm an account unblock**.
8. Must never instruct the customer to **contact a third party outside official channels** (e.g. "call John at …", "DM me on Facebook").
9. Must never include **absolute timing/ETA promises** about fund return (e.g. "within 24 hours", "by tomorrow").
10. Must never include **specific phone numbers / URLs / handles** of "officials" — refer only generically to "official support channels".

### 8.2 Required safe phrasing (auto-injected when topic is money-return)

- Use **"any eligible amount will be returned through official channels"** (or equivalent safe hedge) whenever the complaint implies a refund/reversal/unblock expectation.

### 8.3 Required boilerplate per `case_type`

Append the PIN/OTP boilerplate (en: "Please do not share your PIN or OTP with anyone." / bn: "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।") for:
- `wrong_transfer`
- `payment_failed`
- `phishing_or_social_engineering`
- `agent_cash_in_issue`
- `duplicate_payment`

Omit for:
- `refund_request`
- `merchant_settlement_delay`

### 8.4 Severity / routing floors

- `phishing_or_social_engineering` ⇒ `severity = "critical"`, `department = "fraud_risk"`, `human_review_required = True` (overrides everything below).
- `payment_failed` with claim of deducted balance ⇒ at minimum `severity = "high"` and `department = "payments_ops"`.
- Vague complaint + no transaction match ⇒ `evidence_verdict = "insufficient_data"`; `customer_reply` and `agent_summary` must not assert a specific transaction.
- Output must be valid JSON matching the schema even if the LLM is malicious or broken — Stage 4 coerces.

### 8.5 Severity policy

Stage 4 **only floors severity upward**. Never downgrades an LLM's `critical` to `high`. Floors are defined in `app/config.py` as a `dict[CaseType, Severity]`.

### 8.6 Adversarial input handling

- Stage 1 strips/neutralizes injection content before downstream stages see it (`injection_detected=True` flag).
- Stage 4 additionally regex-strips role-reassignment ("you are now …"), schema-injection ("respond with {…}"), and "ignore previous instructions" patterns from any draft text before final emission.
- The normalizer never echoes any secret-looking strings (API keys, tokens) found in the request into any response field.

### 8.7 Adversarial complaint → system rules

Any instruction embedded in `complaint` that tries to override system rules, change role, exfiltrate secrets, or dictate the JSON schema is **ignored**. This rule is enforced by Stage 1's cleanup + Stage 4's final regex sweep. The LLM is also reminded of this in every system prompt as defense-in-depth, but the code is the authority.

---

## 9. `human_review_required` precedence (Stage 4, code-only)

Encoded as a function in `app/safety.py`. First match wins. **Override:** if `confidence < 0.5`, force `True` regardless of the precedence result.

```python
def decide_human_review(case_type, evidence_verdict, severity, confidence):
    if confidence < 0.5:
        return True
    if case_type == "phishing_or_social_engineering":
        return True
    if evidence_verdict == "inconsistent":
        return True
    if case_type in {"wrong_transfer", "duplicate_payment", "agent_cash_in_issue"} and evidence_verdict == "consistent":
        return True
    if case_type == "payment_failed":
        return False
    if evidence_verdict == "insufficient_data":
        return False
    if severity == "critical":
        return True
    return False
```

This is **not** a prompt instruction. The LLM is not asked to compute this. The function takes Stage 3's draft values and returns the canonical boolean.

---

## 10. Language handling

- Stage 1 detects language and writes it into `CleanedTicket.detected_language`. The cleaned complaint text is **not translated** — it stays in the customer's language so Stage 3 sees the original meaning.
- Stage 3 drafts `customer_reply` in the same language as the input (en → en, bn → bn, mixed → en).
- Stage 4 verifies language match and, if the LLM drifted (e.g. replied in English to a Bangla complaint), rewrites `customer_reply` from the bilingual safe templates in `app/config.py`.
- Bilingual safe templates ship for both `en` and `bn` for every `case_type`. Templates live in `app/config.py`.

---

## 11. Sample-case contract

All 10 sample cases in `docs/SUST_Preli_Sample_Cases.json` must produce a response that is **functionally equivalent** to the expected output:

- same `relevant_transaction_id` (or `null` where expected),
- same `evidence_verdict`,
- same `case_type`,
- same `department`,
- comparable `severity`,
- safe `customer_reply`,
- `human_review_required` matching the precedence rule.

`tests/tests.http` contains all 10 cases as executable requests plus:

- `GET /health` (smoke test),
- malformed JSON body (expect 400),
- empty `complaint` (expect 422).

---

## 12. Standing rules for every turn

These apply to every Claude/agent invocation in this repo. No exceptions.

1. **Touch ONLY `normalizer/` and the root `docker-compose.yaml`.** Never edit `backend/`, `deploy/`, `docs/`, root `README.md`, root `CLAUDE.md`, `.github/`, or `docker-compose.prod.yml`.
2. **Safety lives in `app/safety.py` as code**, never in prompts. The LLM may be reminded of safety in system prompts, but the authoritative enforcement is deterministic.
3. **Every code path returns schema-valid JSON.** The endpoint never returns 5xx for a parseable request body. 4xx is reserved for malformed JSON (400) and empty `complaint` (422).
4. **No GPU. No torch. No model weights. No dependency larger than 100 MB.** All four stages use the OpenRouter-hosted model in `OPENROUTER_MODEL`.
5. **Show the diff and wait before applying any change.** No silent edits. Every file write is preceded by a proposed diff in the chat.
6. **Git is allowed, but normalizer-only and per-command.** Rule #6 (v1: "never run git") is **lifted** as of locked settings v3 with the following boundaries — see rule #11 for the full policy. Read/write operations, branch operations, and remote operations are permitted only against files and refs inside `normalizer/`. The user must explicitly name the command and target on every invocation; no chained git actions.
7. **Never read or write secrets.** `OPENROUTER_API_KEY` is read from env at runtime. Never logged. Never echoed. Never committed. The current value in `normalizer/.env` stays untouched. Git operations must never stage, diff, or transmit `normalizer/.env`.
8. **Locked settings win.** If a future prompt contradicts this file, surface the contradiction and ask before deviating.
9. **One file at a time.** When implementing, write one file, show the proposed content, wait for approval, then write the next. Same discipline for git: one command at a time, show intended command and target, wait for "go".
10. **Tests first for safety.** `tests/test_safety.py` is written and reviewed BEFORE `app/safety.py` is implemented. `tests/test_investigator.py` is written and reviewed BEFORE `app/investigator.py` is implemented. TDD discipline for the code-only stages.
11. **Git boundary (normalizer-only, locked settings v3).**
    - **In scope**: any `git` command whose effect is confined to `normalizer/` files and refs. Examples: `git status` (read-only), `git diff -- normalizer/`, `git add normalizer/...`, `git checkout -b <branch>` on a normalizer-scoped branch, `git commit` whose staged set is entirely inside `normalizer/` and root `docker-compose.yaml`, `git push` of a branch that contains only normalizer-scoped changes.
    - **Out of scope**: any command that touches `backend/`, `deploy/`, `docs/`, root `README.md`, root `CLAUDE.md`, `.github/`, `docker-compose.prod.yml`, or any file/repo-level config (`.gitignore` at root, `.gitattributes`, hooks, refs, remote configuration). This matches the edit-surface rule in §1.
    - **Per-command confirmation required.** The user must state the exact command (or close paraphrase) and the target (branch name, remote name, file path) before each invocation. I will echo the command back and wait for "go". No aliases, no shortcuts, no chained commands.
    - **No history rewrites.** `git push --force`, `git reset --hard`, `git rebase`, `git filter-branch`, `git reflog expire`, and any operation that rewrites or destroys existing history are **forbidden**. If a force-push is genuinely required, I will refuse and ask the user to run it themselves.
    - **No remote configuration changes.** I will not `git remote add`, `git remote set-url`, modify `.git/config`, or alter credentials, hooks, or signing config.
    - **No config-level changes without explicit override.** I will not run `git config --global`, `git config --local`, or change `commit.gpgsign`, hooks bypass (`--no-verify`), or signing (`--gpg-sign`).
    - **Secrets never staged.** Before any `git add` or `git commit`, I verify the staged set with `git diff --cached --name-only` and refuse if it includes `normalizer/.env` or any file matching secret patterns (API keys, tokens, `.env`, `.env.*`).
    - **One git command per turn.** No `&&`-chained git pipelines, no shell loops over git commands, no background git watchers.
    - **Push requires explicit push target.** "push to other branch" is not sufficient — I need the remote name and the branch name. If the user says "push", I ask which remote and which branch before doing anything.
    - **Pulls require source spec.** "git pull" is not sufficient — I need the remote and the source branch, and I will refuse a pull onto a dirty working tree unless the user explicitly approves a stash or commit-first.
    - **Default branch policy.** I will not push to `main` or `master` unless the user explicitly names one of those branches as the push target. Default assumption: feature branches only.
    - **Reporting.** After every git command, I report the exact command run, its exit status, and a concise summary of the result (files changed, branch state, push output). No silent git actions.

---

## 13. Open items requiring user decision before implementation

These remain unresolved from the conversation and block implementation until answered:

- [ ] `claimed_case_type` semantics: customer-stated intent as free-form string, normalized enum, or both fields?
- [ ] `matched_transaction: dict | None` vs typed `Transaction | None` (typed is recommended and matches the request schema).
- [ ] `tests/tests.http` scope: 10 sample cases + health + malformed, or only sample cases?
- [ ] `normalizer/README.md` scope: normalizer-service-only docs vs cross-service.
- [ ] `requirements.txt`: confirm final dep list is `fastapi`, `uvicorn`, `pydantic`, `httpx`, `tenacity`, `pytest`.
- [ ] `temperature` per stage: keep `0.2` everywhere, or lower for Stage 1 cleaning?

These do not block `CLAUDE.md` itself. They block the next implementation turn.

---

## 14. Confirmation log

- **Locked settings v1** — established in the prior turn (port 9000, `.env.example` as sole env source, all stages as defined above, 4-stage pipeline, human-review precedence as written, 6s/4 calls, 24s budget, 25s ceiling).
- **Locked settings v2 (this file, 2026-06-26)** — restates and elaborates v1 with full directory tree, schema definitions, and standing rules.
- **Locked settings v3 (2026-06-26, same day)** — lifts standing rule #6 ("never run git") and replaces it with a normalizer-only git policy:
  - Git commands are permitted only against files and refs inside `normalizer/` (and root `docker-compose.yaml` where it's a normalizer-scoped change).
  - Per-command explicit confirmation required: I echo the command and target, wait for "go".
  - No history rewrites (`push --force`, `reset --hard`, `rebase`, `filter-branch`, `reflog expire`).
  - No remote/configuration changes (`remote add`, `remote set-url`, `git config`, hooks bypass, signing bypass).
  - Secrets (`normalizer/.env`, any `*.env*`, API keys, tokens) are never staged. I verify with `git diff --cached --name-only` before any `git add`/`commit`.
  - One git command per turn. No `&&`-chains, no loops, no background watchers.
  - Push requires explicit remote + branch name. Pull requires explicit source remote + branch name.
  - Default: feature branches only. `main`/`master` push requires explicit naming.
  - Any future deviation requires explicit user sign-off recorded here.

Date locked: 2026-06-26.

- **Locked settings v4 (2026-06-26, same day)** — implementation-phase alignment:
  - **LLM transport**: All four stages use the canonical pattern shown below (OpenAI SDK, `OPENROUTER_*` env, `temperature=0.1`, `response_format={"type":"json_object"}`, `timeout=10s`).
  - **File layout**: Stage modules are `app/stage1_cleaner.py`, `app/stage2_investigator.py`, `app/stage3_reasoner.py`, `app/stage4_safety.py`. LLM transport is `app/llm/openrouter.py` (replaces the previous template `normalizer/llm/`).
  - **Port**: Confirmed normalizer binds **9000**. The "default 8000" text in the implementation brief is stale; `app/config.py::PORT=9000` and the Dockerfile `EXPOSE 9000` are authoritative.
  - **Timeout**: Per-call LLM timeout is **10 s**. Each stage has its own deterministic fallback that fires on timeout/error, so the pipeline always returns. The 25 s ceiling from v1 is a soft guideline, not a hard deadline.
  - **Cross-field signals**: Stages 1, 2, and 3 study cross-field evidence — `amount_in_text_vs_history_discrepancy`, `time_hard_mismatch`, and `status_contradiction` are computed in `app/stage2_investigator.py` and surfaced via `Stage2Output.flags`. Stage 3's prompt is told to lower confidence by 0.1–0.2 when any cross-field flag is set.
  - **Schema rename**: Public response is `TicketResponse` (was `NormalizedResponse` in v2). Internal audit fields `safety_violations_found` and `safety_overrides_applied` live on `Stage4Output` (internal) and are stripped before final emission.
  - **Tests**: `tests/test_safety.py` and `tests/test_investigator.py` cover deterministic code paths (signal helpers, scan/replace, override precedence). LLM behavior is validated by the 10 sample cases in `tests/tests.http`.
  - **Template files**: The previous template at `normalizer/main.py`, `normalizer/config.py`, and `normalizer/llm/` is superseded by `normalizer/app/`. Old template files may remain on disk but are no longer imported by the running service (the Dockerfile CMD points at `normalizer.app.main:app`).

## LLM call pattern — ALL stages use this exact pattern

from openai import OpenAI
import os

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
)

response = client.chat.completions.create(
    model=os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"),
    messages=[...],
    response_format={"type": "json_object"},
    temperature=0.1,
    max_tokens=<stage-appropriate>,
    timeout=10
)
