"""P15-P19 — problem pipeline and the judge's effect on a real run.

The headline case: a *wrong* generated test case must not fail a correct
solution. That's the whole reason the judge exists, so it's exercised
end-to-end here rather than only in unit form.
"""
import os

import pytest

from harness.memory.store import AttemptStore
from harness.orchestrator_multi import MultiAgentOrchestrator
from harness.pipeline import ProblemPipeline
from harness.providers.fake import FakeProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.skills.loader import SkillLoader
from harness.subagents.coder import CoderSubagent
from harness.subagents.extractor import ProblemExtractor
from harness.subagents.judge import JudgeSubagent
from harness.subagents.planner import PlannerSubagent
from harness.subagents.tester import TesterSubagent
from harness.subagents.testgen import TestGenSubagent
from harness.tools.base import ApprovalGate
from harness.tools.fetch_problem import FetchProblemTool
from harness.tools.run_code import RunCodeTool
from harness.tools.run_tests import RunTestsTool
from harness.tools.schemas import RUN_TESTS_SCHEMA
from harness.tracing.tracer import Tracer
from harness.verification.oracle import TestOracle


def _pipeline(tracer):
    gate = ApprovalGate(tracer=tracer, policy="auto")
    provider = FakeProvider()
    return ProblemPipeline(
        fetch_tool=FetchProblemTool(tracer=tracer),
        extractor=ProblemExtractor(provider, tracer),
        testgen=TestGenSubagent(provider, tracer),
        tracer=tracer, approval_gate=gate,
    )


def _orchestrator(tracer, prepared, judge=True, memory=None):
    gate = ApprovalGate(tracer=tracer, policy="auto")
    provider = FakeProvider()
    sandbox = SubprocessSandbox()
    run_tests = RunTestsTool(sandbox=sandbox, oracle=TestOracle(), tracer=tracer)
    run_code = RunCodeTool(sandbox=sandbox, tracer=tracer)
    return MultiAgentOrchestrator(
        planner=PlannerSubagent(provider=provider, tracer=tracer),
        coder=CoderSubagent(provider=provider, registry={"run_tests": (run_tests, RUN_TESTS_SCHEMA)},
                            tracer=tracer, approval_gate=gate),
        tester=TesterSubagent(provider=provider, run_tests_tool=run_tests,
                              run_code_tool=run_code, tracer=tracer, approval_gate=gate),
        tracer=tracer, skill_loader=SkillLoader(), memory=memory,
        judge=JudgeSubagent(provider, tracer) if judge else None,
        complexity_budget=prepared.budget,
    )


# --- pipeline ---------------------------------------------------------------

def test_curated_problem_skips_extraction():
    tracer = Tracer(run_id="t")
    prepared = _pipeline(tracer).prepare("binary_search")
    assert prepared.extraction_method == "curated"
    # No extract span at all — nothing to extract from an already-formed kata.
    assert [e for e in tracer.events if e.kind == "extract"] == []


def test_pipeline_attaches_generated_cases():
    prepared = _pipeline(Tracer(run_id="t")).prepare("binary_search")
    problem = prepared.problem
    assert problem.official_cases          # curated ground truth
    assert problem.generated_cases         # from the test-gen agent
    assert all(c.rationale for c in problem.generated_cases)


def test_pipeline_can_skip_generation():
    prepared = _pipeline(Tracer(run_id="t")).prepare("binary_search", generate_tests=False)
    assert prepared.problem.generated_cases == []


def test_pipeline_emits_budget_span():
    tracer = Tracer(run_id="t")
    _pipeline(tracer).prepare("binary_search")
    assert [e for e in tracer.events if e.kind == "budget" and e.status == "ok"]


def test_to_dict_labels_every_case_tier():
    data = _pipeline(Tracer(run_id="t")).prepare("binary_search").to_dict()
    sources = {c["source"] for c in data["test_cases"]}
    assert sources == {"curated", "generated"}
    assert data["budget"]["acceptable"]
    assert data["entry_point"] == "binary_search"


def test_unknown_ref_raises():
    with pytest.raises(RuntimeError):
        _pipeline(Tracer(run_id="t")).prepare("no-such-problem")


# --- judge in a real run ----------------------------------------------------

def test_correct_solution_survives_a_wrong_generated_case(monkeypatch):
    """The headline guarantee: a hallucinated expected value gets ruled
    `bad_test` and discarded rather than failing a correct solution."""
    monkeypatch.setenv("GLASSBOX_SIMULATE_BAD_TESTCASE", "1")

    tracer = Tracer(run_id="judge-run")
    prepared = _pipeline(tracer).prepare("binary_search")
    # The scripted generator adds a case claiming binary_search(...,9) == 99.
    assert any(c.expected == 99 for c in prepared.problem.generated_cases)

    result = _orchestrator(tracer, prepared).solve(prepared.problem, max_attempts=3)

    assert result.passed is True
    final = result.attempts[-1].report
    assert final.oracle_passed is True
    assert final.generated_failures, "the wrong case should have disagreed"
    assert all(f.verdict == "bad_test" for f in final.generated_failures)
    assert final.judged_real_bugs == []

    judge_spans = [e for e in tracer.events if e.kind == "judge" and e.status == "ok"]
    assert judge_spans, "the judge should have been consulted"


def test_judge_not_consulted_without_generated_cases():
    # No generated tier => nothing that could need adjudicating, so the judge
    # must never fire. (With generated cases present the judge legitimately
    # runs whenever they disagree — including on a genuinely buggy attempt.)
    tracer = Tracer(run_id="no-judge")
    prepared = _pipeline(tracer).prepare("binary_search", generate_tests=False)
    result = _orchestrator(tracer, prepared).solve(prepared.problem, max_attempts=3)

    assert result.passed is True
    assert [e for e in tracer.events if e.kind == "judge"] == []


def test_generated_cases_catch_the_buggy_first_attempt():
    # binary_search's scripted attempt 1 is genuinely wrong, so the generated
    # cases should disagree with it and reach the judge.
    tracer = Tracer(run_id="disagree")
    prepared = _pipeline(tracer).prepare("binary_search")
    result = _orchestrator(tracer, prepared).solve(prepared.problem, max_attempts=3)

    first = result.attempts[0].report
    assert first.oracle_passed is False
    assert first.generated_failures
    assert all(f.verdict is not None for f in first.generated_failures)


def test_retry_story_survives_the_new_pipeline():
    """binary_search is still scripted to fail attempt 1 and pass attempt 2."""
    tracer = Tracer(run_id="retry")
    prepared = _pipeline(tracer).prepare("binary_search")
    result = _orchestrator(tracer, prepared).solve(prepared.problem, max_attempts=3)

    assert result.passed is True
    assert len(result.attempts) == 2
    assert result.attempts[0].report.passed is False
    assert result.attempts[1].report.passed is True


def test_budget_reaches_the_coder():
    tracer = Tracer(run_id="budget")
    prepared = _pipeline(tracer).prepare("binary_search")
    _orchestrator(tracer, prepared).solve(prepared.problem, max_attempts=2)

    coder_spans = [
        e for e in tracer.events
        if e.kind == "subagent" and e.attrs.get("role") == "coder" and e.status == "started"
    ]
    assert coder_spans
    assert "complexity_target" in coder_spans[0].attrs


def test_empty_oracle_fails_rather_than_passing_vacuously():
    """A problem with no ground-truth cases must not silently 'pass'."""
    import dataclasses
    tracer = Tracer(run_id="empty")
    prepared = _pipeline(tracer).prepare("binary_search", generate_tests=False)
    empty = dataclasses.replace(prepared.problem, test_cases=[])

    result = _orchestrator(tracer, prepared).solve(empty, max_attempts=1)
    assert result.passed is False
    assert any("no official test cases" in a
               for a in result.attempts[-1].report.advisory_failures)
