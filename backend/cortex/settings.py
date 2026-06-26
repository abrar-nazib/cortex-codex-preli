"""Django settings for the Cortex mock-preliminary backend.

Env-driven, single-file. No admin, no sessions, no auth views — this is a
pure JSON triage API (POST /sort-ticket, GET /health). The smaller the app
surface, the smaller the blast radius.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
env.read_env(BASE_DIR / ".env")  # no-op if .env absent

# ─── Core ────────────────────────────────────────────────────────────────────
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["hackathonapi.cortextechnologies.net", "localhost", "127.0.0.1"],
)

# ─── Apps ────────────────────────────────────────────────────────────────────
# Intentionally minimal: no admin, no sessions, no messages. contenttypes +
# auth kept because Django expects them and they cost nothing to migrate.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",  # collectstatic + STATIC_URL serving
    "rest_framework",
    "drf_spectacular",
    "tickets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "tickets.middleware.RequestResponseLogMiddleware",
    # No CSRF / session / auth middleware: stateless JSON API, no auth, no cookies.
]

ROOT_URLCONF = "cortex.urls"
WSGI_APPLICATION = "cortex.wsgi.application"
ASGI_APPLICATION = "cortex.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,  # drf_spectacular swagger UI needs the template loader
        "OPTIONS": {"context_processors": []},
    }
]

# ─── Database ────────────────────────────────────────────────────────────────
# Postgres in docker-compose. Tests use the same engine (Django test runner
# creates test_<dbname>). For local-without-docker, set DATABASE_URL=sqlite:///./db.sqlite3.
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://cortex:cortex_pw@db:5432/cortex"),
}

# ─── Normalizer config (read by the future tickets normalizer client) ────────
NORMALIZER_URL = env("NORMALIZER_URL", default="http://normalizer:9000")
NORMALIZER_TIMEOUT_S = env.float("NORMALIZER_TIMEOUT_S", default=20.0)
NORMALIZER_MAX_RETRIES = env.int("NORMALIZER_MAX_RETRIES", default=2)
NORMALIZER_RETRY_BACKOFF_S = env.float("NORMALIZER_RETRY_BACKOFF_S", default=0.5)
LOG_LEVEL = env("LOG_LEVEL", default="INFO").upper()

# Safety: if true, /analyze-ticket returns 500 when customer-facing text remains
# unsafe after rephrase (loud fail). If false, a templated safe reply is returned
# with human_review_required=true (soft fail).
SAFETY_FAIL_LOUD = env.bool("SAFETY_FAIL_LOUD", default=True)

# drf_spectacular swagger UI at /docs/ + OpenAPI schema at /api/schema/.
# Gate so prod can turn the surface off with DJANGO_ENABLE_DOCS=false
# (one-line flip in docker-compose.prod.yml). Defaults on for dev.
ENABLE_DOCS = env.bool("DJANGO_ENABLE_DOCS", default=True)

# ─── DRF + drf_spectacular ───────────────────────────────────────────────────
# JSON only in production (DEBUG=False) — suppresses the Browsable API HTML
# page browsers get when they send `Accept: text/html`, so /health etc. always
# return JSON. Browsable API stays available locally under DEBUG=True.
if DEBUG:
    _DEFAULT_RENDERER_CLASSES = [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ]
else:
    _DEFAULT_RENDERER_CLASSES = ["rest_framework.renderers.JSONRenderer"]

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": _DEFAULT_RENDERER_CLASSES,
    # ValidationError -> 422 (matches the original FastAPI/Pydantic contract).
    "EXCEPTION_HANDLER": "tickets.exceptions.custom_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Cortex Mock Preliminary — Backend",
    "DESCRIPTION": "CRM ticket triage. POST /sort-ticket classifies one ticket at a time.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ─── I18n / static ───────────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
# collectstatic target. Bind-mounted to ./backend/staticfiles on the host so
# nginx can serve the collected assets directly (see deploy/nginx/*.conf).
STATIC_ROOT = "/app/staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─── Logging (lifecycle, mirrors the previous basicConfig) ──────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "level": LOG_LEVEL,
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}