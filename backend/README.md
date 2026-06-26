# Backend — Cortex Mock Preliminary

Django + DRF service. Public HTTPS entrypoint. Backed by PostgreSQL (the `db`
service in the root `docker-compose.yml`).

## Run (local dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # set DATABASE_URL + NORMALIZER_URL
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Swagger UI at <http://127.0.0.1:8000/docs/>.

## Run (docker, with normalizer + postgres)

From the repo root:

```bash
docker compose up -d --build          # db + backend + normalizer
```

The backend container runs `migrate` then `gunicorn` in its CMD, so the schema
is applied on every start.

## Endpoints

- `GET /health` — health probe (`{"status":"ok"}`)
- `POST /sort-ticket` — classify one CRM ticket
- `GET /docs/` — drf_spectacular swagger UI
- `GET /api/schema/` — OpenAPI schema

## Tests

Django's built-in test runner (no pytest). Against the compose postgres:

```bash
docker compose exec backend python manage.py test
# single file:
docker compose exec backend python manage.py test tickets.tests.test_sort_ticket
```

Tests stub the normalizer with `unittest.mock.patch` — no network.

## Layout

```
backend/
├── manage.py
├── requirements.txt
├── Dockerfile
├── cortex/                # Django project (settings, urls, wsgi/asgi)
└── tickets/               # the one app
    ├── models.py          # Ticket (PK = ticket_id)
    ├── choices.py          # locked spec enums
    ├── serializers.py     # DRF TicketIn / TicketOut / HealthOut
    ├── views.py           # /health, /sort-ticket
    ├── pipeline.py        # persist → normalize → merge → safety → save
    ├── normalizer_client.py  # httpx + tenacity retry
    ├── safety.py          # PIN/OTP/password/card regex block
    ├── exceptions.py       # 422 for validation errors
    ├── migrations/
    └── tests/             # Django test runner (APITestCase + SimpleTestCase)
```

## Env

See `.env.example`. `NORMALIZER_URL` and `DATABASE_URL` are the ones that
usually need changing.