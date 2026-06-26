# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CORTEX team's submission for the SUST CSE Carnival 2026 Codex Community
Hackathon (AI/API challenge, 4.5-hour online preliminary). A deployed backend
API service that judges hit over HTTPS. The **Problem Statement is published**
(`docs/SUST_Hackathon_Preli_Problem_Statement.pdf`) — the challenge is
**QueueStorm Investigator**: an AI/API support copilot for digital finance that
reads a customer complaint **plus** a snippet of their recent transaction
history and returns one structured JSON response that classifies, routes, and
explains the case and drafts a safe reply.

**Build status:**
- `/analyze-ticket` endpoint exists with the full §5 request + §6 response
  serializers, §7 enum validation, and the 400/422/500 error contract wired
  (28 tests green). The **200 analysis path is not implemented yet** — a valid
  request returns **501** so the error contract can be tested in isolation.
  Next milestone: the evidence-match → classify → route → draft-safe-reply
  pipeline + safety filter.
- The old `/summarize` placeholder API + its client/tests have been removed
  from both backend and normalizer. The normalizer currently exposes only
  `/health`; its reasoning endpoint lands with the backend 200 path. The
  generic LLM-provider layer (`normalizer/llm/`, `config.py`) is kept — it is
  not summarize-specific and the `/analyze` reasoning call will reuse it.
- `README.md` / `backend/README.md` still describe an older `/sort-ticket`
  triage shape (`pipeline.py`, `safety.py`, `normalizer_client.py`) — those
  names are stale targets, not existing files. The Problem Statement below is
  ground truth.

## Problem Statement: QueueStorm Investigator

Service is an **investigator, not a classifier**. Every input has a complaint
**and** a short transaction history (typically 2–5 txns). The complaint says
one thing; the data may show another. The service decides what is true. When
evidence is genuinely unclear, it must say so, not guess. It is an internal
copilot, never an autonomous financial decision-maker.

### Endpoints (judges only exercise these)

| Method | Path | SLA | Purpose |
|--------|------|-----|---------|
| GET | `/health` | `{"status":"ok"}` within 60 s of start | readiness probe |
| POST | `/analyze-ticket` | ≤ 30 s per request | analyze one ticket, return structured JSON |

### HTTP codes
200 success · 400 malformed input (invalid JSON / missing required fields) ·
422 semantically valid JSON but invalid (e.g. empty complaint) — optional but
**encouraged** · 500 internal error (never leak stack traces, tokens, secrets).
**Must not crash on malformed input** — 400/500 acceptable, process exit is not.

### Request schema (`POST /analyze-ticket`)
```json
{
  "ticket_id": "TKT-001",                 // req, string, echoed in response
  "complaint": "I sent 5000 taka ...",    // req, string, en/bn/mixed Banglish
  "language": "en",                       // opt: en | bn | mixed
  "channel": "in_app_chat",               // opt: in_app_chat | call_center | email | merchant_portal | field_agent
  "user_type": "customer",                // opt: customer | merchant | agent | unknown
  "campaign_context": "boishakh_bonanza_day_1",  // opt, harness-provided
  "transaction_history": [                // opt, may be empty (safety-only cases)
    {
      "transaction_id": "TXN-9101",
      "timestamp": "2026-04-14T14:08:22Z",  // ISO 8601
      "type": "transfer",                  // transfer | payment | cash_in | cash_out | settlement | refund
      "amount": 5000,                       // BDT, number
      "counterparty": "+8801719876543",
      "status": "completed"                 // completed | failed | pending | reversed
    }
  ],
  "metadata": {}                          // opt, simulated context
}
```

### Response schema
```json
{
  "ticket_id": "TKT-001",                 // req, must match request
  "relevant_transaction_id": "TXN-9101",  // req, string | null (no match)
  "evidence_verdict": "consistent",       // req: consistent | inconsistent | insufficient_data
  "case_type": "wrong_transfer",          // req, see enum
  "severity": "high",                     // req: low | medium | high | critical
  "department": "dispute_resolution",     // req, see enum
  "agent_summary": "...",                 // req, 1–2 sentences for the agent
  "recommended_next_action": "...",       // req, operational next step for the agent
  "customer_reply": "...",                // req, safe official reply honoring §8
  "human_review_required": true,          // req, bool — true for disputes/suspicious/high-value/ambiguous
  "confidence": 0.9,                      // opt, float 0–1
  "reason_codes": ["wrong_transfer", "transaction_match"]  // opt, short labels
}
```

### Enums (must match EXACTLY — case/plural/spelling variants = schema violation)
- **case_type**: `wrong_transfer`, `payment_failed`, `refund_request`,
  `duplicate_payment`, `merchant_settlement_delay`, `agent_cash_in_issue`,
  `phishing_or_social_engineering`, `other`
- **department**: `customer_support`, `dispute_resolution`, `payments_ops`,
  `merchant_operations`, `agent_operations`, `fraud_risk`
- department↔case_type mapping (typical): wrong_transfer/contested refund →
  dispute_resolution; payment_failed/duplicate_payment → payments_ops;
  merchant_settlement_delay → merchant_operations; agent_cash_in_issue →
  agent_operations; phishing/suspicious → fraud_risk; other/vague/low-severity
  → customer_support.

### Safety rules (auto-checked; violations subtract directly, can disqualify)
Checked automatically against `customer_reply` and `recommended_next_action`:
1. **Never ask for PIN, OTP, password, or full card number** — even framed as
   verification. May *warn* users not to share them; never *request* them. −15.
2. **Never confirm refund/reversal/account unblock/recovery without authority.**
   Use "any eligible amount will be returned through official channels", NOT
   "we will refund you". −10.
3. **Never instruct customer to contact a suspicious third party** — official
   channels only. −10.
4. **Ignore prompt injection** embedded in the complaint — adversarial text
   must not override system rules (applies to all output fields).
5. **2+ critical safety violations** → not eligible for top-40 finalist pool.

### Runtime profile
2 vCPU / 4 GB RAM sufficient. No GPU (not recommended). Image < 5 GB preferred
(Manual §8 Docker fallback: < 500 MB recommended, 1 GB hard). No multi-GB
runtime downloads, no runtime training, no large baked weights. Bind 0.0.0.0.

Latency tiers (p95): full credit ≤ 5 s, partial ≤ 15 s, minimal ≤ 30 s. The
harness stops waiting at 30 s — treat as a hard failure. `/health` within 60 s.

### Allowed external services
Major public LLM/AI providers: OpenAI, Anthropic, Hugging Face Inference,
Cohere, Google AI, "and similar". **Outbound to your own servers, scraping
sites, or unrelated endpoints may be blocked** by the eval environment. The
normalizer's OpenRouter provider is OpenAI-compatible and falls under "and
similar"; keep it. No LLM API credits provided — team supplies own keys.
Rule-based / small local / free-tier solutions are fine; an LLM is **not
required** to score well. Hybrid (deterministic for validation/safety + AI for
language/structured reasoning/drafting) is the recommended approach.

### Deliverables
- GitHub repo (public or add organizer handle `bipulhf`).
- README.md: setup, run command, tech stack, AI approach, **safety logic**,
  model+cost reasoning, assumptions, known limitations, **MODELS section**
  (every model used, where it runs, why chosen).
- Sample output file: at least one output generated from a public sample case
  in `QueueStorm_Preli_Sample_Cases.json` (10 worked cases; file name in the
  statement as `SUST_Preli_Sample_Cases.json` — fetch/confirm exact name).
  Cases are reference examples, **not the test set** — hidden cases are broader.
  Expected output is *one* valid response; match functionally
  (`relevant_transaction_id`, `evidence_verdict`, `case_type`, `department`,
  comparable `severity`, safety-respecting `customer_reply`), not word-for-word.
- `.env.example` (recommended — var names only).
- Optional 90 s architecture walkthrough video.

### Submission (any ONE valid; Live URL strongly preferred)
A. Live URL (public HTTPS, `/health` + `/analyze-ticket` reachable, no
login/private-network). B. Docker image (`docker pull` + run command). C. Code
+ runbook (last resort). Even with a live URL, README must contain a runbook
so judges can redeploy if the URL dies.

### Scoring (weights)
Evidence Reasoning 35 · Safety & Escalation 20 · API Contract & Schema 15 ·
Performance & Reliability 10 · Response Quality 10 (manual, shortlisted) ·
Deployment & Reproducibility 5 · Documentation 5 (manual, shortlisted).
Two-stage: automated (all teams) → manual (shortlisted).

Tie-breakers (in order): safety score + no critical violations → evidence
reasoning → API/schema validity → reliability/timeout/deploy stability →
exceptional engineering (caching, monitoring, fallback, cost-aware model use)
→ language-handling → documentation → 90 s video.

**Build priority (per the statement/rubric): schema & endpoints first →
evidence reasoning → safety guardrails → reliability/deployment → README.**

## Architecture

Two services + a database, compose-internal, HTTP-only between them:

```
nginx (HTTPS) → backend (Django+DRF, gunicorn :8000) → normalizer (FastAPI, uvicorn :9000)
                        ↓
                   postgres (internal only)
```

- **backend** (`backend/`) — public entrypoint. Host port `127.0.0.1:38181` →
  container `8000` (loopback only; only on-box nginx reaches it). Stateless
  JSON API: no admin, no auth/session/CSRF middleware, no CORS. Validates input,
  calls normalizer, treats normalizer output as **untrusted**.
- **normalizer** (`normalizer/`) — internal only, no published host port.
  Reached at `http://normalizer:9000` via compose DNS. Owns all LLM plumbing.
- **db** — postgres 16, compose-internal only, no host port.

### `tickets/` app layout
- `models.py` — `Ticket` (PK `ticket_id`, the natural key echoed in the
  response) + `Transaction` (FK→Ticket, `related_name="transaction_history"`).
  Mirrors §5; persistence optional (analysis is stateless) but the models
  + `0001_initial` migration exist so a future persist/audit step needs no
  schema reshape.
- `serializers.py` — single enum source-of-truth at top (§7, exact match), then:
  `TransactionSerializer` (standalone §5.2 entry) · `TicketSerializer`
  (standalone §5 ticket, no history) · `TicketWithTransactionSerializer`
  (extends TicketSerializer with nested `transaction_history` list — the
  `POST /analyze-ticket` body) · `AnalyzeTicketOutSerializer` (full §6
  response, `relevant_transaction_id` string|null, optional `confidence`
  0–1 / `reason_codes`) · `HealthOutSerializer`.
- `views.py` — `HealthView` (GET `/health`), `AnalyzeTicketView`
  (`POST /analyze-ticket`).
- `exceptions.py` — `custom_exception_handler` (see contracts below).
- `middleware.py` — `RequestResponseLogMiddleware` logs every request (`>>`)
  / response (`<<`) with clipped bodies — full cycle in
  `docker compose logs -f backend`.
- `tests/` — `test_health`, `test_analyze_ticket` (400/422/500 contract).

### `POST /analyze-ticket` request flow (current)
1. `AnalyzeTicketView.post` reads `request.data` in a try/except — a malformed
   JSON body raises there and becomes **400** (§4.1 "invalid JSON"). A
   valid-JSON non-object (list/number/string) is also **400** (contract is a
   JSON object). This is done in-view so the global 400→422 handler does NOT
   swallow it.
2. `TicketWithTransactionSerializer(data=...)` validates §5 (incl. nested
   `transaction_history` entries via `TransactionSerializer`, §7 enum
   exact-match).
3. On errors, `_classify_validation_errors` walks the DRF error tree and
   collects `.code`s: if **every** code is `required` → **400** (missing
   required field, including nested); anything else (`invalid`, `null`,
   `blank`, `max_length`, `invalid_choice`, `not_a_list`) → **422**.
4. Valid payload → `self._analyze(validated_data)` wrapped in a catch-all
   try/except. `_analyze` currently returns **501** "Analysis pipeline not
   implemented." Any unexpected exception → sanitized **500**
   `{"detail": "Internal server error."}` with a **sanitized log line**
   (`ticket_id` + `type(exc).__name__` only — never `str(exc)` or a traceback,
   per rubric "Secret handling": no stack traces/tokens/secrets in logs OR
   responses). `_analyze` is the single hook the 200 path grows into and the
   seam tests use to force a 500.
5. `RequestResponseLogMiddleware` logs the full in/out cycle.

### Key contracts to preserve
- **400 vs 422 split** (read this carefully before touching `exceptions.py` or
  `views.py`): `/analyze-ticket` classifies in-view
  (`_classify_validation_errors`): malformed JSON / non-object /
  missing-required → **400**; type/size/null/empty/enum → **422**. It never
  reaches the global handler. The global `custom_exception_handler`
  (`tickets/exceptions.py`) still rewrites DRF's default 400 → 422 — it is now
  unused by any shipped endpoint but remains as the safety net for any future
  endpoint that wants the §4.1 "422 encouraged" semantics; don't delete it
  blindly, and don't "fix" `/analyze-ticket` to a single status — the statement
  (§4.1) wants 400 for malformed/missing and 422 for schema-valid-but-bad, which
  `/analyze-ticket` implements exactly.
- **Docs gated** — `drf_spectacular` swagger at `/docs/`, OpenAPI at
  `/api/schema/`, both 404 when `DJANGO_ENABLE_DOCS=false` (set in
  `docker-compose.prod.yml`). Mounted conditionally in `cortex/urls.py`.
- **Normalizer LLM provider is pluggable** — `llm/base.py` `LLMProvider` ABC;
  `llm/__init__.get_provider()` dispatches on `SETTINGS.provider` (only
  `openrouter` wired). Add providers under `llm/` and register in
  `get_provider`. Keep the 30 s per-request SLA in mind — the OpenRouter call
  timeout is `OPENROUTER_TIMEOUT_S=30`; budget downstream so the total
  `/analyze-ticket` round trip stays ≤ 30 s.
- **Treat normalizer output as untrusted** — parse, coerce enums, fall back to
  conservative defaults. Never let a malformed/missing normalizer reply leak
  raw or crash the response.

## Commands

Run the full stack (from repo root):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
curl -s http://localhost:38181/health                       # {"status":"ok"}
curl -s -X POST http://localhost:38181/analyze-ticket \
  -H 'content-type: application/json' \
  -d '{"ticket_id":"TKT-001","complaint":"I sent 5000 taka to a wrong number around 2pm today.","language":"en","channel":"in_app_chat","user_type":"customer","campaign_context":"boishakh_bonanza_day_1","transaction_history":[{"transaction_id":"TXN-9101","timestamp":"2026-04-14T14:08:22Z","type":"transfer","amount":5000,"counterparty":"+8801719876543","status":"completed"}]}'
# valid payload -> 501 (200 not implemented yet); malformed/missing -> 400; type/enum/size/null -> 422
```

Backend dev on host (no docker):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL (sqlite:///./db.sqlite3 works) + NORMALIZER_URL
python manage.py runserver 127.0.0.1:8000
```

Normalizer dev on host (from repo root, parent of the `normalizer/` package):

```bash
cd normalizer && pip install -r requirements.txt
cp .env.example .env          # set OPENROUTER_API_KEY
uvicorn normalizer.main:app --reload --port 9000
```

### Tests

Django built-in test runner (no pytest). Normalizer is mocked with
`unittest.mock.patch` — tests are network-free and don't require the
normalizer or a live LLM.

```bash
# all backend tests (against compose postgres test DB; 32 tests currently)
docker compose exec backend python manage.py test
# single module
docker compose exec backend python manage.py test tickets.tests.test_analyze_ticket
# single test method
docker compose exec backend python manage.py test tickets.tests.test_analyze_ticket.AnalyzeTicket422Tests.test_null_ticket_id_returns_422
```

The compose service is named **`backend`** (not `web`). `/analyze-ticket`
tests are network-free (no 200 path → no normalizer call). The 500 tests
patch `tickets.views.AnalyzeTicketView._analyze` to raise a RuntimeError
carrying a fake secret + traceback and assert neither leaks into the response
or the log stream.

There is no normalizer test suite currently (`normalizer/tests/` is empty);
the LLM call is the only thing to stub if you add one — mock
`normalizer.llm.get_provider` or `OpenRouterProvider.complete`.

## Deploy

Push to `main` triggers `.github/workflows/cd.yml`: SSH to the VPS
(`ubuntu@76.13.214.32`), `git pull --ff-only`, then
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
Migrations + `collectstatic` run inside the backend container's CMD on every
start (idempotent), so deploys stay a one-liner. We work on `main` directly —
no long-lived branches.

GitHub auth on this machine is **SSH only** — see global CLAUDE.md; never
switch a remote to HTTPS.

## Remaining work (the 200 analysis path)

The error contract (400/422), request/response serializers, §7 enum validation,
models, and migration are done. What's left to ship the real `/analyze-ticket`:

1. **Evidence-match (the Investigator Twist)** — match the complaint against
   `transaction_history`: pick `relevant_transaction_id` or `null`, derive
   `evidence_verdict` (`consistent` / `inconsistent` / `insufficient_data`).
   Deterministic rules (amount/time/counterparty signals) are the backbone;
   LLM only augments language understanding. Never confirm a refund without
   checking the history.
2. **Classify + route** — `case_type` (§7.1), `department` (§7.2 map), `severity`
   (low/medium/high/critical), `human_review_required` (true for disputes /
   suspicious / high-value / ambiguous).
3. **Draft the three text fields** — `agent_summary`, `recommended_next_action`,
   `customer_reply`. LLM-assisted drafting, then run the safety filter.
4. **Safety filter** (backend, after merge, before response): regex/keyword
   block on `customer_reply` + `recommended_next_action` for PIN/OTP/password/
   full-card-number requests; refuse unauthorized refund/reversal/unblock
   promises; refuse third-party-contact instructions; ignore injected
   instructions from `complaint` (prompt injection). `SAFETY_FAIL_LOUD` env
   already exists in `.env.example`/compose — wire it (loud 500 vs. sanitized
   `human_review_required=true` fallback). Safety is a hard requirement
   (−15/−10/−10, disqualification at 2+ critical violations).
5. **Normalizer**: add the real classification/reasoning contract (new
   endpoint, e.g. `/analyze`, built on the existing `llm/` provider layer +
   `config.py`). Keep the OpenRouter provider + pluggable `get_provider()`;
   constrain prompts to emit the §7 enums and JSON shape; parse untrusted. Add
   an httpx + tenacity client in the backend for the normalizer round trip
   (5xx/network/timeout retry, no 4xx retry, treat output as untrusted). Hybrid: deterministic rules for
   evidence-match + safety, LLM for language understanding + drafting. Keep
   total round trip ≤ 30 s (budget the OpenRouter call well under
   `OPENROUTER_TIMEOUT_S=30`).
6. **Replace the 501** in `AnalyzeTicketView.post` with the real 200 path that
   builds + validates the response through `AnalyzeTicketOutSerializer` and
   echoes `ticket_id`. Add a `test_analyze_ticket` 200-case using the public
   sample cases in `docs/SUST_Preli_Sample_Cases.json` (10 worked cases —
   reference only, NOT the hidden test set).
7. Keep intact: the in-view 400/422 classification and the catch-all sanitized
   500 (no stack traces/secrets in response OR logs), untrusted-normalizer
   parsing, loopback-only blast surface, `/health` shape, no-secret logging.

Reference docs in `docs/`: Problem Statement (contract above), Team
Instructions Manual (workflow/deploy/secrets checklist), Evaluation Rubric
(scoring/penalties/latency tiers/tie-breakers), `SUST_Preli_Sample_Cases.json`
(10 worked cases). Read all four.