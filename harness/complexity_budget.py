"""P17 — Complexity budget.

Works out what time complexity a problem's constraints actually permit, and
hands that to the Coder so it aims at the right algorithm class up front
instead of writing an O(n^2) solution for n = 10^5 and timing out.

Deliberately **deterministic — no LLM**. This is the standard competitive-
programming heuristic (roughly 10^8 simple operations per second), which is a
lookup table, not a judgment call. Keeping it out of the model means the
budget can't be hallucinated and is trivially unit-testable.

The hard part isn't the table, it's reading the right number out of the
constraints: `1 <= nums.length <= 10^5` is a *size* bound (drives complexity)
while `-10^4 <= nums[i] <= 10^4` is a *value* bound (does not). We only look
at size-shaped constraints, and return `None` rather than guessing when none
is found — an honest "no budget" beats a confidently wrong one.
"""
from __future__ import annotations

import re

from harness.models import ComplexityBudget

# "5 * 10^4", "10^5", "10**5", "2 x 10^4" -> a plain integer.
_POWER = re.compile(r"(?:(\d+(?:\.\d+)?)\s*[*x×]\s*)?10\s*(?:\^|\*\*)\s*(\d+)")
_INT = re.compile(r"\d[\d,]*")

# A constraint is about SIZE (how many things) rather than VALUE (how big each
# thing is) if it mentions a length/count-ish term as a whole word.
_SIZE_TERMS = re.compile(
    r"\.length\b|\blen\s*\(|\bn\b|\bm\b|\bsize\b|\bcalls?\b|\boperations?\b"
    r"|\bnodes?\b|\bqueries\b|\bwords\b|\bpoints\b|\belements\b|\blength\b",
    re.IGNORECASE,
)
# `nums[i]`, `matrix[i][j]` — indexing means we're bounding an element's value.
_ELEMENT_INDEX = re.compile(r"\w+\s*\[\s*\w+\s*\]")


def _expand_powers(text: str) -> str:
    def repl(m: re.Match) -> str:
        coeff = float(m.group(1)) if m.group(1) else 1.0
        return str(int(coeff * (10 ** int(m.group(2)))))
    return _POWER.sub(repl, text)


def _numbers_in(text: str) -> list[int]:
    expanded = _expand_powers(text)
    out = []
    for raw in _INT.findall(expanded):
        try:
            out.append(int(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def extract_max_n(constraints: list[str]) -> int | None:
    """Largest *size* bound across the constraints, or None if none is
    size-shaped. Value bounds (`-10^9 <= nums[i] <= 10^9`) are ignored."""
    best: int | None = None
    for c in constraints:
        if not _SIZE_TERMS.search(c):
            continue
        # A line can be both ("1 <= n <= 10^5" is fine, "1 <= nums[i] <= n" is
        # a value bound expressed in terms of n) — indexing wins as a signal.
        if _ELEMENT_INDEX.search(c):
            continue
        for value in _numbers_in(c):
            if value > (best or 0):
                best = value
    return best


# (inclusive upper bound on n, acceptable, also_acceptable, too_slow, why).
# Ordered ascending; the first row whose bound covers n wins.
_TABLE: list[tuple[int, str, list[str], list[str], str]] = [
    (10, "O(n!)", ["O(2^n)", "O(n^3)"], [],
     "n is tiny — even brute-force permutations fit."),
    (20, "O(2^n)", ["O(n^3)", "O(n^2)"], ["O(n!)"],
     "n <= 20 is the classic bitmask/subset range."),
    (100, "O(n^3)", ["O(n^2)", "O(n log n)"], ["O(2^n)"],
     "n <= 100 allows a cubic DP."),
    (1_000, "O(n^2)", ["O(n log n)", "O(n)"], ["O(n^3)"],
     "n <= 1000 allows a quadratic pass but not cubic."),
    (1_000_000, "O(n log n)", ["O(n)"], ["O(n^2)"],
     "n is large — sorting/heap territory; anything quadratic blows up."),
    (10_000_000, "O(n)", ["O(log n)"], ["O(n^2)", "O(n log n)"],
     "n is very large — a single linear pass is the ceiling."),
]
_BEYOND = ("O(log n)", ["O(1)"], ["O(n)", "O(n log n)", "O(n^2)"],
           "n is enormous — only sublinear/constant work is viable.")


def derive(max_n: int | None) -> ComplexityBudget:
    if max_n is None:
        return ComplexityBudget(
            max_n=None, acceptable="unknown", also_acceptable=[], too_slow=[],
            reasoning="No size bound found in the constraints.",
        )
    for bound, acceptable, also, too_slow, why in _TABLE:
        if max_n <= bound:
            return ComplexityBudget(
                max_n=max_n, acceptable=acceptable, also_acceptable=also,
                too_slow=too_slow, reasoning=why,
            )
    acceptable, also, too_slow, why = _BEYOND
    return ComplexityBudget(
        max_n=max_n, acceptable=acceptable, also_acceptable=also,
        too_slow=too_slow, reasoning=why,
    )


def for_constraints(constraints: list[str]) -> ComplexityBudget:
    return derive(extract_max_n(constraints))


# Rough ordering used to compare the static analyzer's Big-O guess against the
# budget. Anything unrecognized sorts last so it never triggers a false alarm.
_ORDER = ["O(1)", "O(log n)", "O(n)", "O(n log n)", "O(n^2)", "O(n^3)", "O(2^n)", "O(n!)"]


def exceeds_budget(guessed: str, budget: ComplexityBudget) -> bool:
    """True when `analyze_complexity`'s static guess is asymptotically worse
    than the budget allows. Advisory only — the guess is a heuristic and a
    false alarm must never fail a run on its own."""
    if budget.acceptable == "unknown" or not guessed:
        return False
    normalized = guessed.replace(" ", "").replace("**", "^").lower()
    lookup = {o.replace(" ", "").lower(): i for i, o in enumerate(_ORDER)}
    gi = lookup.get(normalized)
    bi = lookup.get(budget.acceptable.replace(" ", "").lower())
    if gi is None or bi is None:
        return False
    return gi > bi
