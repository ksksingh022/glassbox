"""P10 — Context delivery: structured failure feedback.

Turns a `VerificationReport` into the text a model actually sees on retry —
which case failed, expected vs actual, a short stderr excerpt — never a raw
log dump. Lifted out of `InstructionBuilder` (Phase 1) so the Phase 2
subagents (`PlannerSubagent` on escalation, `CoderSubagent` on every retry)
can format the same way without re-deriving it.
"""
from __future__ import annotations

from harness.models import AttemptRecord


class FailureFormatter:
    def format(self, record: AttemptRecord) -> str:
        report = record.report
        lines = ["That attempt failed verification. Fix the function.", ""]
        for fc in report.failed_cases[:5]:
            if fc.error:
                lines.append(f"- input={fc.input!r} raised: {fc.error}")
            else:
                lines.append(
                    f"- input={fc.input!r} expected={fc.expected!r} got={fc.actual!r}"
                )
        if report.exec_result and report.exec_result.stderr:
            stderr_excerpt = report.exec_result.stderr.strip().splitlines()[-5:]
            lines.append("")
            lines.append("stderr (last lines):")
            lines.extend(stderr_excerpt)

        # Tester's edge-case findings (Phase 2): advisory only — oracle
        # precedence (Decision #5) means these never flip pass/fail, but
        # the Coder should still see them so it can fix real bugs the
        # oracle's fixed test set doesn't happen to cover.
        if report.advisory_failures:
            lines.append("")
            lines.append("Advisory (tester's extra edge cases — does not affect pass/fail):")
            for warning in report.advisory_failures[:5]:
                lines.append(f"- {warning}")

        return "\n".join(lines)

    def one_line_summary(self, record: AttemptRecord) -> str:
        """Condensed form for `ContextManager` to collapse older attempts
        into, instead of dropping them entirely."""
        report = record.report
        return (
            f"attempt {record.attempt_no}: score {report.score:.2f}, "
            f"{len(report.failed_cases)} case(s) failed"
        )
