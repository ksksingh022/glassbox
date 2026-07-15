"""Shared LLM <-> tool-calling inner loop.

Extracted out of the Phase 1 `Orchestrator` so the same "let the model call
tools, see results, iterate until it answers with plain text" mechanics can
be reused by the Phase 2 `CoderSubagent` without copy-pasting ~70 lines.
Policy that's specific to *who* calls this loop (e.g. "always force an
authoritative run_tests check before deciding pass/fail") stays with the
caller — this class only owns the model-facing turn-taking.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from harness.models import Kata, Message, ToolCall
from harness.providers.base import LLMProvider
from harness.tools.base import ApprovalGate, Tool
from harness.tracing.tracer import Tracer

_CODE_BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)

# A tool result this large risks blowing a small local model's context
# window on its own (a real incident: an untrimmed 60-step execution trace
# was ~12KB and, stacked on the rest of the conversation, pushed a request
# past Ollama's 4096-token window — a genuine 400, not a formatting bug).
_MAX_TOOL_RESULT_CHARS = 2500


def extract_code(text: str) -> str:
    match = _CODE_BLOCK.search(text)
    return match.group(1).strip() + "\n" if match else text.strip() + "\n"


def _cap_tool_result(text: str) -> str:
    if len(text) <= _MAX_TOOL_RESULT_CHARS:
        return text
    return text[:_MAX_TOOL_RESULT_CHARS] + f"... [truncated, {len(text)} chars total — ask a narrower question]"


@dataclass
class ToolLoopResult:
    final_completion: object  # Completion
    code: str
    last_report: object | None  # VerificationReport | None
    last_code: str
    tools_used: list[str] = field(default_factory=list)


class ToolCallLoop:
    """Drives up to `max_tool_calls` model turns, each of which may request a
    tool call; executes requested calls for real and feeds results back."""

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

    def run(self, messages: list[Message], tool_schemas: list, kata: Kata) -> ToolLoopResult:
        final_completion = None
        last_code = ""
        last_report = None
        tools_used: list[str] = []

        for call_no in range(self._max_tool_calls + 1):
            offer_tools = call_no < self._max_tool_calls
            with self._tracer.span(
                "llm_call", kind="gen_ai.completion",
                attrs={
                    "gen_ai.system": "openai_compat",
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "tools_offered": [s.name for s in tool_schemas] if offer_tools else [],
                },
            ) as span:
                completion = self._provider.complete(
                    messages, tools=tool_schemas if offer_tools else None,
                )
                span.set_attr("gen_ai.response.model", completion.model_name)
                span.set_attr("gen_ai.usage.input_tokens", completion.input_tokens)
                span.set_attr("gen_ai.usage.output_tokens", completion.output_tokens)
                span.set_attr("latency_ms", round(completion.latency_ms, 2))
                span.set_attr("safety_retries", completion.safety_retries)
                span.set_attr("response_text", completion.text)
                span.set_attr("tool_calls_requested", [tc.name for tc in (completion.tool_calls or [])])

                from harness.costs import estimate_cost
                span.set_attr("gen_ai.usage.cost_usd", estimate_cost(
                    completion.model_name, completion.input_tokens, completion.output_tokens,
                ))

            if not completion.tool_calls:
                final_completion = completion
                break

            messages = messages + [Message(role="assistant", content=completion.text, tool_calls=completion.tool_calls)]
            for tc in completion.tool_calls:
                tools_used.append(tc.name)
                result_text, report = self._execute_tool_call(tc, kata)
                if report is not None:
                    last_code = tc.arguments.get("code", last_code)
                    last_report = report
                messages = messages + [Message(role="tool", content=_cap_tool_result(result_text), tool_call_id=tc.id)]
            final_completion = completion  # in case the budget runs out before a text answer

        code = extract_code(final_completion.text) if final_completion.text else last_code
        return ToolLoopResult(
            final_completion=final_completion, code=code,
            last_report=last_report, last_code=last_code, tools_used=tools_used,
        )

    def _execute_tool_call(self, call: ToolCall, kata: Kata) -> tuple[str, object]:
        entry = self._registry.get(call.name)
        if entry is None:
            return f"Unknown tool: {call.name}", None
        tool, _schema = entry

        approved = self._approval_gate.request(tool, {"kata_id": kata.id, "requested_by": "model"})
        if not approved:
            return "Tool call denied by approval gate.", None

        with self._tracer.span(
            "gen_ai.tool", kind="gen_ai.tool",
            attrs={"gen_ai.tool.name": call.name, "arguments": call.arguments, "model_initiated": True},
        ) as span:
            if call.name == "run_tests":
                tool_result = tool.run(code=call.arguments.get("code", ""), kata=kata)
            else:
                tool_result = tool.run(**call.arguments)
            span.set_attr("success", tool_result.success)

        if call.name == "run_tests":
            report = tool_result.output
            summary = {
                "oracle_passed": report.oracle_passed,
                "score": report.score,
                "failed_cases": [
                    {"input": fc.input, "expected": fc.expected, "actual": fc.actual, "error": fc.error}
                    for fc in report.failed_cases[:5]
                ],
            }
            return json.dumps(summary), report

        if call.name == "run_code":
            exec_result = tool_result.output
            summary = {
                "stdout": exec_result.stdout, "stderr": exec_result.stderr,
                "exit_code": exec_result.exit_code, "timed_out": exec_result.timed_out,
            }
            return json.dumps(summary), None

        if not tool_result.success and tool_result.output is None:
            return json.dumps({"error": tool_result.error}), None
        return json.dumps(tool_result.output), None
