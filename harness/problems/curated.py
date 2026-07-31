"""P15 — Curated (offline) problem source.

The 7 hand-written katas from Phases 1-3. They stay in the product for three
reasons: they're the only problems that run with no network and no API key
(the `FakeProvider` demo), they're what every existing test exercises, and
they give the UI instant quick-picks so a first-time visitor doesn't have to
know a LeetCode number to see the harness work.

Unlike the other sources this one bypasses extraction entirely — a curated
kata is *already* a fully-formed `Problem` with ground-truth cases, so the
Extractor has nothing to do. `ProblemPipeline` checks for that and skips it.
"""
from __future__ import annotations

from harness.models import RawProblem
from harness.problems.base import ProblemSource
from harness.verification.katas_loader import load, load_all


class CuratedSource(ProblemSource):
    name = "curated"

    def can_handle(self, ref: str) -> bool:
        return ref.strip() in load_all()

    def fetch(self, ref: str) -> RawProblem:
        kata = load(ref.strip())
        return RawProblem(
            ref=kata.id,
            title=kata.title,
            statement_html=f"<p>{kata.prompt}</p>",
            statement_text=kata.prompt,
            difficulty=kata.difficulty,
            topics=[kata.category],
            starter_code=kata.function_signature,
            source="curated",
        )
