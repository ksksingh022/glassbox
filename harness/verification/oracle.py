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

# Design problems (LeetCode's "implement a class" shape, e.g. #295
# MedianFinder). A case is [[op names], [arg lists]] and the expected value is
# the list of per-operation returns, with null for the constructor and for
# methods that return nothing.
_DESIGN_HARNESS_TEMPLATE = """
import json

{code}

_test_inputs = {test_inputs_json}
_results = []
for _case in _test_inputs:
    _ops, _op_args = _case[0], _case[1]
    _returns = []
    _obj = None
    try:
        for _i, _op in enumerate(_ops):
            _args = _op_args[_i] if _i < len(_op_args) else []
            if _i == 0:
                _obj = {class_name}(*_args)
                _returns.append(None)
            else:
                _returns.append(getattr(_obj, _op)(*_args))
        _results.append({{"ok": True, "value": _returns}})
    except Exception as _e:
        _results.append({{"ok": False, "error": f"{{type(_e).__name__}}: {{_e}}"}})
print(json.dumps(_results))
"""


# LeetCode states float answers are accepted "within 10^-5 of the actual
# answer" — e.g. #295's findMedian returns 1.5. An exact `==` would fail
# correct solutions that differ in the last bit, so comparison is tolerant for
# floats (and for floats nested inside the per-operation return lists that
# design problems produce). Ints and everything else stay exact.
_FLOAT_TOLERANCE = 1e-5


def values_equal(actual, expected) -> bool:
    # bool is an int subclass, so Python considers True == 1.0. A function that
    # should return True but returns 1.0 (or vice versa) is a real mismatch, so
    # only compare bools with bools.
    if isinstance(expected, bool) != isinstance(actual, bool):
        return False
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, float) or isinstance(actual, float):
        if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
            return False
        return abs(actual - expected) <= _FLOAT_TOLERANCE
    if isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple)):
        if len(expected) != len(actual):
            return False
        return all(values_equal(a, e) for a, e in zip(actual, expected))
    return actual == expected


class TestOracle:
    def build_harness(self, kata: Kata, code: str, cases: list | None = None) -> str:
        """`cases` lets a caller verify a subset (e.g. only the generated
        tier) against the same code; defaults to the problem's own cases."""
        selected = kata.test_cases if cases is None else cases
        test_inputs = [tc.input for tc in selected]
        if getattr(kata, "kind", "function") == "design":
            return _DESIGN_HARNESS_TEMPLATE.format(
                code=code,
                test_inputs_json=json.dumps(test_inputs),
                class_name=kata.class_name,
            )
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
        cases: list | None = None,
    ) -> VerificationReport:
        selected = kata.test_cases if cases is None else cases
        script = self.build_harness(kata, code, cases=selected)
        exec_result = exec_fn(script)

        if exec_result.timed_out:
            failed = [
                FailedCase(input=tc.input, expected=tc.expected, actual=None,
                           error="sandbox timeout")
                for tc in selected
            ]
            return VerificationReport(
                oracle_passed=False, score=0.0, failed_cases=failed,
                exec_result=exec_result,
            )

        if exec_result.exit_code != 0:
            failed = [
                FailedCase(input=tc.input, expected=tc.expected, actual=None,
                           error=exec_result.stderr.strip()[-300:] or "non-zero exit")
                for tc in selected
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
                for tc in selected
            ]
            return VerificationReport(
                oracle_passed=False, score=0.0, failed_cases=failed,
                exec_result=exec_result,
            )

        failed_cases: list[FailedCase] = []
        for tc, result in zip(selected, results):
            if result.get("ok") and values_equal(result.get("value"), tc.expected):
                continue
            failed_cases.append(FailedCase(
                input=tc.input, expected=tc.expected,
                actual=result.get("value") if result.get("ok") else None,
                error=result.get("error") if not result.get("ok") else None,
            ))

        total = len(selected)
        score = (total - len(failed_cases)) / total if total else 0.0
        return VerificationReport(
            oracle_passed=len(failed_cases) == 0,
            score=score,
            failed_cases=failed_cases,
            exec_result=exec_result,
        )
