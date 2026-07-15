"""P9 — SessionHistory."""
from harness.context.history import SessionHistory
from harness.models import AttemptRecord, Completion, FailedCase, Message, VerificationReport


def _attempt(n):
    report = VerificationReport(oracle_passed=False, score=0.0, failed_cases=[FailedCase(input=[1], expected=2, actual=3)])
    completion = Completion(text="", model_name="fake", input_tokens=1, output_tokens=1, latency_ms=1.0)
    return AttemptRecord(attempt_no=n, code=f"def f(): return {n}\n", completion=completion, report=report)


def test_record_and_for_prompt_builds_code_failure_pairs():
    history = SessionHistory()
    history.record(_attempt(1))
    history.record(_attempt(2))

    head = [Message(role="system", content="sys"), Message(role="user", content="problem")]
    messages = history.for_prompt(head, budget_tokens=10_000)

    assert messages[0] == head[0]
    assert messages[1] == head[1]
    # 2 attempts -> 2 (assistant, user) pairs
    assert len(messages) == 2 + 4
    assert "def f(): return 1" in messages[2].content
    assert "def f(): return 2" in messages[4].content


def test_attempts_property_is_a_copy():
    history = SessionHistory()
    history.record(_attempt(1))
    snapshot = history.attempts
    snapshot.append(_attempt(2))
    assert len(history.attempts) == 1
