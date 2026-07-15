"""P12 — Tester subagent: runs the oracle (authoritative) + extra
edge-case inputs the Coder's code hasn't been checked against (advisory).

Oracle precedence (Decision #5): the oracle's `run_tests` result is the
only thing that can flip pass/fail. Edge cases the Tester invents are
inputs only — it doesn't know their expected output — so a case only
becomes an `advisory_failures` entry if the code crashes or times out on
it, never on a value mismatch. `VerificationReport.passed` stays exactly
`oracle_passed` (see `models.py`); the advisory list rides alongside for
the Coder to see on the next attempt.
"""
from __future__ import annotations

import dataclasses
import json

from harness.costs import estimate_cost
from harness.models import Kata, Message, SubagentResult, SubagentTask
from harness.providers.base import LLMProvider
from harness.subagents.base import Subagent
from harness.tools.base import ApprovalGate
from harness.tools.run_code import RunCodeTool
from harness.tools.run_tests import RunTestsTool
from harness.tracing.tracer import Tracer

_EDGE_CASE_SYSTEM = """You are the Tester in a multi-agent coding harness.

KATA_ID: {kata_id}
ROLE: tester

Propose 3-5 extra EDGE CASE inputs for the function below, beyond what a
typical fixed test suite covers — empty/None/negative/duplicate/very large
values, whatever is plausible for this signature. Respond with ONLY a JSON
array of argument lists (one list of positional args per call), e.g.
[[[], 5], [[1], 1]]. No prose, no markdown fence.

Function signature:
{function_signature}
"""

_MAX_EDGE_CASES = 5


class TesterSubagent(Subagent):
    role = "tester"

    def __init__(
        self,
        provider: LLMProvider,
        run_tests_tool: RunTestsTool,
        run_code_tool: RunCodeTool,
        tracer: Tracer,
        approval_gate: ApprovalGate,
    ):
        self._provider = provider
        self._run_tests_tool = run_tests_tool
        self._run_code_tool = run_code_tool
        self._tracer = tracer
        self._approval_gate = approval_gate

    def run(self, task: SubagentTask) -> SubagentResult:
        kata = task.kata
        code = task.code

        with self._tracer.span(
            "subagent", kind="subagent",
            attrs={"role": "tester", "kata_id": kata.id, "attempt_no": task.attempt_no},
        ) as span:
            report = self._run_oracle(code, kata)
            advisory = self._run_edge_cases(code, kata)
            report = dataclasses.replace(report, advisory_failures=advisory)

            span.set_attr("oracle_passed", report.oracle_passed)
            span.set_attr("score", report.score)
            span.set_attr("advisory_failures", advisory)

        return SubagentResult(role="tester", report=report, advisory_failures=advisory)

    def _run_oracle(self, code: str, kata: Kata):
        approved = self._approval_gate.request(self._run_tests_tool, {"kata_id": kata.id, "requested_by": "tester"})
        if not approved:
            raise RuntimeError("run_tests denied by approval gate")
        tool_result = self._run_tests_tool.run(code=code, kata=kata)
        return tool_result.output

    def _run_edge_cases(self, code: str, kata: Kata) -> list[str]:
        messages = [Message(role="system", content=_EDGE_CASE_SYSTEM.format(
            kata_id=kata.id, function_signature=kata.function_signature,
        ))]
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

        edge_inputs = self._parse_inputs(completion.text)

        advisory: list[str] = []
        for args in edge_inputs[:_MAX_EDGE_CASES]:
            approved = self._approval_gate.request(self._run_code_tool, {"kata_id": kata.id, "requested_by": "tester"})
            if not approved:
                continue
            script = (
                f"{code}\n"
                f"try:\n"
                f"    print(repr({kata.function_name}(*{args!r})))\n"
                f"except Exception as _e:\n"
                f"    print(f'EDGE_CASE_ERROR: {{type(_e).__name__}}: {{_e}}')\n"
            )
            tool_result = self._run_code_tool.run(code=script)
            exec_result = tool_result.output
            if exec_result.timed_out:
                advisory.append(f"edge case {args!r} timed out")
            elif exec_result.exit_code != 0 or "EDGE_CASE_ERROR" in exec_result.stdout:
                detail = exec_result.stdout.strip() or exec_result.stderr.strip()[-200:]
                advisory.append(f"edge case {args!r} raised: {detail}")
        return advisory

    def _parse_inputs(self, text: str) -> list:
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
