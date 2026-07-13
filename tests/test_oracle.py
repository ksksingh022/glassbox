from harness.sandbox.executor import SubprocessSandbox
from harness.verification.katas_loader import load
from harness.verification.oracle import TestOracle


def _exec_fn(sandbox, timeout_s=5.0, mem_limit_mb=128):
    return lambda script: sandbox.execute(script, timeout_s, mem_limit_mb)


def test_oracle_passes_correct_solution():
    kata = load("reverse_string")
    oracle = TestOracle()
    sandbox = SubprocessSandbox()
    code = "def reverse_string(s):\n    return s[::-1]\n"
    report = oracle.verify(kata, code, _exec_fn(sandbox))
    assert report.oracle_passed is True
    assert report.failed_cases == []


def test_oracle_reports_failed_cases():
    kata = load("reverse_string")
    oracle = TestOracle()
    sandbox = SubprocessSandbox()
    code = "def reverse_string(s):\n    return s\n"  # identity, wrong
    report = oracle.verify(kata, code, _exec_fn(sandbox))
    assert report.oracle_passed is False
    assert len(report.failed_cases) > 0


def test_oracle_reports_exceptions_as_failures():
    kata = load("binary_search")
    oracle = TestOracle()
    sandbox = SubprocessSandbox()
    code = "def binary_search(arr, target):\n    raise ValueError('boom')\n"
    report = oracle.verify(kata, code, _exec_fn(sandbox))
    assert report.oracle_passed is False
    assert all(fc.error for fc in report.failed_cases)
