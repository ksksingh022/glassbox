"""P1 — Provider seam.

The model is a swappable commodity behind this one interface. Every
concrete provider — OpenRouter, a native Gemini/Anthropic client later, or
the deterministic `FakeProvider` used for demos without an API key — must
satisfy this contract and nothing more.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Completion, Message, ToolSchema


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self, messages: list[Message], tools: list[ToolSchema] | None = None, **kwargs
    ) -> Completion:
        # If `tools` is given, the model may respond with `Completion.tool_calls`
        # instead of (or with empty) `text` — a provider that doesn't support
        # function calling should just ignore `tools` and answer with text.
        ...
