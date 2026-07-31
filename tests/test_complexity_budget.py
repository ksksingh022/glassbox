"""P17 — ComplexityBudget: constraint parsing + the lookup table."""
import pytest

from harness.complexity_budget import derive, exceeds_budget, extract_max_n, for_constraints


# --- reading the right number out of the constraints ------------------------

def test_extracts_power_notation():
    assert extract_max_n(["1 <= nums.length <= 10^5"]) == 100_000


def test_extracts_coefficient_power():
    assert extract_max_n(["1 <= n <= 5 * 10^4"]) == 50_000


def test_extracts_plain_integer():
    assert extract_max_n(["1 <= nums.length <= 100000"]) == 100_000


def test_ignores_element_value_bounds():
    # A value bound must not be mistaken for a size bound — this is the whole
    # reason the parser exists rather than "take the biggest number".
    constraints = ["1 <= nums.length <= 1000", "-10^9 <= nums[i] <= 10^9"]
    assert extract_max_n(constraints) == 1_000


def test_returns_none_when_no_size_bound():
    assert extract_max_n(["-10^4 <= x <= 10^4"]) is None
    assert extract_max_n([]) is None


def test_takes_largest_size_bound_across_constraints():
    constraints = ["1 <= n <= 100", "1 <= queries <= 10^5"]
    assert extract_max_n(constraints) == 100_000


def test_handles_at_most_calls_phrasing():
    # LeetCode design problems bound the operation count, not an array length.
    assert extract_max_n(["At most 10^5 calls will be made to addNum"]) == 100_000


# --- the table --------------------------------------------------------------

@pytest.mark.parametrize("n,expected", [
    (8, "O(n!)"),
    (10, "O(n!)"),
    (20, "O(2^n)"),
    (100, "O(n^3)"),
    (1_000, "O(n^2)"),
    (100_000, "O(n log n)"),
    (1_000_000, "O(n log n)"),
    (10_000_000, "O(n)"),
    (10**9, "O(log n)"),
])
def test_budget_table_boundaries(n, expected):
    assert derive(n).acceptable == expected


def test_unknown_budget_when_no_bound():
    budget = derive(None)
    assert budget.acceptable == "unknown"
    assert budget.max_n is None


def test_quadratic_flagged_too_slow_at_1e5():
    assert "O(n^2)" in derive(100_000).too_slow


def test_for_constraints_end_to_end():
    budget = for_constraints(["1 <= nums.length <= 10^5", "-10^9 <= nums[i] <= 10^9"])
    assert budget.max_n == 100_000
    assert budget.acceptable == "O(n log n)"


def test_prompt_line_mentions_bound_and_target():
    line = for_constraints(["1 <= n <= 10^5"]).as_prompt_line()
    assert "100,000" in line
    assert "O(n log n)" in line


def test_prompt_line_when_unknown():
    assert "No explicit input bound" in derive(None).as_prompt_line()


# --- cross-check against the static analyzer --------------------------------

def test_exceeds_budget_detects_too_slow():
    budget = derive(100_000)  # O(n log n)
    assert exceeds_budget("O(n^2)", budget) is True
    assert exceeds_budget("O(n log n)", budget) is False
    assert exceeds_budget("O(n)", budget) is False


def test_exceeds_budget_is_silent_when_unknown():
    # Never raise a false alarm on an unparseable guess or an unknown budget.
    assert exceeds_budget("O(n^2)", derive(None)) is False
    assert exceeds_budget("something weird", derive(100_000)) is False
    assert exceeds_budget("", derive(100_000)) is False
