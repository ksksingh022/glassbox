"""P3 — `run_tests` tool: run a submission against a kata's oracle."""
from __future__ import annotations

import dataclasses

from harness.models import Kata, ToolResult
from harness.sandbox.executor import SandboxExecutor
from harness.tools.base import Tool
from harness.tracing.tracer import Tracer
from harness.verification.oracle import TestOracle

# Trace payloads are for humans reading the inspector panel, not for
# reproducing a giant stdout dump — cap any single field so a pathological
# submission can't blow up the SSE stream.
_MAX_FIELD_CHARS = 4000


def _cap(text: str) -> str:
    if len(text) <= _MAX_FIELD_CHARS:
        return text
    return text[:_MAX_FIELD_CHARS] + f"\n... [truncated, {len(text)} chars total]"


class RunTestsTool(Tool):
    name = "run_tests"
    description = "Verify a submitted function against a kata's deterministic oracle."

    def __init__(
        self,
        sandbox: SandboxExecutor,
        oracle: TestOracle,
        tracer: Tracer,
        timeout_s: float = 5.0,
        mem_limit_mb: int = 256,
    ):
        self._sandbox = sandbox
        self._oracle = oracle
        self._tracer = tracer
        self._timeout_s = timeout_s
        self._mem_limit_mb = mem_limit_mb

    def run(self, **kwargs) -> ToolResult:
        code: str = kwargs["code"]
        kata: Kata = kwargs["kata"]
        # Optional case subset — lets the Tester verify the ground-truth tier
        # and the generated tier separately, since only the former decides
        # pass/fail (see the authority ladder in DECISIONS.md).
        cases = kwargs.get("cases")

        def exec_fn(script: str):
            with self._tracer.span(
                "sandbox_exec", kind="sandbox_exec",
                attrs={
                    "kata_id": kata.id, "timeout_s": self._timeout_s,
                    "mem_limit_mb": self._mem_limit_mb, "script": _cap(script),
                },
            ) as span:
                result = self._sandbox.execute(script, self._timeout_s, self._mem_limit_mb)
                span.set_attr("exit_code", result.exit_code)
                span.set_attr("timed_out", result.timed_out)
                span.set_attr("duration_ms", round(result.duration_ms, 2))
                span.set_attr("stdout", _cap(result.stdout))
                span.set_attr("stderr", _cap(result.stderr))
                return result

        selected = kata.test_cases if cases is None else cases
        with self._tracer.span(
            "verification", kind="verification",
            attrs={
                "kata_id": kata.id, "code": code, "test_case_count": len(selected),
                "case_tier": "mixed" if cases is None else (
                    selected[0].source if selected else "empty"
                ),
            },
        ) as span:
            report = self._oracle.verify(kata, code, exec_fn, cases=selected)
            span.set_attr("oracle_passed", report.oracle_passed)
            span.set_attr("score", report.score)
            span.set_attr("failed_case_count", len(report.failed_cases))
            span.set_attr("failed_cases", [dataclasses.asdict(fc) for fc in report.failed_cases])

        return ToolResult(success=report.oracle_passed, output=report)
