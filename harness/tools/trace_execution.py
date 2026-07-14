"""P3 — `trace_execution` tool: sandboxed line-by-line variable trace.

Different from `run_code` (which just captures stdout) and `run_tests`
(which only reports pass/fail): this instruments the candidate code with
`sys.settrace` and reports, for one specific call, the sequence of
(line, function, local variables) as execution proceeds — a real debugger
step-through, not a print statement. Runs inside the same sandbox as every
other execution primitive (Decision #2's trust boundary isn't widened).
"""
from __future__ import annotations

import json

from harness.models import ToolResult
from harness.sandbox.executor import SandboxExecutor
from harness.tools.base import Tool
from harness.tracing.tracer import Tracer

# Kept deliberately small: this output goes back into the model's own
# conversation on a small local model with a limited context window (a
# nested-loop DP trace at 60 steps was ~12KB and blew a 4096-token Ollama
# context on its own). 20 steps is still enough to see a bug happen.
_MAX_STEPS = 20
_MAX_FIELD_CHARS = 4000
_MAX_LOCAL_VALUE_CHARS = 40

_TRACE_TEMPLATE = '''
import sys, json

_trace_steps = []
_max_steps = {max_steps}
_max_value_chars = {max_value_chars}

def _tracer(frame, event, arg):
    if event == "line" and frame.f_code.co_name != "<module>":
        if len(_trace_steps) < _max_steps:
            try:
                snapshot = {{k: repr(v)[:_max_value_chars] for k, v in frame.f_locals.items() if not k.startswith("_")}}
            except Exception:
                snapshot = {{}}
            _trace_steps.append({{"line": frame.f_lineno, "function": frame.f_code.co_name, "locals": snapshot}})
    return _tracer

{code}

_result = None
_error = None
sys.settrace(_tracer)
try:
    _result = repr({call})
except Exception as _e:
    _error = "{{}}: {{}}".format(type(_e).__name__, _e)
finally:
    sys.settrace(None)

print(json.dumps({{"steps": _trace_steps[:_max_steps], "result": _result, "error": _error}}))
'''


def _cap(text: str) -> str:
    return text if len(text) <= _MAX_FIELD_CHARS else text[:_MAX_FIELD_CHARS] + "\n... [truncated]"


class TraceExecutionTool(Tool):
    name = "trace_execution"
    description = (
        "Run one specific call to your code with a line-by-line trace of "
        "local variables at each step, to debug tricky logic (e.g. an "
        "off-by-one or a boundary case). Sandboxed."
    )

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
        code: str = kwargs.get("code", "")
        call: str = kwargs.get("call", "")
        if not call:
            return ToolResult(success=False, output=None, error="call is required, e.g. \"is_match('aa','a*')\"")

        script = _TRACE_TEMPLATE.format(
            code=code, call=call, max_steps=_MAX_STEPS, max_value_chars=_MAX_LOCAL_VALUE_CHARS,
        )

        with self._tracer.span(
            "sandbox_exec", kind="sandbox_exec",
            attrs={
                "timeout_s": self._timeout_s, "mem_limit_mb": self._mem_limit_mb,
                "script": _cap(script), "call": call,
            },
        ) as span:
            result = self._sandbox.execute(script, self._timeout_s, self._mem_limit_mb)
            span.set_attr("exit_code", result.exit_code)
            span.set_attr("timed_out", result.timed_out)
            span.set_attr("duration_ms", round(result.duration_ms, 2))
            span.set_attr("stdout", _cap(result.stdout))
            span.set_attr("stderr", _cap(result.stderr))

        if result.timed_out:
            return ToolResult(success=False, output=None, error="trace timed out")
        if result.exit_code != 0:
            return ToolResult(success=False, output=None, error=(result.stderr or "trace failed")[-500:])

        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return ToolResult(success=False, output=None, error="could not parse trace output")

        return ToolResult(success=data.get("error") is None, output=data)
