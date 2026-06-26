"""Environment configuration for the normalizer.

Single place that reads env vars. The rest of the package imports `SETTINGS`.
"""
from __future__ import annotations

import os

from pydantic import BaseModel


class Settings(BaseModel):
    # LLM provider selection. Only "openrouter" is wired for now.
    provider: str = "openrouter"

    # OpenRouter (cloud, OpenAI-compatible).
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-2.5-flash-lite"

    # Per-request HTTP timeout for the upstream LLM call.
    openrouter_timeout_s: float = 30.0

    # Logging.
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            provider=os.getenv("NORMALIZER_PROVIDER", "openrouter"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite"),
            openrouter_timeout_s=float(os.getenv("OPENROUTER_TIMEOUT_S", "30")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )


SETTINGS = Settings.from_env()