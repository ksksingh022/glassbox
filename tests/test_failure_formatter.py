"""P10 — FailureFormatter."""
from harness.context.failure_formatter import FailureFormatter
from harness.models import AttemptRecord, Completion, ExecResult, FailedCase, VerificationReport


def _record(advisory=None):
    report = VerificationReport(
        oracle_passed=False, score=0.5,
        failed_cases=[FailedCase(input=[1, 2], expected=3, actual=4, error=None)],
        advisory_failures=advisory or [],
        exec_result=ExecResult(stdout="", stderr="Traceback...\nValueError: boom", exit_code=1, duration_ms=1.0, timed_out=False),
    )
    completion = Completion(text="", model_name="fake", input_tokens=1, output_tokens=1, latency_ms=1.0)
    return AttemptRecord(attempt_no=1, code="def f(): pass\n", completion=completion, report=report)


def test_format_includes_failed_case_and_stderr():
    text = FailureFormatter().format(_record())
    assert "input=[1, 2]" in text
    assert "expected=3" in text
    assert "ValueError: boom" in text


def test_format_includes_advisory_failures_labeled_non_authoritative():
    text = FailureFormatter().format(_record(advisory=["edge case [] raised IndexError"]))
    assert "advisory" in text.lower()
    assert "edge case [] raised IndexError" in text


def test_one_line_summary():
    summary = FailureFormatter().one_line_summary(_record())
    assert "attempt 1" in summary
    assert "1 case(s) failed" in summary
