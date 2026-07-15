"""P12 — Planner subagent: problem -> ordered step plan, no code."""
from __future__ import annotations

import re

from harness.costs import estimate_cost
from harness.models import Message, SubagentResult, SubagentTask
from harness.providers.base import LLMProvider
from harness.subagents.base import Subagent
from harness.tracing.tracer import Tracer

_SYSTEM_TEMPLATE = """You are the Planner in a multi-agent coding harness.

KATA_ID: {kata_id}
ATTEMPT: {attempt_no}
ROLE: planner

Produce an ordered list of implementation steps for the kata below — no code,
just numbered steps a programmer would follow. Think about edge cases as one
of the steps.
{extra}"""

_STEP_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s*(.+)$")


class PlannerSubagent(Subagent):
    role = "planner"

    def __init__(self, provider: LLMProvider, tracer: Tracer):
        self._provider = provider
        self._tracer = tracer

    def run(self, task: SubagentTask) -> SubagentResult:
        kata = task.kata
        extra_lines = []
        if task.previous_plan:
            extra_lines.append(
                "\nThe previous plan below did not lead to a passing solution "
                "after multiple attempts — revise it:\n"
                + "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(task.previous_plan))
            )
        if task.failure_feedback:
            extra_lines.append(f"\nMost recent failure:\n{task.failure_feedback}")
        if task.memory_hint:
            extra_lines.append(f"\nMemory recall: {task.memory_hint}")

        messages = [
            Message(role="system", content=_SYSTEM_TEMPLATE.format(
                kata_id=kata.id, attempt_no=task.attempt_no, extra="".join(extra_lines),
            )),
            Message(role="user", content=(
                f"Title: {kata.title}\n{kata.prompt}\n\n"
                f"Function signature:\n{kata.function_signature}"
            )),
        ]

        with self._tracer.span(
            "subagent", kind="subagent",
            attrs={
                "role": "planner", "kata_id": kata.id, "attempt_no": task.attempt_no,
                "revision": bool(task.previous_plan),
            },
        ) as span:
            with self._tracer.span(
                "llm_call", kind="gen_ai.completion",
                attrs={"gen_ai.system": "openai_compat", "messages": [{"role": m.role, "content": m.content} for m in messages]},
            ) as llm_span:
                completion = self._provider.complete(messages)
                llm_span.set_attr("gen_ai.response.model", completion.model_name)
                llm_span.set_attr("gen_ai.usage.input_tokens", completion.input_tokens)
                llm_span.set_attr("gen_ai.usage.output_tokens", completion.output_tokens)
                llm_span.set_attr("gen_ai.usage.cost_usd", estimate_cost(
                    completion.model_name, completion.input_tokens, completion.output_tokens,
                ))
                llm_span.set_attr("response_text", completion.text)

            plan_steps = self._parse_steps(completion.text)
            span.set_attr("plan_steps", plan_steps)

        return SubagentResult(role="planner", plan_steps=plan_steps)

    def _parse_steps(self, text: str) -> list[str]:
        steps = []
        for line in text.splitlines():
            match = _STEP_LINE.match(line)
            if match:
                steps.append(match.group(1).strip())
        if not steps:
            # Model ignored the numbered-list instruction — fall back to
            # non-empty lines rather than an empty plan.
            steps = [line.strip() for line in text.splitlines() if line.strip()]
        return steps
