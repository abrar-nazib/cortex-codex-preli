"""FastAPI app — the normalizer service endpoints.

  GET  /health   -> {"status":"ok"}

The reasoning/classification contract for POST /analyze-ticket (the
investigator pipeline: evidence-match -> classify -> route -> draft safe reply)
wires in here once the backend's 200 path lands. Until then this is the
health probe only.

All LLM plumbing that the future reasoning endpoint will use lives in:
  config.py     env settings
  llm/          provider interface + OpenRouter impl

Run from the project root (parent of this package):
    uvicorn normalizer.main:app --reload --port 9000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import SETTINGS

logging.basicConfig(
    level=SETTINGS.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("normalizer.api")

app = FastAPI(title="Cortex Normalizer", version="0.3.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}