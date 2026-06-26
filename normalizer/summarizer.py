"""Summarization orchestration.

Builds the chat messages and delegates to the configured LLM provider.
The FastAPI layer in `main.py` calls `summarize(text)`; everything upstream
(HTTP, parsing, provider selection) lives here + in `llm/` + `config.py`.
"""
from __future__ import annotations

from .llm import LLMError, get_provider

SYSTEM_PROMPT = (
    "You are a concise summarizer. Summarize the user's text in a few clear "
    "sentences. Output only the summary, with no preamble or headings."
)


def summarize(text: str) -> str:
    """Summarize `text` and return the cleaned summary string.

    Raises LLMError if the provider call fails or returns nothing usable.
    """
    provider = get_provider()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    content = provider.complete(messages)
    summary = (content or "").strip()
    if not summary:
        raise LLMError("provider returned empty summary")
    return summary