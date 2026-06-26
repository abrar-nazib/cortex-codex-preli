"""LLM provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMError(Exception):
    """Raised when the upstream LLM call fails or returns an unusable reply."""


class LLMProvider(ABC):
    """A chat-completions style provider. `complete` must be total-or-raise."""

    @abstractmethod
    def complete(self, messages: list[dict[str, Any]]) -> str:
        """Given a chat message list, return the model's text content."""
        raise NotImplementedError