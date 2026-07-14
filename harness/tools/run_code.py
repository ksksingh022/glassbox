"""P3 — `run_code` tool: execute arbitrary code in the sandbox, no oracle."""
from __future__ import annotations

from harness.models import ToolResult
from harness.sandbox.executor import SandboxExecutor
from harness.tools.base import Tool
from harness.tracing.tracer import Tracer


class RunCodeTool(Tool):
    name = "run_code"
    description = "Execute a Python snippet in the sandbox and return stdout/stderr."

    def __init__(
        self,
        sandbox: SandboxExecutor,
        tracer: Tracer,
        timeout_s: float = 5.0,
        mem_limit_mb: int = 256,
    ):
        self._sandbox = sandbox
        self._tracer = tracer
        self._timeout_s = timeout_s
        self._mem_limit_mb = mem_limit_mb

    def run(self, **kwargs) -> ToolResult:
        code: str = kwargs["code"]
        with self._tracer.span(
            "sandbox_exec", kind="sandbox_exec",
            attrs={"timeout_s": self._timeout_s, "mem_limit_mb": self._mem_limit_mb, "script": code},
        ) as span:
            result = self._sandbox.execute(code, self._timeout_s, self._mem_limit_mb)
            span.set_attr("exit_code", result.exit_code)
            span.set_attr("timed_out", result.timed_out)
            span.set_attr("duration_ms", round(result.duration_ms, 2))
            span.set_attr("stdout", result.stdout)
            span.set_attr("stderr", result.stderr)

        success = result.exit_code == 0 and not result.timed_out
        return ToolResult(
            success=success,
            output=result,
            error=None if success else (result.stderr or "timed out"),
        )
