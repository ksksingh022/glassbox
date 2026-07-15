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

import time

from harness.instructions import InstructionBuilder
from harness.models import AttemptRecord, Kata, RunContext, RunResult
from harness.providers.base import LLMProvider
from harness.tool_loop import ToolCallLoop, extract_code
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

# Re-exported for backwards compatibility — callers (and existing tests)
# import `extract_code` from this module; the implementation now lives in
# `harness.tool_loop` so `CoderSubagent` (Phase 2) can share it.
__all__ = ["Orchestrator", "extract_code"]


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

            # The model may call a tool (and see the result) up to
            # `max_tool_calls` times before it must answer with plain text.
            # A model that ignores `tools` entirely (or a provider that
            # doesn't support function calling) just answers on the first
            # turn — this loop degenerates to the old single-shot behavior.
            loop = ToolCallLoop(
                provider=self._provider, registry=self._registry,
                tracer=self._tracer, approval_gate=self._approval_gate,
                max_tool_calls=self._max_tool_calls,
            )
            loop_result = loop.run(messages, tool_schemas, context.kata)
            code = loop_result.code

            # Oracle precedence (Decision #5): whatever the model did inside
            # the loop, the harness always runs its own authoritative check
            # on the final code before deciding pass/fail — unless the
            # model's last run_tests call already covered this exact code.
            if loop_result.last_report is not None and loop_result.last_code == code:
                report = loop_result.last_report
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
            attempt_span.set_attr("tools_used", loop_result.tools_used)

            return AttemptRecord(
                attempt_no=context.attempt_no, code=code,
                completion=loop_result.final_completion, report=report,
            )
