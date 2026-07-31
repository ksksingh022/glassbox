"""P12 — Tester subagent: verify a candidate solution against both case tiers.

Two tiers, deliberately kept apart (the authority ladder in DECISIONS.md):

  tier 1 — official/curated cases. Their expected values are ground truth,
           published with the problem. These alone set `oracle_passed`.
  tier 3 — generated cases. Their expected values were *invented* by the
           test-gen agent and may be wrong, so a disagreement here is
           recorded as an unjudged `GeneratedFailure` and handed up to the
           orchestrator, which asks the Judge (P19) to rule on it. The Tester
           never decides that a generated mismatch is a bug.

Crashes and timeouts on a generated case are different — those need no
adjudication, since a correct solution doesn't raise on legal input. They're
surfaced as advisory feedback immediately.
"""
from __future__ import annotations

import dataclasses

from harness.models import (
    GeneratedFailure,
    Problem,
    SubagentResult,
    SubagentTask,
    VerificationReport,
)
from harness.subagents.base import Subagent
from harness.tools.base import ApprovalGate
from harness.tools.run_tests import RunTestsTool
from harness.tracing.tracer import Tracer


class TesterSubagent(Subagent):
    role = "tester"

    def __init__(
        self,
        provider,
        run_tests_tool: RunTestsTool,
        run_code_tool,
        tracer: Tracer,
        approval_gate: ApprovalGate,
    ):
        # `provider`/`run_code_tool` are retained for interface compatibility
        # with the Phase 2 constructor; edge-case *invention* now belongs to
        # the dedicated TestGen agent (P18) rather than being done here.
        self._provider = provider
        self._run_tests_tool = run_tests_tool
        self._run_code_tool = run_code_tool
        self._tracer = tracer
        self._approval_gate = approval_gate

    def run(self, task: SubagentTask) -> SubagentResult:
        problem: Problem = task.kata
        code = task.code

        with self._tracer.span(
            "subagent", kind="subagent",
            attrs={"role": "tester", "kata_id": problem.id, "attempt_no": task.attempt_no},
        ) as span:
            official = problem.official_cases
            generated = problem.generated_cases

            report = self._verify(problem, code, official, tier="official")
            generated_failures, advisory = self._verify_generated(problem, code, generated)

            # Merge, don't overwrite: `_verify` can itself emit an advisory
            # (e.g. "no official test cases were available"), and replacing the
            # list would silently drop exactly the warning that matters most.
            report = dataclasses.replace(
                report,
                advisory_failures=list(report.advisory_failures) + advisory,
                generated_failures=generated_failures,
            )

            span.set_attr("oracle_passed", report.oracle_passed)
            span.set_attr("score", report.score)
            span.set_attr("official_case_count", len(official))
            span.set_attr("generated_case_count", len(generated))
            span.set_attr("generated_disagreements", len(generated_failures))
            span.set_attr("advisory_failures", advisory)

        return SubagentResult(
            role="tester", report=report, advisory_failures=advisory,
        )

    # -- tier 1: ground truth ------------------------------------------------

    def _verify(self, problem: Problem, code: str, cases: list, tier: str) -> VerificationReport:
        if not cases:
            # Nothing authoritative to check against. Fail rather than declare
            # success — an empty oracle silently passing anything is exactly
            # the failure mode that makes a dynamic-problem harness worthless.
            return VerificationReport(
                oracle_passed=False, score=0.0, failed_cases=[],
                advisory_failures=[f"no {tier} test cases were available to verify against"],
            )
        approved = self._approval_gate.request(
            self._run_tests_tool, {"kata_id": problem.id, "requested_by": "tester", "tier": tier}
        )
        if not approved:
            raise RuntimeError("run_tests denied by approval gate")
        return self._run_tests_tool.run(code=code, kata=problem, cases=cases).output

    # -- tier 3: generated, needs adjudication -------------------------------

    def _verify_generated(
        self, problem: Problem, code: str, cases: list
    ) -> tuple[list[GeneratedFailure], list[str]]:
        if not cases:
            return [], []

        approved = self._approval_gate.request(
            self._run_tests_tool,
            {"kata_id": problem.id, "requested_by": "tester", "tier": "generated"},
        )
        if not approved:
            return [], []

        report = self._run_tests_tool.run(code=code, kata=problem, cases=cases).output

        by_input = {repr(c.input): c for c in cases}
        failures: list[GeneratedFailure] = []
        advisory: list[str] = []
        for failed in report.failed_cases:
            case = by_input.get(repr(failed.input))
            rationale = case.rationale if case else ""
            if failed.error:
                # A crash/timeout on a legal input needs no judge — correct
                # code doesn't raise on input the constraints permit.
                advisory.append(
                    f"generated case {failed.input!r} ({rationale}) raised: {failed.error}"
                )
                continue
            failures.append(GeneratedFailure(
                input=failed.input, expected=failed.expected, actual=failed.actual,
                rationale=rationale,
            ))
        return failures, advisory
