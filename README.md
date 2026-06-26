# CORTEX — SUST CSE Carnival 2026 (Codex Community Hackathon)

**Team CORTEX** — submission for the **bKash presents SUST CSE Carnival 2026:
Codex Community Hackathon**, AI/API Challenge · 4-Hour Online Preliminary.

- Repo: <https://github.com/abrar-nazib/cortex-codex-mock-preli>
- Live API base URL: `https://hackathonapi.cortextechnologies.net`
- Submission form: (see organizer Google Form)


---

## What we build 

A backend API service that:

1. Answers `GET /health` with `{"status":"ok"}` (proves the service is up; must
   respond within 60s of start — rubric "health readiness").
2. Accepts the required input JSON on `POST /analyze-ticket` and returns the
   required structured output JSON **exactly** as defined in the Problem
   Statement 




### Endpoints

| Method | Path                | SLA        | Purpose                                  |
|--------|---------------------|------------|------------------------------------------|
| GET    | `/health`           | < 60 s start, fast | `{"status":"ok"}` health probe    |
| POST   | `/analyze-ticket` | < 30 s      | Analyze one request, return structured JSON |


---

## Architecture

The system is a robust **two-service orchestrated pipeline** designed to ingest customer complaints and transaction histories, cross-reference them for ground-truth evidence, classify the case, and draft a safe, policy-compliant response.

### 1. 🌐 Overall Architecture

The system is split into a robust **Django/DRF Backend** that handles external API traffic, safety guardrails, and deterministic rules, and a **FastAPI Normalizer** that acts as the intelligence engine interfacing with LLMs.

![Overall Architecture](docs/images/architecture-Overall%20Architecture(1).png)

| Service     | Directory     | Public URL (HTTPS)                     | Internal port     | Host port       |
|--------------|---------------|----------------------------------------|-------------------|-----------------|
| `backend`   | `backend/`    | `https://hackathonapi.cortextechnologies.net` | `8000` (gunicorn) | `127.0.0.1:38181` |
| `normalizer`| `normalizer/` | _internal only — not exposed publicly_       | `9000` (uvicorn)  | _none_          |

### 2. 🧩 Individual Service Architecture

#### A. Backend Service (Django / DRF)
The backend acts as the authoritative gatekeeper. It strictly enforces the API contract, orchestrates the analysis pipeline, overrides LLM hallucination deterministically, and applies safety rails.

![Backend Service](docs/images/architecture-Backend%20Service.png)

#### B. Normalizer Service (FastAPI)
The normalizer is the reasoning engine. It executes a 3-stage intelligence pipeline, keeping LLM interactions constrained and isolated.

![Normalizer Service](docs/images/architecture-Normalizer%20Service.png)

### 3. 🚀 Deployment Architecture

The solution uses a clean, reproducible deployment strategy to a VPS (Ubuntu) via GitHub Actions (CI/CD). It emphasizes security by not exposing internal services (Normalizer) to the host or internet.

![Deployment Architecture](docs/images/architecture-Deployment%20Architecture.png)

#### Blast surface (intentionally small)

- **Only the backend is reachable from the internet**, through one nginx vhost.
- Backend host port binds **`127.0.0.1:38181`** (loopback) — only nginx on the VPS reaches it; not exposed on the public interface.
- **normalizer publishes no host ports** — compose DNS only.
- No Django admin, no auth/session/CSRF middleware — stateless JSON API.
- No CORS headers — any client may call the API.

#### How a request flows

1. Caller hits `POST https://hackathonapi.cortextechnologies.net/analyze-ticket`.
2. `backend` validates the request, parses the schema, and extracts data.
3. `backend` calls `POST http://normalizer:9000/analyze` with the full payload, retrying on 5xx/timeout; treats the response as **untrusted**.
4. `backend` merges the result, applies the **safety filter** (never ask for PIN/OTP/password; never promise unauthorized actions), and escalates risky cases to human review.
5. The merged safe record is returned to the caller.

---

## Running it locally

```bash
# db + backend + normalizer
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Health probe
curl -s http://localhost:38181/health
# -> {"status":"ok"}

# Analyze a request (placeholder endpoint until the problem statement names it)
curl -s -X POST http://localhost:38181/sort-ticket \
  -H 'content-type: application/json' \
  -d '{"ticket_id":"T-001","channel":"app","locale":"en",
       "message":"<sample input from the problem statement>"}'
```

Swagger UI (drf_spectacular): <http://localhost:38181/docs/> — gated by
`DJANGO_ENABLE_DOCS`, flipped off in production via `docker-compose.prod.yml`.

### Backend dev (host)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # set DATABASE_URL + NORMALIZER_URL
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

### Tests

Django's built-in test runner (no pytest), normalizer mocked with
`unittest.mock.patch` — network-free:

```bash
docker compose exec backend python manage.py test
docker compose exec backend python manage.py test tickets.tests.test_sort_ticket
```

---

## Docker fallback (manual §8)

The repo ships a buildable image so judges can run it without a public endpoint.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
# or, single-image fallback once the backend Dockerfile is the judged entrypoint:
# docker build -t cortex-hackathon ./backend
# docker run -p 8000:8000 --env-file judging.env cortex-hackathon
```

| Rule (manual §8)            | This repo                                            |
|-----------------------------|------------------------------------------------------|
| Image size < 500 MB (1 GB hard) | `python:3.11-slim` base, no heavy deps           |
| GPU                         | not used                                              |
| Large local model weights   | not used                                              |
| Multi-GB downloads at runtime | not used                                            |
| Runtime training            | not used                                              |
| Port binding                | `0.0.0.0` (container) → `127.0.0.1:38181` (host) via nginx |
| `/health` readiness         | responds within seconds of start                     |
| Secrets                     | env vars only — never baked in; `.env` gitignored     |

---

## AI / model usage (manual 9, rubric Documentation)

- **Approach:** hybrid rules + optional lightweight LLM (allowed; GPU not
  allowed). The task is designed to be solvable without paid APIs, so the
  deterministic path must score on its own; the LLM only augments language
  understanding / structured-reasoning support where it helps.
- **Provider:** Openrouter as the provider. The skeleton's `normalizer/requirements.txt` drops LLM
  deps until then; they get added back behind the same `/normalize` contract.
- **No GPU, no multi-GB weights, no runtime training** (manual 8/9).
- **Keys:** any provider key is supplied via env vars only (`normalizer/.env`,
  gitignored) and, for judging, via the official private submission field —
  never committed to the repo.

## Safety logic (rubric "Safety & Escalation", 20 pts — hard requirement)

Guardrails enforced **server-side** in the backend after merging the normalizer
output, before the response is returned:

1. **Credential requests** — `agent_summary`/any generated text must never ask
   the customer for PIN, OTP, password, or a full card number. A violation
   fails the case (HTTP 500) rather than returning unsafe text.
2. **Unsafe promises** — never promise unauthorized approvals, irreversible
   actions, account changes, or guaranteed outcomes (the system is a support
   copilot, not an authority — manual §14).
3. **Data exposure** — never echo or leak sensitive private information; only
   synthetic data is used (manual §14).
4. **Escalation** — risky, uncertain, or authorization-sensitive cases route
   to human review rather than auto-resolving. Escalation is mandatory when
   the rubric's risk conditions hit (exact triggers TBD from the Problem
   Statement).
5. **Fail-safe** — if the normalizer is down or returns garbage, the backend
   stays answerable via conservative defaults; it never trades safety for
   confidence.

## Known limitations

- **Problem Statement pending** — main-endpoint name, request/response schema,
  enum values, and reasoning logic are placeholders until the statement
  publishes. Anything marked TBD above fills in then.
- **Normalizer is a skeleton** — `/normalize` returns a placeholder; the real
  classifier wires in post-statement.
- **No real customer/production data** — synthetic data only (manual §14).
- **Docs surface** — `/docs/` is up for now and flipped off in production via
  `DJANGO_ENABLE_DOCS=false` in `docker-compose.prod.yml`.

---

## Live deployment

The CD workflow (`.github/workflows/cd.yml`) SSHs into the VPS on every push to
`main` and runs
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
Migrations + `collectstatic` run inside the backend container's CMD on every
start (idempotent), so the deploy is a one-liner. nginx + Let's Encrypt handle
HTTPS termination; the runbook is [`deploy/nginx/cortex-codex.conf`](deploy/nginx/cortex-codex.conf).

Per-request lifecycle logging (every request/response in/out) is emitted by
`backend/tickets/middleware.py` — `docker compose logs -f backend` shows the
full cycle.

---

## Submission (Google Form fields)

| Field                 | Value                                                           |
|-----------------------|-----------------------------------------------------------------|
| Team name             | **CORTEX**                                                      |
| GitHub repository URL | `https://github.com/abrar-nazib/cortex-codex-mock-preli`        |
| Live API base URL     | `https://hackathonapi.cortextechnologies.net`                   |
| Deployment platform   | Self-hosted VPS via GitHub Actions + docker compose            |
| AI/model usage        | Hybrid rules + optional lightweight LLM (no GPU) — TBD post-statement |
| Known limitations     | See "Known limitations" above                                  |
| Safety logic          | See "Safety logic" above                                        |
| No secrets committed  | Confirmed — `.env` gitignored, keys via env vars / private form |
| No real customer data | Confirmed — synthetic data only                                 |

If the live URL is unreachable, the grader falls back to
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
against this repo (manual §6, priority 2/3).

---

## Project layout

```
cortex-codex-mock-preli/
├── README.md                      ← you are here
├── docker-compose.yml              ← base: db + backend + normalizer (dev)
├── docker-compose.prod.yml         ← prod overlay: debug off, docs off
├── .github/workflows/cd.yml        ← push-to-main SSH deploy
├── deploy/nginx/cortex-codex.conf ← single vhost (HTTPS termination)
├── backend/                        ← Django + DRF service (public entrypoint)
│   ├── Dockerfile / requirements.txt
│   ├── manage.py
│   ├── cortex/                     # Django project (settings, urls, wsgi/asgi)
│   └── tickets/                    # models, serializers, views, pipeline,
│                                 # normalizer_client, safety, middleware, tests
├── normalizer/                     ← FastAPI skeleton (generic /health + /normalize)
│   ├── main.py / requirements.txt / Dockerfile
└── docs/
    ├── SUST_Preli_Team_Instructions_Manual_Sanitized.pdf
    └── SUST_Preli_Evaluation_Rubric_Sanitized.pdf
```

## Team & ownership

| Directory      | Owner          | Stack                                | Members |
|----------------|----------------|--------------------------------------|---------|
| `backend/`     | CORTEX backend | Python · Django · DRF · PostgreSQL   | TBD     |
| `normalizer/`  | CORTEX normalizer | Python · FastAPI · Pydantic       | TBD     |

Team name: **CORTEX**. Member names/roles to be added before submission.

We work on `main` directly — single repo, shared history, no long-lived
branches.
