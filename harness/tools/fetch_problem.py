"""P3/P15 — `fetch_problem` tool.

The first real tool call of a run: turn what the user typed into an actual
problem statement. Wrapped as a `Tool` (rather than called directly) so it
goes through the same `ApprovalGate` and `Tracer` path as every other tool —
the fetch shows up in the trace, the UI, and the approval log like anything
else the harness does.

This is the one tool that touches the network, and it does so from the harness
process. The sandbox stays network-less; generated code can never reach out.
"""
from __future__ import annotations

import dataclasses

from harness.models import ToolResult
from harness.problems import ProblemFetcher, ProblemFetchError, default_fetcher
from harness.tools.base import Tool
from harness.tracing.tracer import Tracer

# Statements can be long; cap what goes into the trace so one enormous problem
# can't blow up the SSE stream (same reasoning as run_tests' _cap).
_MAX_TRACE_CHARS = 4000


class FetchProblemTool(Tool):
    name = "fetch_problem"
    description = (
        "Fetch a coding problem's statement, constraints and examples from a "
        "reference: a LeetCode number ('295'), slug, or URL; a curated kata id; "
        "or pasted problem text."
    )

    def __init__(self, tracer: Tracer, fetcher: ProblemFetcher | None = None):
        self._tracer = tracer
        self._fetcher = fetcher or default_fetcher()

    def run(self, **kwargs) -> ToolResult:
        ref: str = kwargs["ref"]
        with self._tracer.span(
            "fetch_problem", kind="fetch",
            attrs={"ref": ref[:200]},
        ) as span:
            try:
                raw = self._fetcher.fetch(ref)
            except ProblemFetchError as exc:
                span.set_attr("error", str(exc))
                span.set_attr("ok", False)
                return ToolResult(success=False, output=None, error=str(exc))

            span.set_attr("ok", True)
            span.set_attr("source", raw.source)
            span.set_attr("title", raw.title)
            span.set_attr("difficulty", raw.difficulty)
            span.set_attr("topics", raw.topics)
            span.set_attr("url", raw.url)
            span.set_attr("statement_text", raw.statement_text[:_MAX_TRACE_CHARS])
            span.set_attr("statement_chars", len(raw.statement_text))

        return ToolResult(success=True, output=raw)


def raw_problem_summary(raw) -> dict:
    """Trace/UI-safe dict form of a RawProblem (statement capped)."""
    data = dataclasses.asdict(raw)
    data["statement_html"] = data["statement_html"][:_MAX_TRACE_CHARS]
    data["statement_text"] = data["statement_text"][:_MAX_TRACE_CHARS]
    return data
