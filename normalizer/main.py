"""FastAPI app — endpoints only.

  GET  /health     -> {"status":"ok"}
  POST /summarize  -> {"text": "..."} -> {"summary": "..."}

All LLM plumbing lives elsewhere:
  config.py     env settings
  llm/          provider interface + OpenRouter impl
  summarizer.py orchestration (prompt + provider dispatch)

Run from the project root (parent of this package):
    uvicorn normalizer.main:app --reload --port 9000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from .config import SETTINGS
from .llm import LLMError
from .summarizer import summarize

logging.basicConfig(
    level=SETTINGS.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("normalizer.api")

app = FastAPI(title="Cortex Normalizer", version="0.2.0")


class SummarizeRequest(BaseModel):
    """Request envelope. `text` is required; extras are ignored."""

    model_config = ConfigDict(extra="allow")

    text: str


class SummarizeResponse(BaseModel):
    summary: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/summarize", response_model=SummarizeResponse)
def summarize_endpoint(req: SummarizeRequest) -> SummarizeResponse:
    text = req.text.strip()
    if not text:
        log.warning("summarize rejected: empty text")
        raise HTTPException(status_code=422, detail="text must not be empty")

    log.info("POST /summarize len=%d", len(text))
    try:
        summary = summarize(text)
    except LLMError as exc:
        log.warning("summarize FAILED: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    log.info("summarize OK len=%d", len(summary))
    return SummarizeResponse(summary=summary)