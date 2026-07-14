"""P3 — `algorithm_hint` tool: curated strategic hints by kata category.

A static local knowledge base (not model-generated, not fetched) — the same
kind of thing a competitive-programming cheat sheet gives you: which
*technique* usually applies to this shape of problem, not the solution
itself. Deliberately generic enough to not leak a kata's actual answer.
"""
from __future__ import annotations

from harness.models import ToolResult
from harness.tools.base import Tool

_HINTS: dict[str, str] = {
    "warmup": "These are usually solvable directly with a built-in operation or a single pass — look for a Python builtin or slice trick before writing a loop.",
    "conditionals": "Work out the exact rule table on paper first (which conditions combine and in what order) before coding — most bugs here are ordering/precedence mistakes, not algorithmic ones.",
    "algorithms": "Identify the search/traversal shape first: sorted-array problems usually want binary search (watch your loop bound: `<` vs `<=`); string-matching problems usually want DP over two indices with memoization.",
    "data-structures": "Ask what invariant a stack, queue, or hashmap would maintain for you — most 'balanced'/'matching'/'nesting' problems reduce to a stack; most 'have I seen this before' problems reduce to a set or dict.",
    "sorting": "Sort first, then do a single linear pass over the sorted result — most interval/overlap problems become trivial once the input is ordered by start value.",
}

_DIFFICULTY_NOTE = {
    "hard": " This is marked hard — double-check boundary conditions (empty input, single element, pattern exhausted before string or vice versa) explicitly; that's usually where hard problems are actually lost.",
}


class AlgorithmHintTool(Tool):
    name = "algorithm_hint"
    description = "Get a short strategic hint (which technique usually applies) for a kata's category. Does not reveal the solution."

    def run(self, **kwargs) -> ToolResult:
        category: str = (kwargs.get("category") or "").strip().lower()
        difficulty: str = (kwargs.get("difficulty") or "").strip().lower()

        if category not in _HINTS:
            return ToolResult(
                success=False, output=None,
                error=f"unknown category '{category}'; known categories: {sorted(_HINTS)}",
            )

        hint = _HINTS[category] + _DIFFICULTY_NOTE.get(difficulty, "")
        return ToolResult(success=True, output={"category": category, "hint": hint})
