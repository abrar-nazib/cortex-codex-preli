"""LLM provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMError(Exception):
    """Raised when the upstream LLM call fails or returns an unusable reply."""


class LLMProvider(ABC):
    """A chat-completions style provider.

    `complete` returns free text. `complete_json` asks the model for a JSON
    object (via the provider's JSON mode when available) and returns the parsed
    dict; both are total-or-raise.
    """

    @abstractmethod
    def complete(self, messages: list[dict[str, Any]]) -> str:
        """Given a chat message list, return the model's text content."""
        raise NotImplementedError

    @abstractmethod
    def complete_json(
        self, messages: list[dict[str, Any]], schema: dict | None = None
    ) -> dict:
        """Return a parsed JSON object. `schema` is an informal description
        passed to the model in the prompt; enforcement is best-effort here and
        strict upstream (Pydantic) at the call site."""
        raise NotImplementedError