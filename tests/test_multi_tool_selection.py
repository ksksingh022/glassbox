"""Verifies the orchestrator supports a model that reaches for DIFFERENT
tools depending on what it needs — not just repeated run_tests calls. This
is the actual scope-increase the harness was missing: real, situational
tool selection across a diverse toolkit."""
from harness.instructions import InstructionBuilder
from harness.models import Completion, ToolCall
from harness.orchestrator import Orchestrator
from harness.providers.base import LLMProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.tools.algorithm_hint import AlgorithmHintTool
from harness.tools.base import ApprovalGate
from harness.tools.complexity_analysis import AnalyzeComplexityTool
from harness.tools.docs_lookup import DocsLookupTool
from harness.tools.run_tests import RunTestsTool
from harness.tools.trace_execution import TraceExecutionTool
from harness.tracing.tracer import Tracer
from harness.verification.katas_loader import load
from harness.verification.oracle import TestOracle

_FIXED = "def is_balanced(s):\n    pairs = {')': '(', ']': '[', '}': '{'}\n    stack = []\n    for ch in s:\n        if ch in '([{':\n            stack.append(ch)\n        elif ch in pairs:\n            if not stack or stack.pop() != pairs[ch]:\n                return False\n    return not stack\n"


class MultiToolProvider(LLMProvider):
    """Simulates a model that: (1) asks for a strategy hint, (2) traces one
    call to sanity-check its logic, (3) submits final code with no further
    tool call. Three DIFFERENT tools in one attempt, not three run_tests
    calls."""

    def __init__(self):
        self.calls = 0
        self.tool_calls_made: list[str] = []

    def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return Completion(
                text="", model_name="scripted", input_tokens=1, output_tokens=1, latency_ms=1.0,
                tool_calls=[ToolCall(id="c1", name="algorithm_hint", arguments={"category": "data-structures"})],
            )
        if self.calls == 2:
            return Completion(
                text="", model_name="scripted", input_tokens=1, output_tokens=1, latency_ms=1.0,
                tool_calls=[ToolCall(id="c2", name="trace_execution", arguments={"code": _FIXED, "call": "is_balanced('()')"})],
            )
        return Completion(
            text=f"```python\n{_FIXED}```", model_name="scripted",
            input_tokens=1, output_tokens=1, latency_ms=1.0,
        )


def test_model_selects_different_tools_across_one_attempt():
    provider = MultiToolProvider()
    tracer = Tracer(run_id="test-multi-tool")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    sandbox = SubprocessSandbox()
    run_tests_tool = RunTestsTool(sandbox=sandbox, oracle=TestOracle(), tracer=tracer)
    orchestrator = Orchestrator(
        provider=provider,
        run_tests_tool=run_tests_tool,
        trace_execution_tool=TraceExecutionTool(sandbox=sandbox, tracer=tracer),
        algorithm_hint_tool=AlgorithmHintTool(),
        analyze_complexity_tool=AnalyzeComplexityTool(),
        lookup_docs_tool=DocsLookupTool(),
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    )
    kata = load("balanced_brackets")

    result = orchestrator.solve(kata, max_attempts=2)

    assert result.passed is True
    assert len(result.attempts) == 1  # converged in one attempt via tool use, not outer retries

    tool_events = [e for e in tracer.events if e.kind == "gen_ai.tool" and e.status == "ok" and e.attrs.get("model_initiated")]
    names_called = [e.attrs["gen_ai.tool.name"] for e in tool_events]
    assert names_called == ["algorithm_hint", "trace_execution"]

    # The system prompt must actually list all five offered tools (not just
    # run_tests) so a real model has grounds to pick among them.
    instructions_events = [e for e in tracer.events if e.kind == "instructions" and e.status == "ok"]
    system_msg = instructions_events[0].attrs["messages"][0]["content"]
    for tool_name in ["run_tests", "trace_execution", "lookup_docs", "analyze_complexity", "algorithm_hint"]:
        assert tool_name in system_msg


def test_kata_with_no_tool_calls_still_works_unchanged():
    """A model that needs none of the extra tools (e.g. a trivial kata)
    must still work exactly like the pre-multi-tool baseline."""
    from harness.providers.fake import FakeProvider

    tracer = Tracer(run_id="test-no-tools")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    sandbox = SubprocessSandbox()
    run_tests_tool = RunTestsTool(sandbox=sandbox, oracle=TestOracle(), tracer=tracer)
    orchestrator = Orchestrator(
        provider=FakeProvider(),
        run_tests_tool=run_tests_tool,
        trace_execution_tool=TraceExecutionTool(sandbox=sandbox, tracer=tracer),
        algorithm_hint_tool=AlgorithmHintTool(),
        analyze_complexity_tool=AnalyzeComplexityTool(),
        lookup_docs_tool=DocsLookupTool(),
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    )
    kata = load("reverse_string")
    result = orchestrator.solve(kata, max_attempts=2)
    assert result.passed is True
    assert len(result.attempts) == 1
