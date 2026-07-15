"""P11 — ContextManager token-budget trimming."""
from harness.context.context_manager import ContextManager
from harness.models import Message
from harness.tracing.tracer import Tracer


def _messages(n_attempts: int, chars_per_pair: int = 400):
    head = [Message(role="system", content="sys"), Message(role="user", content="problem")]
    tail = []
    for i in range(n_attempts):
        tail.append(Message(role="assistant", content="x" * chars_per_pair))
        tail.append(Message(role="user", content="y" * chars_per_pair))
    return head + tail


def test_fit_under_budget_returns_unchanged():
    messages = _messages(1, chars_per_pair=10)
    result = ContextManager().fit(messages, budget_tokens=1000)
    assert result == messages


def test_fit_over_budget_collapses_older_attempts_keeps_last_verbatim():
    messages = _messages(5, chars_per_pair=400)
    result = ContextManager().fit(messages, budget_tokens=100)

    # head (2) + collapsed summaries + last pair (2) verbatim
    assert result[0] == messages[0]
    assert result[1] == messages[1]
    assert result[-2:] == messages[-2:]
    assert len(result) < len(messages)
    # collapsed messages shouldn't contain the raw 400-char filler
    collapsed = result[2:-2]
    assert all("collapsed" in m.content for m in collapsed)


def test_fit_logs_budget_math_to_tracer():
    tracer = Tracer(run_id="t")
    messages = _messages(5, chars_per_pair=400)
    ContextManager(tracer=tracer).fit(messages, budget_tokens=100)

    events = [e for e in tracer.events if e.kind == "context"]
    assert len(events) == 1
    attrs = events[0].attrs
    assert attrs["budget_tokens"] == 100
    assert attrs["estimated_tokens_after"] <= attrs["estimated_tokens_before"]
    assert attrs["messages_after"] <= attrs["messages_before"]
