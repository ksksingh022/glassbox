"""P9 — History (per-session attempt state).

Owns the growing list of attempts for one run and turns it into the message
tail the next subagent call sees — code + structured failure per attempt —
then hands the whole thing to `ContextManager` so a long retry session
still fits a fixed token budget. `RunContext.attempts` (Phase 1) is the raw
storage; `SessionHistory` is the read side subagents actually talk to.
"""
from __future__ import annotations

from harness.context.context_manager import ContextManager
from harness.context.failure_formatter import FailureFormatter
from harness.models import AttemptRecord, Message


class SessionHistory:
    def __init__(self, context_manager: ContextManager | None = None, formatter: FailureFormatter | None = None):
        self._attempts: list[AttemptRecord] = []
        self._context_manager = context_manager or ContextManager()
        self._formatter = formatter or FailureFormatter()

    def record(self, attempt: AttemptRecord) -> None:
        self._attempts.append(attempt)

    @property
    def attempts(self) -> list[AttemptRecord]:
        return list(self._attempts)

    def for_prompt(self, head: list[Message], budget_tokens: int = 4000) -> list[Message]:
        """`head` is the system + problem-statement messages that always
        stay verbatim. Returns head + one (code, failure) message pair per
        attempt so far, trimmed to fit `budget_tokens`."""
        tail: list[Message] = []
        for attempt in self._attempts:
            tail.append(Message(role="assistant", content=f"```python\n{attempt.code}```"))
            tail.append(Message(role="user", content=self._formatter.format(attempt)))
        return self._context_manager.fit(head + tail, budget_tokens)
