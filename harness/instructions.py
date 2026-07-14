"""P2 — Instructions (system prompt assembly).

Builds the message list handed to the provider each attempt. On retries it
folds in the previous attempt's code and its structured failure detail, so
the model sees exactly what broke instead of re-guessing from scratch.

`KATA_ID:` / `ATTEMPT:` tags are embedded in the system message on purpose:
they're harmless to a real LLM (just more prompt text) but let
`FakeProvider` script deterministic demo behavior without parsing prose.
"""
from __future__ import annotations

from harness.models import Message, RunContext

_SYSTEM_TEMPLATE = """You are a careful Python programmer solving a coding kata.

KATA_ID: {kata_id}
ATTEMPT: {attempt_no}

Rules:
- Implement exactly the function described below, matching its signature.
- You have several tools available — use whichever actually help for this
  specific problem; you don't need all of them and some problems need none:
{tool_list}
- When you are ready to give your final answer, respond with ONLY a single
  ```python fenced code block containing the function — no tool call, no
  explanation text outside the code block. That is what ends the attempt.
"""


class InstructionBuilder:
    def build(self, context: RunContext, tool_schemas: list | None = None) -> list[Message]:
        # Tool descriptions are generated from the same ToolSchema objects
        # the provider uses for real function-calling, so the prompt can
        # never drift out of sync with what's actually callable.
        kata = context.kata
        tool_schemas = tool_schemas or []
        tool_list = "\n".join(f"  - `{t.name}`: {t.description}" for t in tool_schemas) or "  (none offered this run)"
        messages = [
            Message(
                role="system",
                content=_SYSTEM_TEMPLATE.format(
                    kata_id=kata.id, attempt_no=context.attempt_no, tool_list=tool_list,
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Title: {kata.title}\n"
                    f"Category: {kata.category} · Difficulty: {kata.difficulty}\n\n"
                    f"{kata.prompt}\n\n"
                    f"Function signature:\n{kata.function_signature}"
                ),
            ),
        ]

        for record in context.attempts:
            messages.append(Message(
                role="assistant",
                content=f"```python\n{record.code}```",
            ))
            messages.append(Message(
                role="user",
                content=self._format_failure(record),
            ))

        return messages

    def _format_failure(self, record) -> str:
        report = record.report
        lines = ["That attempt failed verification. Fix the function.", ""]
        for fc in report.failed_cases[:5]:
            if fc.error:
                lines.append(f"- input={fc.input!r} raised: {fc.error}")
            else:
                lines.append(
                    f"- input={fc.input!r} expected={fc.expected!r} got={fc.actual!r}"
                )
        if report.exec_result and report.exec_result.stderr:
            stderr_excerpt = report.exec_result.stderr.strip().splitlines()[-5:]
            lines.append("")
            lines.append("stderr (last lines):")
            lines.extend(stderr_excerpt)
        return "\n".join(lines)
