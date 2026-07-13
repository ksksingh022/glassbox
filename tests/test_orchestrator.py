"""End-to-end retry story: FakeProvider scripts binary_search to fail on
attempt 1 (off-by-one) and pass on attempt 2 — this is the exit-criterion
demo from plan §3.5 ("at least one kata visibly fails attempt 1 and passes
attempt 2"), verified here without hitting a real LLM.
"""
from harness.instructions import InstructionBuilder
from harness.orchestrator import Orchestrator, extract_code
from harness.providers.fake import FakeProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.tools.base import ApprovalGate
from harness.tools.run_tests import RunTestsTool
from harness.tracing.tracer import Tracer
from harness.verification.katas_loader import load
from harness.verification.oracle import TestOracle


def _make_orchestrator():
    tracer = Tracer(run_id="test-run")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    run_tests_tool = RunTestsTool(sandbox=SubprocessSandbox(), oracle=TestOracle(), tracer=tracer)
    return Orchestrator(
        provider=FakeProvider(), run_tests_tool=run_tests_tool,
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    ), tracer


def test_binary_search_fails_then_passes():
    orchestrator, _ = _make_orchestrator()
    kata = load("binary_search")
    result = orchestrator.solve(kata, max_attempts=3)

    assert result.passed is True
    assert len(result.attempts) == 2
    assert result.attempts[0].report.oracle_passed is False
    assert result.attempts[1].report.oracle_passed is True


def test_reverse_string_passes_first_try():
    orchestrator, _ = _make_orchestrator()
    kata = load("reverse_string")
    result = orchestrator.solve(kata, max_attempts=3)

    assert result.passed is True
    assert len(result.attempts) == 1


def test_tracer_emits_events_for_full_run():
    orchestrator, tracer = _make_orchestrator()
    kata = load("binary_search")
    orchestrator.solve(kata, max_attempts=3)

    kinds = {e.kind for e in tracer.events}
    assert "run" in kinds
    assert "instructions" in kinds
    assert "gen_ai.completion" in kinds
    assert "approval_gate" in kinds
    assert "sandbox_exec" in kinds
    assert "verification" in kinds


def test_extract_code_from_fenced_block():
    text = "Here you go:\n```python\ndef f():\n    return 1\n```"
    assert extract_code(text) == "def f():\n    return 1\n"
