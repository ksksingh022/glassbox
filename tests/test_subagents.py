"""P12 — Planner/Coder/Tester subagents against FakeProvider."""
from harness.models import SubagentTask
from harness.providers.fake import FakeProvider
from harness.sandbox.executor import SubprocessSandbox
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


def _tracer():
    return Tracer(run_id="t")


def test_planner_produces_plan_steps_no_code():
    tracer = _tracer()
    planner = PlannerSubagent(provider=FakeProvider(), tracer=tracer)
    kata = load("binary_search")

    result = planner.run(SubagentTask(kata=kata, attempt_no=1))

    assert result.role == "planner"
    assert len(result.plan_steps) >= 1
    assert result.code == ""
    assert any(e.kind == "subagent" for e in tracer.events)


def test_coder_produces_buggy_then_fixed_code_by_attempt():
    tracer = _tracer()
    gate = ApprovalGate(tracer=tracer, policy="auto")
    run_tests_tool = RunTestsTool(sandbox=SubprocessSandbox(), oracle=TestOracle(), tracer=tracer)
    registry = {"run_tests": (run_tests_tool, RUN_TESTS_SCHEMA)}
    coder = CoderSubagent(provider=FakeProvider(), registry=registry, tracer=tracer, approval_gate=gate)
    kata = load("binary_search")

    attempt1 = coder.run(SubagentTask(kata=kata, attempt_no=1, plan_steps=["do it"]))
    attempt2 = coder.run(SubagentTask(kata=kata, attempt_no=2, plan_steps=["do it"]))

    assert "while low < high" in attempt1.code  # buggy version
    assert "while low <= high" in attempt2.code  # fixed version


def test_tester_runs_oracle_and_flags_advisory_but_never_flips_pass():
    tracer = _tracer()
    gate = ApprovalGate(tracer=tracer, policy="auto")
    sandbox = SubprocessSandbox()
    run_tests_tool = RunTestsTool(sandbox=sandbox, oracle=TestOracle(), tracer=tracer)
    run_code_tool = RunCodeTool(sandbox=sandbox, tracer=tracer)
    tester = TesterSubagent(
        provider=FakeProvider(), run_tests_tool=run_tests_tool,
        run_code_tool=run_code_tool, tracer=tracer, approval_gate=gate,
    )
    kata = load("binary_search")
    fixed_code = (
        "def binary_search(arr, target):\n"
        "    low, high = 0, len(arr) - 1\n"
        "    while low <= high:\n"
        "        mid = (low + high) // 2\n"
        "        if arr[mid] == target:\n            return mid\n"
        "        elif arr[mid] < target:\n            low = mid + 1\n"
        "        else:\n            high = mid - 1\n"
        "    return -1\n"
    )

    result = tester.run(SubagentTask(kata=kata, attempt_no=2, code=fixed_code))

    assert result.role == "tester"
    assert result.report.oracle_passed is True
    assert result.report.passed is True  # oracle precedence — advisory can't flip this
