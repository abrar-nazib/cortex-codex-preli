# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CORTEX team's submission for the SUST CSE Carnival 2026 Codex Community
Hackathon (AI/API challenge, 4-hour online preliminary). A backend API service
that judges hit over HTTPS. Problem Statement was unpublished at scaffold time;
the repo is the deployable skeleton that fills in once it drops.

`README.md` and `backend/README.md` describe the **intended** `/sort-ticket`
triage pipeline (models, `pipeline.py`, `safety.py`, `normalizer_client.py`,
escalation logic). **That code does not exist yet.** The actual shipped code is
a simpler `/summarize` text-passthrough. Treat the source, not the READMEs, as
ground truth; the READMEs are the target shape post-Problem-Statement.

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

### Request flow (actual `/summarize`)

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

### Key contracts

- **Validation errors return 422, not 400** — `tickets/exceptions.py`
  `custom_exception_handler` rewrites DRF's default 400. Existing tests/callers
  depend on this. Don't "fix" it to 400.
- **Docs gated** — `drf_spectacular` swagger at `/docs/`, OpenAPI at
  `/api/schema/`, both 404 when `DJANGO_ENABLE_DOCS=false` (set in
  `docker-compose.prod.yml`). Mounted conditionally in `cortex/urls.py`.
- **No DB models exist yet** — backend has no `models.py` / no migrations;
  `migrate` in the Dockerfile CMD is currently a no-op. Postgres is wired
  (`DATABASE_URL`) but unused. Add models when the Problem Statement requires
  persistence; the README's `Ticket` model is the intended shape.
- **Normalizer LLM provider is pluggable** — `llm/base.py` `LLMProvider` ABC;
  `llm/__init__.get_provider()` dispatches on `SETTINGS.provider` (only
  `openrouter` wired). Add providers under `llm/` and register in `get_provider`.

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

## Constraints (from the hackathon manual)

- Image size < 500 MB (1 GB hard) — `python:3.11-slim` base, no heavy deps.
- **No GPU, no multi-GB weights, no runtime training.** Deterministic path must
  score on its own; LLM only augments. Any provider key via env vars only
  (`normalizer/.env`, gitignored) / the private submission field — never committed.
- `/health` must answer within 60 s of start (it answers in seconds).
- Synthetic data only — no real customer/PII data.
- Safety is a hard requirement (rubric "Safety & Escalation", 20 pts): never
  ask for PIN/OTP/password/full card number, never promise unauthorized or
  irreversible actions. Safety filter slots into the backend after merging
  normalizer output, before response. README "Safety logic" section is the
  spec; implement it when the real reasoning endpoint lands.

## Adding the real endpoint

When the Problem Statement publishes, the placeholder work is: rename/add the
main endpoint in `tickets/urls.py` + `views.py`, add models + migrations under
`tickets/`, build the `pipeline.py` / `safety.py` / `normalizer_client.py`
modules the README describes, and replace the normalizer's generic
`/summarize` with the real classification contract (same `/normalize`-style
boundary the README anticipates). Keep the 422 contract, the untrusted-normalizer
parsing pattern, and the loopback-only blast surface intact.