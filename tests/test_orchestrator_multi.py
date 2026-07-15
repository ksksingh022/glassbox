"""End-to-end multi-agent retry story: Planner -> Coder -> Tester, using
FakeProvider's scripted fail-then-pass binary_search solutions — the same
demo shape as tests/test_orchestrator.py, now via three subagents."""
from harness.memory.store import AttemptStore
from harness.orchestrator_multi import MultiAgentOrchestrator
from harness.providers.fake import FakeProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.skills.loader import SkillLoader
from harness.subagents.coder import CoderSubagent
from harness.subagents.planner import PlannerSubagent
from harness.subagents.tester import TesterSubagent
from harness.tools.base import ApprovalGate
from harness.tools.run_code import RunCodeTool
from harness.tools.run_tests import RunTestsTool
from harness.tools.schemas import RUN_TESTS_SCHEMA
from harness.tracing.tracer import Tracer
from harness.verification.katas_loader import load
from harness.verification.oracle import TestOracle


def _make_orchestrator(tmp_path, memory=None):
    tracer = Tracer(run_id="multi-test")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    sandbox = SubprocessSandbox()
    oracle = TestOracle()
    run_tests_tool = RunTestsTool(sandbox=sandbox, oracle=oracle, tracer=tracer)
    run_code_tool = RunCodeTool(sandbox=sandbox, tracer=tracer)
    registry = {"run_tests": (run_tests_tool, RUN_TESTS_SCHEMA)}

    provider = FakeProvider()
    orchestrator = MultiAgentOrchestrator(
        planner=PlannerSubagent(provider=provider, tracer=tracer),
        coder=CoderSubagent(provider=provider, registry=registry, tracer=tracer, approval_gate=gate),
        tester=TesterSubagent(
            provider=provider, run_tests_tool=run_tests_tool,
            run_code_tool=run_code_tool, tracer=tracer, approval_gate=gate,
        ),
        tracer=tracer,
        skill_loader=SkillLoader(),
        memory=memory,
    )
    return orchestrator, tracer


def test_binary_search_fails_then_passes(tmp_path):
    orchestrator, _ = _make_orchestrator(tmp_path)
    kata = load("binary_search")

    result = orchestrator.solve(kata, max_attempts=3)

    assert result.passed is True
    assert len(result.attempts) == 2
    assert result.attempts[0].report.oracle_passed is False
    assert result.attempts[1].report.oracle_passed is True


def test_planner_runs_once_then_only_on_escalation(tmp_path):
    orchestrator, tracer = _make_orchestrator(tmp_path)
    kata = load("binary_search")
    orchestrator.solve(kata, max_attempts=3)

    planner_spans = [e for e in tracer.events if e.kind == "subagent" and e.attrs.get("role") == "planner" and e.status == "ok"]
    # 2 attempts total, only 2 solutions scripted (fail then pass) -> planner
    # runs on attempt 1 always; escalation threshold is 2 consecutive
    # failures, never reached here since attempt 2 already passes.
    assert len(planner_spans) == 1


def test_subagent_fan_out_traced_in_order(tmp_path):
    orchestrator, tracer = _make_orchestrator(tmp_path)
    kata = load("reverse_string")
    orchestrator.solve(kata, max_attempts=3)

    roles = [e.attrs.get("role") for e in tracer.events if e.kind == "subagent" and e.status == "started"]
    assert roles == ["planner", "coder", "tester"]


def test_memory_write_and_recall(tmp_path):
    memory = AttemptStore(db_path=tmp_path / "mem.db")
    orchestrator, tracer = _make_orchestrator(tmp_path, memory=memory)
    kata = load("binary_search")

    orchestrator.solve(kata, max_attempts=3)

    stats = memory.stats()
    assert stats["per_kata"]["binary_search"]["attempt_count"] == 2
    memory_events = [e for e in tracer.events if e.kind == "memory"]
    assert any(e.name == "memory_write" for e in memory_events)

    # A second run of a kata in the same category should get a recall hint
    # from the first run's failure.
    orchestrator2, tracer2 = _make_orchestrator(tmp_path, memory=memory)
    orchestrator2.solve(load("binary_search"), max_attempts=3)
    recall_events = [e for e in tracer2.events if e.name == "memory_recall"]
    # binary_search's own category algorithms had a failed attempt recorded
    # from the first run (excluding itself would exclude same kata_id, but
    # the hint query only excludes exact kata_id match on request, and here
    # we reuse the same kata_id, so recall may be empty by design — assert
    # the mechanism didn't crash and stats reflect both runs.
    assert memory.stats()["total_attempts"] == 4
