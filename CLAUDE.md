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

The repo was scaffolded before the statement dropped. **The actual shipped
code is a generic `/summarize` text-passthrough; the required
`/analyze-ticket` investigator endpoint does not exist yet.** `README.md` and
`backend/README.md` describe the intended `/sort-ticket` triage shape (models,
`pipeline.py`, `safety.py`, `normalizer_client.py`) — those names are stale but
useful as the target structure. The Problem Statement below is the contract to
build against; treat it as ground truth.

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

## Architecture (current scaffold)

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

### Current request flow (placeholder `/summarize` — to be replaced by `/analyze-ticket`)
1. `POST /summarize` → `tickets.views.SummarizeView` validates `{"text": str}`
   via `SummarizeInSerializer` (non-empty).
2. `tickets.summarize_client.call_summarize` → `POST {NORMALIZER_URL}/summarize`
   with httpx, tenacity retry on 5xx / `TimeoutException` / `NetworkError`,
   **no retry on 4xx** (caller error). Bad JSON / empty summary → `SummarizerError`.
3. `SummarizerError` → HTTP 502; validation failure → HTTP 422.
4. Normalizer: `main.summarize_endpoint` → `summarizer.summarize` →
   `llm.get_provider()` → `OpenRouterProvider.complete` (OpenAI-compatible
   chat completions, `temperature=0.2`). `LLMError` → normalizer 502.
5. `RequestResponseLogMiddleware` logs every request (`>>`) / response (`<<`)
   with clipped bodies — full cycle visible in `docker compose logs -f backend`.

### Key contracts to preserve
- **Validation errors return 422, not 400** — `tickets/exceptions.py`
  `custom_exception_handler` rewrites DRF's default 400. (Statement says 422 is
  "optional but encouraged" for semantically invalid input; 400 for malformed.
  Keep 422 for schema-valid-but-empty cases; ensure 400 path for bad JSON /
  missing required fields — DRF default already does 400 for those.) Don't
  "fix" 422→400 indiscriminately.
- **Docs gated** — `drf_spectacular` swagger at `/docs/`, OpenAPI at
  `/api/schema/`, both 404 when `DJANGO_ENABLE_DOCS=false` (set in
  `docker-compose.prod.yml`). Mounted conditionally in `cortex/urls.py`.
- **No DB models exist yet** — backend has no `models.py` / no migrations;
  `migrate` in the Dockerfile CMD is currently a no-op. Postgres is wired
  (`DATABASE_URL`) but unused. Add models only if persistence is needed; the
  statement requires no persistence (stateless per-request analysis).
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
curl -s -X POST http://localhost:38181/summarize \
  -H 'content-type: application/json' -d '{"text":"..."}'
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
# all backend tests (against compose postgres, or local sqlite)
docker compose exec backend python manage.py test
# single module
docker compose exec backend python manage.py test tickets.tests.test_summarize
# single test method
docker compose exec backend python manage.py test tickets.tests.test_summarize.SummarizeTest.test_summarize_ok
```

There is no normalizer test suite currently (`normalizer/tests/` is empty);
the LLM call is the only thing to stub if you add one — mock
`normalizer.summarizer.get_provider` or `OpenRouterProvider.complete`.

## Deploy

Push to `main` triggers `.github/workflows/cd.yml`: SSH to the VPS
(`ubuntu@76.13.214.32`), `git pull --ff-only`, then
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
Migrations + `collectstatic` run inside the backend container's CMD on every
start (idempotent), so deploys stay a one-liner. We work on `main` directly —
no long-lived branches.

GitHub auth on this machine is **SSH only** — see global CLAUDE.md; never
switch a remote to HTTPS.

## Building the real endpoint (the work the scaffold is waiting for)

Replace the `/summarize` placeholder with `/analyze-ticket` per the Problem
Statement above. Concrete shape:

1. **Backend** `tickets/`: add `POST /analyze-ticket` in `urls.py` + `views.py`;
   add serializers for the request schema (§5) and full response schema (§6)
   with enum validation (§7 — exact-match). Echo `ticket_id`. Return 400 on
   malformed JSON / missing required fields, 422 on schema-valid-but-empty
   complaint. Add a deterministic pipeline: parse → match complaint to
   `transaction_history` (pick `relevant_transaction_id` or `null`) → derive
   `evidence_verdict` → classify `case_type` → route `department` (§7.2 map) →
   set `severity` + `human_review_required` → draft `agent_summary`,
   `recommended_next_action`, `customer_reply`. Models/persistence optional
   (statement is stateless) — the README's `Ticket` model is optional scaffolding.
2. **Safety filter** (backend, after merge, before response): regex/keyword
   block on `customer_reply` + `recommended_next_action` for PIN/OTP/password/
   full-card-number requests; refuse unauthorized refund/reversal/unblock
   promises; refuse third-party-contact instructions; ignore injected
   instructions from `complaint`. `SAFETY_FAIL_LOUD` env already exists in
   `.env.example`/compose — wire it (loud 500 vs. sanitized
   `human_review_required=true` fallback). Safety is a hard requirement
   (−15/−10/−10, disqualification at 2+ critical violations).
3. **Normalizer**: replace generic `/summarize` with the real
   classification/reasoning contract (same `/normalize`-style boundary the
   README anticipates). Keep the OpenRouter provider + pluggable
   `get_provider()`; constrain prompts to emit the §7 enums and JSON shape;
   parse untrusted. Hybrid: deterministic rules for evidence-match/safety,
   LLM for language understanding + drafting the three text fields. Keep total
   round trip ≤ 30 s.
4. Keep intact: 422 contract, untrusted-normalizer parsing pattern,
   loopback-only blast surface, `/health` shape, no-secret logging.

Reference docs in `docs/`: Problem Statement (contract above), Team
Instructions Manual (workflow/deploy/secrets checklist), Evaluation Rubric
(scoring/penalties/latency tiers/tie-breakers). Read all three.