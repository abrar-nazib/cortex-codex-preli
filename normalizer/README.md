# Normalizer Service

Internal AI service for the QueueStorm ticket triage pipeline. Receives
`POST /analyze-ticket` from the backend, runs a 4-stage LLM pipeline, and
returns a structured `TicketResponse`.

The backend is a passthrough; the judge never calls this service directly.

## Endpoints

- `GET  /health` → `{"status":"ok"}`
- `POST /analyze-ticket` → `200 TicketResponse` | `400` | `422` | `500`

## Pipeline

```
HTTP POST /analyze-ticket
  → Stage 1: cleaner        LLM  → CleanedTicket (translate, extract, neutralize injection)
  → Stage 2: investigator   LLM  → EvidenceResult (code-computed signals + LLM verdict)
  → Stage 3: reasoner       LLM  → Stage3Output (classify + draft)
  → Stage 4: safety         code → Stage4Output (mandatory enforcement + optional LLM improve)
  → 200 TicketResponse
```

Every stage has a deterministic fallback. The pipeline never raises to
the caller — even on a quadruple LLM failure, the endpoint returns a
valid `TicketResponse`.

## Env

`.env.example` (4 keys, the only ones):

```
NORMALIZER_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=google/gemini-2.5-flash-lite
```

## Run locally

```bash
cd normalizer
pip install -r requirements.txt
uvicorn normalizer.app.main:app --reload --port 9000
```

## Run in Docker

```bash
docker compose up --build normalizer
```

## Tests

```bash
cd normalizer
pytest -q
```

## Manual REST client

`tests/tests.http` contains all 10 sample cases, plus adversarial /
malformed-body requests, ready for JetBrains / VSCode REST client.

## Layout

```
normalizer/
├── app/
│   ├── main.py              FastAPI routes
│   ├── pipeline.py          orchestrator
│   ├── stage1_cleaner.py    Stage 1
│   ├── stage2_investigator.py  Stage 2
│   ├── stage3_reasoner.py   Stage 3
│   ├── stage4_safety.py     Stage 4
│   ├── config.py            constants, enums, safe-phrase templates
│   ├── schema.py            Pydantic models
│   └── llm/                 OpenAI-SDK transport for OpenRouter
└── tests/
    ├── test_safety.py
    ├── test_investigator.py
    └── tests.http
```

See `CLAUDE.md` for the authoritative architecture and locked settings.