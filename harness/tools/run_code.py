"""P3 — `run_code` tool: execute arbitrary code in the sandbox, no oracle."""
from __future__ import annotations

from harness.models import ToolResult
from harness.sandbox.executor import SandboxExecutor
from harness.tools.base import Tool


class RunCodeTool(Tool):
    name = "run_code"
    description = "Execute a Python snippet in the sandbox and return stdout/stderr."

    def __init__(self, sandbox: SandboxExecutor, timeout_s: float = 5.0, mem_limit_mb: int = 256):
        self._sandbox = sandbox
        self._timeout_s = timeout_s
        self._mem_limit_mb = mem_limit_mb

    def run(self, **kwargs) -> ToolResult:
        code: str = kwargs["code"]
        result = self._sandbox.execute(code, self._timeout_s, self._mem_limit_mb)
        success = result.exit_code == 0 and not result.timed_out
        return ToolResult(
            success=success,
            output=result,
            error=None if success else (result.stderr or "timed out"),
        )
