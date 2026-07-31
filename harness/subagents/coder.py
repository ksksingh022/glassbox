"""P12 — Coder subagent: plan + skill context -> implementation.

Reuses `ToolCallLoop` (the same LLM<->tool turn-taking Phase 1's
`Orchestrator` uses) instead of re-implementing it — the Coder is just a
different caller with a different prompt and no forced final oracle check
(that's the Tester's job, per the plan's subagent flow).
"""
from __future__ import annotations

from harness.models import Message, SubagentResult, SubagentTask
from harness.providers.base import LLMProvider
from harness.subagents.base import Subagent
from harness.tool_loop import ToolCallLoop
from harness.tools.base import ApprovalGate, Tool
from harness.tracing.tracer import Tracer

_SYSTEM_TEMPLATE = """You are the Coder in a multi-agent coding harness.

KATA_ID: {kata_id}
ATTEMPT: {attempt_no}
ROLE: coder

Implement exactly the function described below, matching its signature,
following this plan from the Planner:
{plan}
{budget_block}{skill_block}
You have several tools available — use whichever actually help; you don't
need all of them and some problems need none:
{tool_list}
When you are ready to give your final answer, respond with ONLY a single
```python fenced code block containing the function — no tool call, no
explanation text outside the code block.
"""


class CoderSubagent(Subagent):
    role = "coder"

    def __init__(
        self,
        provider: LLMProvider,
        registry: dict[str, tuple[Tool, object]],
        tracer: Tracer,
        approval_gate: ApprovalGate,
        max_tool_calls: int = 5,
    ):
        self._provider = provider
        self._registry = registry
        self._tracer = tracer
        self._approval_gate = approval_gate
        self._max_tool_calls = max_tool_calls

    def run(self, task: SubagentTask) -> SubagentResult:
        kata = task.kata
        plan_text = (
            "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(task.plan_steps))
            or "  (no plan provided — use your own judgment)"
        )
        skill_block = (
            "\n\nRelevant technique notes (loaded skills):\n" + "\n\n".join(task.skill_context)
            if task.skill_context else ""
        )
        # The complexity budget is derived from the problem's own stated
        # constraints (P17) — telling the model the target up front is what
        # stops it writing an O(n^2) solution for n = 10^5.
        budget_block = (
            f"\nPERFORMANCE TARGET: {task.complexity_budget.as_prompt_line()}\n"
            if task.complexity_budget and task.complexity_budget.acceptable != "unknown" else ""
        )
        # Design problems need the class skeleton, not a single def line.
        entry_hint = (
            f"Implement the class `{kata.class_name}` exactly as sketched:\n{kata.starter_code or kata.function_signature}"
            if kata.kind == "design"
            else f"Function signature:\n{kata.function_signature}"
        )
        tool_schemas = [schema for _, schema in self._registry.values()]
        tool_list = "\n".join(f"  - `{s.name}`: {s.description}" for s in tool_schemas) or "  (none offered this run)"

        messages = [
            Message(role="system", content=_SYSTEM_TEMPLATE.format(
                kata_id=kata.id, attempt_no=task.attempt_no, plan=plan_text,
                budget_block=budget_block, skill_block=skill_block, tool_list=tool_list,
            )),
            Message(role="user", content=(
                f"Title: {kata.title}\n"
                f"Category: {kata.category} · Difficulty: {kata.difficulty}\n\n"
                f"{kata.prompt}\n\n"
                f"{entry_hint}"
            )),
        ]
        if task.failure_feedback:
            messages.append(Message(role="user", content=task.failure_feedback))

        with self._tracer.span(
            "subagent", kind="subagent",
            attrs={
                "role": "coder", "kata_id": kata.id, "attempt_no": task.attempt_no,
                "plan_step_count": len(task.plan_steps), "skills_loaded": len(task.skill_context),
                "complexity_target": (
                    task.complexity_budget.acceptable if task.complexity_budget else "unknown"
                ),
            },
        ) as span:
            loop = ToolCallLoop(
                provider=self._provider, registry=self._registry, tracer=self._tracer,
                approval_gate=self._approval_gate, max_tool_calls=self._max_tool_calls,
            )
            result = loop.run(messages, tool_schemas, kata)
            span.set_attr("code_chars", len(result.code))
            span.set_attr("tools_used", result.tools_used)

        return SubagentResult(
            role="coder", code=result.code, tools_used=result.tools_used,
            completion=result.final_completion,
        )
