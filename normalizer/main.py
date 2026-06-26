"""FastAPI app — the normalizer service endpoints.

  GET   /health      -> {"status":"ok"}
  POST  /analyze     -> run the reasoning pipeline (normalize -> evidence
                        -> classify+draft) and return a structured result the
                        backend turns into the §6 response.
  POST  /rephrase    -> rephrase customer-facing text that tripped the backend
                        deterministic safety rail.

All LLM plumbing lives in llm/ (provider interface + OpenRouter impl); the
pipeline in pipeline.py; schemas in schemas.py.

Run from the project root (parent of this package):
    uvicorn normalizer.main:app --reload --port 9000
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import SETTINGS
from .pipeline import rephrase, run_analyze
from .schemas import AnalyzeRequest, AnalyzeResult, RephraseRequest, RephraseResult

logging.basicConfig(
    level=SETTINGS.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("normalizer.api")

app = FastAPI(title="Cortex Normalizer", version="0.4.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResult)
def analyze(req: AnalyzeRequest) -> AnalyzeResult:
    return run_analyze(req)


@app.post("/rephrase", response_model=RephraseResult)
def do_rephrase(req: RephraseRequest) -> RephraseResult:
    return rephrase(req)