"""P6 — Orchestration (loop + retry policy).

Wires every other primitive together: Instructions -> Provider -> approval
gate -> run_tests tool (sandbox + oracle) -> on failure, structured
feedback folds back into the next attempt's prompt via RunContext.
"""
from __future__ import annotations

import re
import time
import uuid

from harness.instructions import InstructionBuilder
from harness.models import AttemptRecord, Kata, RunContext, RunResult
from harness.providers.base import LLMProvider
from harness.tools.base import ApprovalGate
from harness.tools.run_tests import RunTestsTool
from harness.tracing.tracer import Tracer

_CODE_BLOCK = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    match = _CODE_BLOCK.search(text)
    return match.group(1).strip() + "\n" if match else text.strip() + "\n"


class Orchestrator:
    def __init__(
        self,
        provider: LLMProvider,
        run_tests_tool: RunTestsTool,
        approval_gate: ApprovalGate,
        tracer: Tracer,
        instruction_builder: InstructionBuilder | None = None,
    ):
        self._provider = provider
        self._run_tests_tool = run_tests_tool
        self._approval_gate = approval_gate
        self._tracer = tracer
        self._instructions = instruction_builder or InstructionBuilder()

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

    def _attempt(self, context: RunContext) -> AttemptRecord:
        with self._tracer.span(
            "attempt", kind="orchestrator",
            attrs={"attempt_no": context.attempt_no, "kata_id": context.kata.id},
        ) as attempt_span:
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
                },
            ) as span:
                messages = self._instructions.build(context)
                span.set_attr("messages", [{"role": m.role, "content": m.content} for m in messages])

            with self._tracer.span(
                "llm_call", kind="gen_ai.completion",
                attrs={
                    "gen_ai.system": "openai_compat",
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                },
            ) as span:
                completion = self._provider.complete(messages)
                span.set_attr("gen_ai.response.model", completion.model_name)
                span.set_attr("gen_ai.usage.input_tokens", completion.input_tokens)
                span.set_attr("gen_ai.usage.output_tokens", completion.output_tokens)
                span.set_attr("latency_ms", round(completion.latency_ms, 2))
                span.set_attr("safety_retries", completion.safety_retries)
                span.set_attr("response_text", completion.text)

            code = extract_code(completion.text)

            approved = self._approval_gate.request(self._run_tests_tool, {"kata_id": context.kata.id})
            if not approved:
                raise RuntimeError("run_tests denied by approval gate")

            with self._tracer.span(
                "gen_ai.tool", kind="gen_ai.tool",
                attrs={"gen_ai.tool.name": self._run_tests_tool.name, "code": code},
            ) as span:
                tool_result = self._run_tests_tool.run(code=code, kata=context.kata)
                span.set_attr("success", tool_result.success)

            attempt_span.set_attr("oracle_passed", tool_result.output.oracle_passed)
            attempt_span.set_attr("score", tool_result.output.score)

            return AttemptRecord(
                attempt_no=context.attempt_no, code=code,
                completion=completion, report=tool_result.output,
            )
