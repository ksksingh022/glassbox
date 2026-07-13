"""P5 — Verification (test oracle).

The oracle is the sole authority on pass/fail (Decision #5). It never runs
code itself — it wraps the submission in a harness script and hands
execution off to a caller-supplied `exec_fn`, so the oracle stays sandbox-
agnostic and testable with a fake executor.
"""
from __future__ import annotations

import json
from typing import Callable

from harness.models import ExecResult, FailedCase, Kata, VerificationReport

_HARNESS_TEMPLATE = """
import json

{code}

_test_inputs = {test_inputs_json}
_results = []
for _args in _test_inputs:
    try:
        _out = {function_name}(*_args)
        _results.append({{"ok": True, "value": _out}})
    except Exception as _e:
        _results.append({{"ok": False, "error": f"{{type(_e).__name__}}: {{_e}}"}})
print(json.dumps(_results))
"""


class TestOracle:
    def build_harness(self, kata: Kata, code: str) -> str:
        test_inputs = [tc.input for tc in kata.test_cases]
        return _HARNESS_TEMPLATE.format(
            code=code,
            test_inputs_json=json.dumps(test_inputs),
            function_name=kata.function_name,
        )

    def verify(
        self,
        kata: Kata,
        code: str,
        exec_fn: Callable[[str], ExecResult],
    ) -> VerificationReport:
        script = self.build_harness(kata, code)
        exec_result = exec_fn(script)

        if exec_result.timed_out:
            failed = [
                FailedCase(input=tc.input, expected=tc.expected, actual=None,
                           error="sandbox timeout")
                for tc in kata.test_cases
            ]
            return VerificationReport(
                oracle_passed=False, score=0.0, failed_cases=failed,
                exec_result=exec_result,
            )

        if exec_result.exit_code != 0:
            failed = [
                FailedCase(input=tc.input, expected=tc.expected, actual=None,
                           error=exec_result.stderr.strip()[-300:] or "non-zero exit")
                for tc in kata.test_cases
            ]
            return VerificationReport(
                oracle_passed=False, score=0.0, failed_cases=failed,
                exec_result=exec_result,
            )

        try:
            results = json.loads(exec_result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            failed = [
                FailedCase(input=tc.input, expected=tc.expected, actual=None,
                           error="could not parse sandbox output")
                for tc in kata.test_cases
            ]
            return VerificationReport(
                oracle_passed=False, score=0.0, failed_cases=failed,
                exec_result=exec_result,
            )

        failed_cases: list[FailedCase] = []
        for tc, result in zip(kata.test_cases, results):
            if result.get("ok") and result.get("value") == tc.expected:
                continue
            failed_cases.append(FailedCase(
                input=tc.input, expected=tc.expected,
                actual=result.get("value") if result.get("ok") else None,
                error=result.get("error") if not result.get("ok") else None,
            ))

        total = len(kata.test_cases)
        score = (total - len(failed_cases)) / total if total else 0.0
        return VerificationReport(
            oracle_passed=len(failed_cases) == 0,
            score=score,
            failed_cases=failed_cases,
            exec_result=exec_result,
        )
