FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_TIMEOUT=60 \
    PIP_RETRIES=10

WORKDIR /app

# curl for the healthcheck. psycopg[binary] bundles libpq so no libpq-dev.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so source changes don't bust the layer cache.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# App source (manage.py, cortex/, tickets/)
COPY . ./

EXPOSE 8000

# collectstatic + migrate on every start (idempotent), then serve with gunicorn.
# collectstatic writes to STATIC_ROOT (/app/staticfiles), bind-mounted to
# ./backend/staticfiles on the host so nginx serves the assets directly.
# workers=1 keeps the request flow simple; the normalizer round-trip is the
# slow part, not CPU. timeout=60 covers the 30s SLA + normalizer retries.
CMD ["sh", "-c", "python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn cortex.wsgi:application -b 0.0.0.0:8000 --workers 1 --timeout 60"]