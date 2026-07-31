"""P15-P18 — problem preparation pipeline.

    reference -> fetch -> extract -> complexity budget -> generated tests
                                                       -> PreparedProblem

Split out from the orchestrator on purpose: the UI wants to *show* the
question, its constraints, its derived budget and its test cases before (and
while) anything is solved, so `GET /problem/{ref}` runs this alone. It also
keeps the orchestrator about solving rather than about acquiring.

Curated katas short-circuit extraction — they're already fully-formed
`Problem`s with ground-truth cases, so there's nothing for the Extractor to do
and no reason to spend a model call proving it.
"""
from __future__ import annotations

import dataclasses
import time

from harness.complexity_budget import derive, for_constraints
from harness.models import ComplexityBudget, Problem, RawProblem
from harness.subagents.extractor import ProblemExtractor
from harness.subagents.testgen import TestGenSubagent
from harness.tools.base import ApprovalGate
from harness.tools.fetch_problem import FetchProblemTool
from harness.tracing.tracer import Tracer
from harness.verification.katas_loader import load as load_kata


@dataclasses.dataclass
class PreparedProblem:
    problem: Problem                  # official + generated cases merged
    budget: ComplexityBudget
    raw: RawProblem
    extraction_method: str = "curated"
    prepare_ms: float = 0.0

    def to_dict(self) -> dict:
        p = self.problem
        return {
            "id": p.id,
            "title": p.title,
            "difficulty": p.difficulty,
            "topics": p.topics,
            "kind": p.kind,
            "entry_point": p.function_name or p.class_name,
            "signature": p.function_signature,
            "statement_html": p.statement_html,
            "statement_text": p.prompt,
            "constraints": p.constraints,
            "max_n": p.max_n,
            "source_ref": p.source_ref,
            "source": self.raw.source,
            "url": self.raw.url,
            "starter_code": p.starter_code,
            "extraction_method": self.extraction_method,
            "prepare_ms": round(self.prepare_ms, 2),
            "budget": {
                "max_n": self.budget.max_n,
                "acceptable": self.budget.acceptable,
                "also_acceptable": self.budget.also_acceptable,
                "too_slow": self.budget.too_slow,
                "reasoning": self.budget.reasoning,
            },
            "test_cases": [
                {
                    "input": tc.input,
                    "expected": tc.expected,
                    "source": tc.source,
                    "rationale": tc.rationale,
                }
                for tc in p.test_cases
            ],
        }


class ProblemPipeline:
    def __init__(
        self,
        fetch_tool: FetchProblemTool,
        extractor: ProblemExtractor,
        testgen: TestGenSubagent,
        tracer: Tracer,
        approval_gate: ApprovalGate,
    ):
        self._fetch_tool = fetch_tool
        self._extractor = extractor
        self._testgen = testgen
        self._tracer = tracer
        self._approval_gate = approval_gate

    def prepare(self, ref: str, generate_tests: bool = True) -> PreparedProblem:
        start = time.monotonic()

        approved = self._approval_gate.request(self._fetch_tool, {"ref": ref[:120]})
        if not approved:
            raise RuntimeError("fetch_problem denied by approval gate")

        result = self._fetch_tool.run(ref=ref)
        if not result.success:
            raise RuntimeError(result.error or "problem fetch failed")
        raw: RawProblem = result.output

        if raw.source == "curated":
            problem = load_kata(raw.ref)
            method = "curated"
        else:
            problem, method = self._extractor.extract(raw)

        budget = (
            for_constraints(problem.constraints)
            if problem.constraints else derive(problem.max_n)
        )
        with self._tracer.span(
            "budget", kind="budget",
            attrs={"constraints": problem.constraints, "max_n": budget.max_n},
        ) as span:
            span.set_attr("acceptable", budget.acceptable)
            span.set_attr("also_acceptable", budget.also_acceptable)
            span.set_attr("too_slow", budget.too_slow)
            span.set_attr("reasoning", budget.reasoning)
            span.set_attr("prompt_line", budget.as_prompt_line())

        if generate_tests:
            generated = self._testgen.generate(problem)
            if generated:
                problem = dataclasses.replace(
                    problem, test_cases=list(problem.test_cases) + list(generated)
                )

        return PreparedProblem(
            problem=problem, budget=budget, raw=raw,
            extraction_method=method,
            prepare_ms=(time.monotonic() - start) * 1000,
        )
