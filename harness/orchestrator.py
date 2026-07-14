"""P6 — Orchestration (loop + retry policy).

Wires every other primitive together: Instructions -> Provider -> the model
itself decides whether to call a tool via real function-calling, sees the
result, and may iterate before answering -> once it answers with no further
tool call, the harness runs its own authoritative run_tests check regardless
(oracle precedence, Decision #5) -> on failure, structured feedback folds
back into the next attempt's prompt via RunContext.

The model has up to six tools available (see harness/tools/schemas.py),
each a genuinely different kind of help — correctness checking, scratch
execution, line-by-line debugging, offline stdlib docs, static complexity
analysis, and strategic category hints — not six variations on "run the
code." Which ones (if any) it reaches for is the model's call per attempt.
"""
from __future__ import annotations

import json
import re
import time

from harness.instructions import InstructionBuilder
from harness.models import AttemptRecord, Kata, Message, RunContext, RunResult, ToolCall
from harness.providers.base import LLMProvider
from harness.tools.algorithm_hint import AlgorithmHintTool
from harness.tools.base import ApprovalGate, Tool
from harness.tools.complexity_analysis import AnalyzeComplexityTool
from harness.tools.docs_lookup import DocsLookupTool
from harness.tools.run_code import RunCodeTool
from harness.tools.run_tests import RunTestsTool
from harness.tools.schemas import (
    ALGORITHM_HINT_SCHEMA,
    ANALYZE_COMPLEXITY_SCHEMA,
    LOOKUP_DOCS_SCHEMA,
    RUN_CODE_SCHEMA,
    RUN_TESTS_SCHEMA,
    TRACE_EXECUTION_SCHEMA,
)
from harness.tools.trace_execution import TraceExecutionTool
from harness.tracing.tracer import Tracer

_CODE_BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)

# A tool result this large risks blowing a small local model's context
# window on its own (a real incident: an untrimmed 60-step execution trace
# was ~12KB and, stacked on the rest of the conversation, pushed a request
# past Ollama's 4096-token window — a genuine 400, not a formatting bug).
# This is a backstop independent of any individual tool's own truncation,
# so a future tool that forgets to cap its own output still can't do this.
_MAX_TOOL_RESULT_CHARS = 2500


def extract_code(text: str) -> str:
    match = _CODE_BLOCK.search(text)
    return match.group(1).strip() + "\n" if match else text.strip() + "\n"


def _cap_tool_result(text: str) -> str:
    if len(text) <= _MAX_TOOL_RESULT_CHARS:
        return text
    return text[:_MAX_TOOL_RESULT_CHARS] + f"... [truncated, {len(text)} chars total — ask a narrower question]"


class Orchestrator:
    def __init__(
        self,
        provider: LLMProvider,
        run_tests_tool: RunTestsTool,
        approval_gate: ApprovalGate,
        tracer: Tracer,
        instruction_builder: InstructionBuilder | None = None,
        run_code_tool: RunCodeTool | None = None,
        trace_execution_tool: TraceExecutionTool | None = None,
        lookup_docs_tool: DocsLookupTool | None = None,
        analyze_complexity_tool: AnalyzeComplexityTool | None = None,
        algorithm_hint_tool: AlgorithmHintTool | None = None,
        max_tool_calls: int = 5,
    ):
        self._provider = provider
        self._tracer = tracer
        self._approval_gate = approval_gate
        self._instructions = instruction_builder or InstructionBuilder()
        self._max_tool_calls = max_tool_calls

        # (tool instance, its ToolSchema) for every tool actually offered.
        # run_tests is always present — it's the oracle check, not optional.
        self._registry: dict[str, tuple[Tool, object]] = {"run_tests": (run_tests_tool, RUN_TESTS_SCHEMA)}
        if run_code_tool is not None:
            self._registry["run_code"] = (run_code_tool, RUN_CODE_SCHEMA)
        if trace_execution_tool is not None:
            self._registry["trace_execution"] = (trace_execution_tool, TRACE_EXECUTION_SCHEMA)
        if lookup_docs_tool is not None:
            self._registry["lookup_docs"] = (lookup_docs_tool, LOOKUP_DOCS_SCHEMA)
        if analyze_complexity_tool is not None:
            self._registry["analyze_complexity"] = (analyze_complexity_tool, ANALYZE_COMPLEXITY_SCHEMA)
        if algorithm_hint_tool is not None:
            self._registry["algorithm_hint"] = (algorithm_hint_tool, ALGORITHM_HINT_SCHEMA)

        self._run_tests_tool = run_tests_tool

    def solve(self, kata: Kata, max_attempts: int = 3) -> RunResult:
        run_id = self._tracer.run_id
        context = RunContext(kata=kata)
        start = time.monotonic()

        with self._tracer.span("run", kind="run", attrs={"kata_id": kata.id, "max_attempts": max_attempts}):
            passed = False
            while context.attempt_no <= max_attempts and not passed:
                record = self._attempt(context)
                context.attempts.append(record)
                passed = record.report.oracle_passed

        total_duration_ms = (time.monotonic() - start) * 1000
        self._tracer.finish()
        return RunResult(
            run_id=run_id, kata_id=kata.id, passed=passed,
            attempts=context.attempts, total_duration_ms=total_duration_ms,
        )

    def _tool_schemas(self) -> list:
        return [schema for _, schema in self._registry.values()]

    def _attempt(self, context: RunContext) -> AttemptRecord:
        with self._tracer.span(
            "attempt", kind="orchestrator",
            attrs={"attempt_no": context.attempt_no, "kata_id": context.kata.id},
        ) as attempt_span:
            tool_schemas = self._tool_schemas()

            # Convention (for the UI's click-to-inspect panel): attrs passed
            # when a span opens are that primitive's INPUT; attrs set before
            # it closes are its OUTPUT. Both land on the span's "ok" event.
            with self._tracer.span(
                "instructions", kind="instructions",
                attrs={
                    "attempt_no": context.attempt_no,
                    "kata_prompt": context.kata.prompt,
                    "function_signature": context.kata.function_signature,
                    "prior_attempts": len(context.attempts),
                    "tools_available": [s.name for s in tool_schemas],
                },
            ) as span:
                messages = self._instructions.build(context, tool_schemas=tool_schemas)
                span.set_attr("messages", [{"role": m.role, "content": m.content} for m in messages])

            final_completion = None
            last_code = ""
            last_report = None
            tools_used: list[str] = []

            # The model may call a tool (and see the result) up to
            # `max_tool_calls` times before it must answer with plain text.
            # A model that ignores `tools` entirely (or a provider that
            # doesn't support function calling) just answers on the first
            # turn — this loop degenerates to the old single-shot behavior.
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

                if not completion.tool_calls:
                    final_completion = completion
                    break

                # The model asked to call one or more tools. Echo its
                # request back into the conversation, then execute each
                # call for real and feed the result back as a "tool"
                # message so the model can see it on its next turn.
                messages = messages + [Message(role="assistant", content=completion.text, tool_calls=completion.tool_calls)]
                for tc in completion.tool_calls:
                    tools_used.append(tc.name)
                    result_text, report = self._execute_tool_call(tc, context)
                    if report is not None:
                        last_code = tc.arguments.get("code", last_code)
                        last_report = report
                    messages = messages + [Message(role="tool", content=_cap_tool_result(result_text), tool_call_id=tc.id)]
                final_completion = completion  # in case the budget runs out before a text answer

            code = extract_code(final_completion.text) if final_completion.text else last_code

            # Oracle precedence (Decision #5): whatever the model did inside
            # the loop, the harness always runs its own authoritative check
            # on the final code before deciding pass/fail — unless the
            # model's last run_tests call already covered this exact code.
            if last_report is not None and last_code == code:
                report = last_report
            else:
                approved = self._approval_gate.request(self._run_tests_tool, {"kata_id": context.kata.id})
                if not approved:
                    raise RuntimeError("run_tests denied by approval gate")
                with self._tracer.span(
                    "gen_ai.tool", kind="gen_ai.tool",
                    attrs={"gen_ai.tool.name": self._run_tests_tool.name, "code": code, "model_initiated": False},
                ) as span:
                    tool_result = self._run_tests_tool.run(code=code, kata=context.kata)
                    span.set_attr("success", tool_result.success)
                report = tool_result.output

            attempt_span.set_attr("oracle_passed", report.oracle_passed)
            attempt_span.set_attr("score", report.score)
            attempt_span.set_attr("tools_used", tools_used)

            return AttemptRecord(
                attempt_no=context.attempt_no, code=code,
                completion=final_completion, report=report,
            )

    def _execute_tool_call(self, call: ToolCall, context: RunContext) -> tuple[str, object]:
        """Runs a tool the model itself requested. Returns (result text to
        feed back to the model, VerificationReport or None — only run_tests
        produces a report the outer attempt loop can reuse as authoritative)."""
        entry = self._registry.get(call.name)
        if entry is None:
            return f"Unknown tool: {call.name}", None
        tool, _schema = entry

        approved = self._approval_gate.request(tool, {"kata_id": context.kata.id, "requested_by": "model"})
        if not approved:
            return "Tool call denied by approval gate.", None

        with self._tracer.span(
            "gen_ai.tool", kind="gen_ai.tool",
            attrs={"gen_ai.tool.name": call.name, "arguments": call.arguments, "model_initiated": True},
        ) as span:
            if call.name == "run_tests":
                tool_result = tool.run(code=call.arguments.get("code", ""), kata=context.kata)
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

        # trace_execution, lookup_docs, analyze_complexity, algorithm_hint
        # all already return a small JSON-serializable dict (or an error).
        if not tool_result.success and tool_result.output is None:
            return json.dumps({"error": tool_result.error}), None
        return json.dumps(tool_result.output), None
