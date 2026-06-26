"""FastAPI app — the normalizer service.

  GET  /health                  → {"status":"ok"}
  POST /analyze-ticket          → 200 TicketResponse | 400 | 422 | 500

Binds 0.0.0.0:PORT (PORT defaults to 9000 per locked settings).
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import config
from .pipeline import run_pipeline
from .schema import TicketRequest

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("normalizer.api")

app = FastAPI(title="Cortex Normalizer", version="1.0.0")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /analyze-ticket
# ---------------------------------------------------------------------------

@app.post("/analyze-ticket")
async def analyze_ticket(request: Request):
    # 400 — invalid JSON
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid JSON body"})

    if not isinstance(raw, dict):
        return JSONResponse(status_code=400, content={"error": "request body must be a JSON object"})

    # 400 — missing required fields
    if "ticket_id" not in raw or "complaint" not in raw:
        return JSONResponse(
            status_code=400,
            content={"error": "missing required field: ticket_id or complaint"},
        )

    # 422 — empty complaint
    complaint = raw.get("complaint")
    if not isinstance(complaint, str) or not complaint.strip():
        return JSONResponse(status_code=422, content={"error": "complaint must be a non-empty string"})

    # Validate via Pydantic (extra="ignore" so unknown fields don't 400)
    try:
        ticket = TicketRequest.model_validate(raw)
    except Exception as exc:
        # Defensive — the manual checks above should have caught this.
        return JSONResponse(status_code=400, content={"error": "invalid request body"})

    try:
        response = await run_pipeline(ticket)
        return JSONResponse(status_code=200, content=response.model_dump())
    except Exception:
        # Last-resort guard. Pipeline.run_pipeline is total; this should not trigger.
        log.exception("pipeline raised unexpectedly")
        return JSONResponse(status_code=500, content={"error": "internal error"})


# ---------------------------------------------------------------------------
# Validation-error formatter (defensive — FastAPI default would leak details)
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": "validation failed"})


# ---------------------------------------------------------------------------
# Entrypoint for `uvicorn normalizer.app.main:app`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("normalizer.app.main:app", host="0.0.0.0", port=config.PORT)