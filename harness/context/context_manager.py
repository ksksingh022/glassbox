"""P11 — Context management (trimming/compaction).

No tokenizer dependency (vanilla-Python ground rule) — `len(text) // 4` is
the standard rough estimate for English/code text and is good enough for a
budget check, not for billing. Strategy: keep the system + problem message
and the most recent attempt's code/failure pair verbatim (the model needs
full detail on what just went wrong); collapse every older attempt pair
into a one-line summary; if still over budget, drop the oldest summaries
first. Every call logs the before/after math to the tracer — a long retry
session staying under budget is only provable if the numbers are visible.
"""
from __future__ import annotations

from harness.models import Message
from harness.tracing.tracer import Tracer

_CHARS_PER_TOKEN = 4


def _estimate_tokens(messages: list[Message]) -> int:
    return sum(len(m.content) for m in messages) // _CHARS_PER_TOKEN


class ContextManager:
    def __init__(self, tracer: Tracer | None = None):
        self._tracer = tracer

    def fit(self, messages: list[Message], budget_tokens: int) -> list[Message]:
        before = _estimate_tokens(messages)
        if before <= budget_tokens:
            self._log(budget_tokens, before, before, len(messages), len(messages), 0)
            return messages

        # messages[0] = system, messages[1] = problem statement. Everything
        # after alternates (assistant code, user failure) per attempt — see
        # InstructionBuilder.build. Keep the head, keep the last pair
        # verbatim, collapse the rest.
        head = messages[:2]
        pairs = messages[2:]
        attempt_pairs = [pairs[i:i + 2] for i in range(0, len(pairs), 2)]

        if not attempt_pairs:
            self._log(budget_tokens, before, before, len(messages), len(messages), 0)
            return messages

        last_pair = attempt_pairs[-1]
        older_pairs = attempt_pairs[:-1]

        collapsed = [
            Message(role="user", content=self._summarize(pair))
            for pair in older_pairs
        ]

        fitted = head + collapsed + last_pair
        after = _estimate_tokens(fitted)
        dropped = 0

        # Still over budget (an enormous last-pair failure dump, or a very
        # tight budget): drop the oldest collapsed summaries first, one at
        # a time, until it fits or nothing's left to drop.
        while after > budget_tokens and collapsed:
            collapsed.pop(0)
            dropped += 1
            fitted = head + collapsed + last_pair
            after = _estimate_tokens(fitted)

        self._log(budget_tokens, before, after, len(messages), len(fitted), dropped)
        return fitted

    def _summarize(self, pair: list[Message]) -> str:
        # pair = [assistant code, user failure detail]. First line of the
        # failure message is already "That attempt failed verification...";
        # the actually useful one-liner is produced by FailureFormatter
        # upstream, but ContextManager only sees raw Messages here, so it
        # falls back to a byte-size-based note — good enough to show
        # "something was dropped" without re-parsing the report.
        assistant_msg = pair[0] if pair else None
        code_chars = len(assistant_msg.content) if assistant_msg else 0
        return f"(earlier attempt collapsed — {code_chars} chars of code + failure detail omitted to fit budget)"

    def _log(self, budget: int, before: int, after: int, kept_before: int, kept_after: int, dropped: int) -> None:
        if self._tracer is None:
            return
        self._tracer.event(
            "context_fit", kind="context",
            attrs={
                "budget_tokens": budget,
                "estimated_tokens_before": before,
                "estimated_tokens_after": after,
                "messages_before": kept_before,
                "messages_after": kept_after,
                "summaries_dropped": dropped,
            },
        )
