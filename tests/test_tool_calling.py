"""Verifies the Orchestrator's inner tool-call loop: a model that actually
uses function-calling (run_tests / run_code) mid-attempt, not just the
single-shot codegen-then-check path FakeProvider exercises elsewhere."""
from harness.instructions import InstructionBuilder
from harness.models import Completion, Message, ToolCall
from harness.orchestrator import Orchestrator
from harness.providers.base import LLMProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.tools.base import ApprovalGate
from harness.tools.run_code import RunCodeTool
from harness.tools.run_tests import RunTestsTool
from harness.tracing.tracer import Tracer
from harness.verification.katas_loader import load
from harness.verification.oracle import TestOracle

_BUGGY = "def reverse_string(s):\n    return s\n"  # identity, wrong
_FIXED = "def reverse_string(s):\n    return s[::-1]\n"


class ScriptedToolCallingProvider(LLMProvider):
    """Simulates a model that: (1) calls run_tests with buggy code, sees it
    fail, (2) calls run_tests again with fixed code, sees it pass, (3) gives
    its final text answer with no further tool call."""

    def __init__(self):
        self.calls = 0
        self.seen_tool_results: list[str] = []

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        # Record the newest tool-result message the orchestrator fed back
        # (if any), so the test can assert the loop really round-tripped
        # through the model — only the tail message, since `messages` grows
        # cumulatively across calls and re-scanning the whole list would
        # double-count results seen on a prior call.
        if messages and messages[-1].role == "tool":
            self.seen_tool_results.append(messages[-1].content)

        if self.calls == 1:
            return Completion(
                text="", model_name="scripted", input_tokens=1, output_tokens=1,
                latency_ms=1.0, tool_calls=[ToolCall(id="c1", name="run_tests", arguments={"code": _BUGGY})],
            )
        if self.calls == 2:
            return Completion(
                text="", model_name="scripted", input_tokens=1, output_tokens=1,
                latency_ms=1.0, tool_calls=[ToolCall(id="c2", name="run_tests", arguments={"code": _FIXED})],
            )
        return Completion(
            text=f"```python\n{_FIXED}```", model_name="scripted",
            input_tokens=1, output_tokens=1, latency_ms=1.0,
        )


def _make_orchestrator(provider, run_code_tool=None):
    tracer = Tracer(run_id="test-tool-calling")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    run_tests_tool = RunTestsTool(sandbox=SubprocessSandbox(), oracle=TestOracle(), tracer=tracer)
    orchestrator = Orchestrator(
        provider=provider, run_tests_tool=run_tests_tool, run_code_tool=run_code_tool,
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    )
    return orchestrator, tracer


def test_model_can_call_run_tests_mid_attempt_and_iterate():
    provider = ScriptedToolCallingProvider()
    orchestrator, tracer = _make_orchestrator(provider)
    kata = load("reverse_string")

    result = orchestrator.solve(kata, max_attempts=3)

    assert result.passed is True
    # The model converged within ONE attempt via two tool calls, not by
    # burning through the outer attempt-retry loop.
    assert len(result.attempts) == 1
    assert result.attempts[0].code.strip() == _FIXED.strip()
    assert provider.calls == 3
    # The tool results the orchestrator fed back must reflect the real
    # oracle outcome (fail then pass), proving genuine round-tripping.
    assert len(provider.seen_tool_results) == 2
    assert '"oracle_passed": false' in provider.seen_tool_results[0]
    assert '"oracle_passed": true' in provider.seen_tool_results[1]


def test_tool_calls_are_traced_as_model_initiated():
    provider = ScriptedToolCallingProvider()
    orchestrator, tracer = _make_orchestrator(provider)
    kata = load("reverse_string")
    orchestrator.solve(kata, max_attempts=3)

    tool_events = [e for e in tracer.events if e.kind == "gen_ai.tool" and e.status == "ok"]
    assert len(tool_events) == 2
    assert all(e.attrs.get("model_initiated") is True for e in tool_events)
    # No extra harness-initiated final check needed — the model's own last
    # run_tests call on the final code is reused (oracle precedence still
    # holds, it's just not redundantly re-run).
    model_initiated_final = [e for e in tracer.events if e.kind == "gen_ai.tool" and e.attrs.get("model_initiated") is False]
    assert model_initiated_final == []


def test_model_can_call_run_code_to_debug():
    class DebugThenAnswerProvider(LLMProvider):
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return Completion(
                    text="", model_name="scripted", input_tokens=1, output_tokens=1, latency_ms=1.0,
                    tool_calls=[ToolCall(id="d1", name="run_code", arguments={"code": "print('abc'[::-1])"})],
                )
            return Completion(
                text=f"```python\n{_FIXED}```", model_name="scripted",
                input_tokens=1, output_tokens=1, latency_ms=1.0,
            )

    provider = DebugThenAnswerProvider()
    tracer = Tracer(run_id="test-run-code")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    sandbox = SubprocessSandbox()
    run_tests_tool = RunTestsTool(sandbox=sandbox, oracle=TestOracle(), tracer=tracer)
    run_code_tool = RunCodeTool(sandbox=sandbox, tracer=tracer)
    orchestrator = Orchestrator(
        provider=provider, run_tests_tool=run_tests_tool, run_code_tool=run_code_tool,
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    )
    kata = load("reverse_string")

    result = orchestrator.solve(kata, max_attempts=2)

    assert result.passed is True
    sandbox_events = [e for e in tracer.events if e.kind == "sandbox_exec" and e.status == "ok"]
    # One sandbox_exec from the model's run_code debug call, one from the
    # harness's final authoritative run_tests check.
    assert len(sandbox_events) >= 2


def test_fake_provider_ignores_tools_param_unchanged_behavior():
    """Backward-compat guard: a provider (like FakeProvider) that doesn't
    return tool_calls must make the loop degenerate to exactly one llm_call
    per attempt, matching pre-tool-calling behavior."""
    from harness.providers.fake import FakeProvider

    tracer = Tracer(run_id="test-backcompat")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    run_tests_tool = RunTestsTool(sandbox=SubprocessSandbox(), oracle=TestOracle(), tracer=tracer)
    orchestrator = Orchestrator(
        provider=FakeProvider(), run_tests_tool=run_tests_tool,
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    )
    kata = load("reverse_string")
    result = orchestrator.solve(kata, max_attempts=3)

    assert result.passed is True
    assert len(result.attempts) == 1
    llm_calls = [e for e in tracer.events if e.kind == "gen_ai.completion" and e.status == "started"]
    assert len(llm_calls) == 1
