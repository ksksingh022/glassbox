"""P6/P12 — Multi-agent orchestration (Phase 2).

Same outer shape as `Orchestrator.solve` (Phase 1) — an attempt-retry loop
that stops on first pass or `max_attempts` — but each attempt is driven by
three subagents instead of one big tool-calling loop:

    Planner: problem (+ prior failure / memory hint) -> ordered step plan
    Coder:   plan + skill context -> implementation (via ToolCallLoop)
    Tester:  runs the oracle (authoritative) + extra edge cases (advisory)

Escalation policy (this is the "your design decision" the plan calls out
in §4.2): the Planner only runs on attempt 1, or after the Coder has
failed twice in a row — otherwise each retry re-runs Coder+Tester with
structured failure feedback but keeps the existing plan. This is a
Strategy choice, not the only valid one; it's documented in DECISIONS.md.
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field

from harness.context.failure_formatter import FailureFormatter
from harness.memory.store import AttemptStore
from harness.models import AttemptRecord, ComplexityBudget, Kata, RunResult, SubagentTask
from harness.skills.loader import SkillLoader
from harness.subagents.coder import CoderSubagent
from harness.subagents.judge import JudgeSubagent
from harness.subagents.planner import PlannerSubagent
from harness.subagents.tester import TesterSubagent
from harness.tracing.tracer import Tracer

_ESCALATE_AFTER_N_CODER_FAILURES = 2


@dataclass
class MultiAgentRunContext:
    kata: Kata
    attempts: list[AttemptRecord] = field(default_factory=list)
    plan_steps: list[str] = field(default_factory=list)
    consecutive_coder_failures: int = 0

    @property
    def attempt_no(self) -> int:
        return len(self.attempts) + 1


class MultiAgentOrchestrator:
    def __init__(
        self,
        planner: PlannerSubagent,
        coder: CoderSubagent,
        tester: TesterSubagent,
        tracer: Tracer,
        skill_loader: SkillLoader | None = None,
        memory: AttemptStore | None = None,
        failure_formatter: FailureFormatter | None = None,
        session_id: str | None = None,
        judge: JudgeSubagent | None = None,
        complexity_budget: ComplexityBudget | None = None,
    ):
        self._planner = planner
        self._coder = coder
        self._tester = tester
        self._tracer = tracer
        self._skill_loader = skill_loader
        self._memory = memory
        self._formatter = failure_formatter or FailureFormatter()
        self._session_id = session_id or tracer.run_id
        self._judge = judge
        self._budget = complexity_budget

    def solve(self, kata: Kata, max_attempts: int = 3) -> RunResult:
        run_id = self._tracer.run_id
        context = MultiAgentRunContext(kata=kata)
        start = time.monotonic()

        memory_hint = self._memory.recent_failure_hint(kata.category, exclude_kata_id=kata.id) if self._memory else None
        if memory_hint:
            self._tracer.event("memory_recall", kind="memory", attrs={"kata_id": kata.id, "hint": memory_hint})

        with self._tracer.span("run", kind="run", attrs={"kata_id": kata.id, "max_attempts": max_attempts, "mode": "multi"}):
            passed = False
            while context.attempt_no <= max_attempts and not passed:
                record = self._attempt(context, memory_hint)
                context.attempts.append(record)
                # `.passed` rather than `.oracle_passed`: ground-truth cases
                # must pass AND no generated case may have been judged a real
                # bug (see the authority ladder in DECISIONS.md).
                passed = record.report.passed
                self._write_memory(context, record, passed)

        total_duration_ms = (time.monotonic() - start) * 1000
        self._tracer.finish()
        return RunResult(
            run_id=run_id, kata_id=kata.id, passed=passed,
            attempts=context.attempts, total_duration_ms=total_duration_ms,
        )

    def _attempt(self, context: MultiAgentRunContext, memory_hint: str | None) -> AttemptRecord:
        with self._tracer.span(
            "attempt", kind="orchestrator",
            attrs={"attempt_no": context.attempt_no, "kata_id": context.kata.id, "mode": "multi"},
        ) as attempt_span:
            attempt_no = context.attempt_no
            last_failure_text = self._formatter.format(context.attempts[-1]) if context.attempts else None

            run_planner = attempt_no == 1 or context.consecutive_coder_failures >= _ESCALATE_AFTER_N_CODER_FAILURES
            if run_planner:
                planner_task = SubagentTask(
                    kata=context.kata, attempt_no=attempt_no,
                    previous_plan=context.plan_steps, failure_feedback=last_failure_text,
                    memory_hint=memory_hint if attempt_no == 1 else None,
                )
                planner_result = self._planner.run(planner_task)
                context.plan_steps = planner_result.plan_steps
                context.consecutive_coder_failures = 0
                attempt_span.set_attr("planner_ran", True)
                attempt_span.set_attr("escalated", attempt_no > 1)
            else:
                attempt_span.set_attr("planner_ran", False)

            skills = self._skill_loader.select(context.kata) if self._skill_loader else []
            for skill in skills:
                self._tracer.event(
                    "skill_loaded", kind="skill",
                    attrs={"skill": skill.name, "kata_category": context.kata.category},
                )
            skill_context = [f"## {s.name}\n{s.content}" for s in skills]

            coder_task = SubagentTask(
                kata=context.kata, attempt_no=attempt_no, plan_steps=context.plan_steps,
                failure_feedback=last_failure_text, skill_context=skill_context,
                complexity_budget=self._budget,
            )
            coder_result = self._coder.run(coder_task)

            tester_task = SubagentTask(kata=context.kata, attempt_no=attempt_no, code=coder_result.code)
            tester_result = self._tester.run(tester_task)
            report = self._adjudicate(context.kata, coder_result.code, tester_result.report)

            if report.passed:
                context.consecutive_coder_failures = 0
            else:
                context.consecutive_coder_failures += 1

            attempt_span.set_attr("oracle_passed", report.oracle_passed)
            attempt_span.set_attr("passed", report.passed)
            attempt_span.set_attr("score", report.score)
            attempt_span.set_attr("advisory_failure_count", len(report.advisory_failures))
            attempt_span.set_attr("generated_disagreements", len(report.generated_failures))
            attempt_span.set_attr("judged_real_bugs", len(report.judged_real_bugs))
            attempt_span.set_attr("consecutive_coder_failures", context.consecutive_coder_failures)

            return AttemptRecord(
                attempt_no=attempt_no, code=coder_result.code,
                completion=coder_result.completion, report=report,
            )

    def _adjudicate(self, problem, code: str, report):
        """Hand generated-case disagreements to the Judge (P19). Skipped
        entirely when there's no judge or nothing disagreed — the judge is
        never consulted about ground-truth cases, only about the invented
        ones whose expected values might themselves be wrong."""
        if self._judge is None or not report.generated_failures:
            return report
        judged = self._judge.adjudicate(problem, code, report.generated_failures)
        return dataclasses.replace(report, generated_failures=judged)

    def _write_memory(self, context: MultiAgentRunContext, record: AttemptRecord, passed: bool) -> None:
        if self._memory is None:
            return
        failure_reason = None
        if not passed and record.report.failed_cases:
            fc = record.report.failed_cases[0]
            failure_reason = fc.error or f"input={fc.input!r} expected={fc.expected!r} got={fc.actual!r}"

        tokens_used = 0
        if record.completion is not None:
            tokens_used = record.completion.input_tokens + record.completion.output_tokens

        self._memory.record_attempt(
            kata_id=context.kata.id, kata_category=context.kata.category,
            session_id=self._session_id, attempt_no=record.attempt_no, passed=passed,
            duration_ms=0.0, tokens_used=tokens_used, trace_id=self._tracer.run_id,
            failure_reason=failure_reason,
        )
        self._tracer.event(
            "memory_write", kind="memory",
            attrs={"kata_id": context.kata.id, "attempt_no": record.attempt_no, "passed": passed},
        )
