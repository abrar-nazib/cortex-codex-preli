"""LLM providers. `get_provider()` returns the configured one."""
from __future__ import annotations

from ..config import SETTINGS
from .base import LLMError, LLMProvider
from .openrouter import OpenRouterProvider

__all__ = ["LLMProvider", "LLMError", "get_provider"]


def get_provider() -> LLMProvider:
    """Resolve the active LLM provider from SETTINGS.provider."""
    name = SETTINGS.provider.strip().lower()
    if name == "openrouter":
        return OpenRouterProvider()
    raise LLMError(f"unknown provider: {SETTINGS.provider!r}")