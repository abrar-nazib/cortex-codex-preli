"""LLM provider package.

Exposes the LLMProvider ABC and OpenRouterProvider. All four stages
call `llm_call(messages, max_tokens)` from `app.llm.openrouter`.
"""
from .base import LLMError, LLMProvider
from .openrouter import OpenRouterProvider, llm_call, get_provider

__all__ = ["LLMProvider", "LLMError", "OpenRouterProvider", "llm_call", "get_provider"]
