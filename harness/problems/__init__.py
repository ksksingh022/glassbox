"""P15 — Problem sources (Phase 4)."""
from harness.problems.base import (
    ProblemFetcher,
    ProblemFetchError,
    ProblemSource,
    extract_constraints,
    html_to_text,
)
from harness.problems.curated import CuratedSource
from harness.problems.leetcode import LeetCodeSource
from harness.problems.pasted import PastedTextSource

__all__ = [
    "ProblemFetcher", "ProblemFetchError", "ProblemSource",
    "CuratedSource", "LeetCodeSource", "PastedTextSource",
    "extract_constraints", "html_to_text", "default_fetcher",
]


def default_fetcher() -> ProblemFetcher:
    """Curated first (offline, exact ids — never triggers a network call for a
    demo kata), then LeetCode, then pasted text as the catch-all."""
    return ProblemFetcher([CuratedSource(), LeetCodeSource(), PastedTextSource()])
