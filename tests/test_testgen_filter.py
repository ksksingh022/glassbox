"""P18 — generated cases: parsing and the deterministic constraint filter.

The filter is the guard that stops a hallucinated case from becoming part of
the oracle (or blowing the sandbox's memory limit), so it gets the most
attention here.
"""
from harness.models import Problem, TestCase
from harness.providers.fake import FakeProvider
from harness.subagents.testgen import MAX_GENERATED_CASES, TestGenSubagent, filter_cases
from harness.tracing.tracer import Tracer


def _function_problem(max_n=None) -> Problem:
    return Problem(
        id="binary_search", title="Binary Search", prompt="Find target.",
        function_name="binary_search",
        function_signature="def binary_search(arr: list[int], target: int) -> int:",
        test_cases=[], category="algorithms", difficulty="medium", max_n=max_n,
    )


def _design_problem() -> Problem:
    return Problem(
        id="median_finder", title="MedianFinder", prompt="Implement it.",
        function_name="", function_signature="class MedianFinder: ...",
        test_cases=[], category="design", difficulty="hard",
        kind="design", class_name="MedianFinder",
    )


def _case(inp, expected=0, source="generated") -> TestCase:
    return TestCase(input=inp, expected=expected, source=source)


# --- arity / shape ----------------------------------------------------------

def test_keeps_well_formed_case():
    kept, dropped = filter_cases(_function_problem(), [_case([[1, 2, 3], 2])])
    assert len(kept) == 1
    assert dropped == []


def test_drops_wrong_arity():
    kept, dropped = filter_cases(_function_problem(), [_case([[1, 2, 3]])])
    assert kept == []
    assert "signature takes 2" in dropped[0]


def test_drops_non_list_input():
    kept, dropped = filter_cases(_function_problem(), [_case("not a list")])
    assert kept == []
    assert dropped


# --- constraints ------------------------------------------------------------

def test_drops_case_exceeding_stated_bound():
    problem = _function_problem(max_n=10)
    kept, dropped = filter_cases(problem, [_case([list(range(50)), 1])])
    assert kept == []
    assert "exceeds the stated bound" in dropped[0]


def test_keeps_case_at_the_bound():
    problem = _function_problem(max_n=10)
    kept, _ = filter_cases(problem, [_case([list(range(10)), 1])])
    assert len(kept) == 1


def test_drops_absurdly_large_input_even_without_a_bound():
    # No max_n stated, but a million-element array would still blow the
    # sandbox's memory limit and be unreadable in the UI.
    kept, dropped = filter_cases(_function_problem(), [_case([list(range(50_000)), 1])])
    assert kept == []
    assert "too large" in dropped[0]


def test_caps_at_ten_cases():
    cases = [_case([[1], 1]) for _ in range(25)]
    kept, _ = filter_cases(_function_problem(), cases)
    assert len(kept) == MAX_GENERATED_CASES


# --- design shape -----------------------------------------------------------

def test_design_case_must_be_ops_and_args():
    kept, dropped = filter_cases(_design_problem(), [_case([["addNum"]], expected=[None])])
    assert kept == []
    assert "operations" in dropped[0]


def test_design_case_ops_and_args_must_align():
    bad = _case([["MedianFinder", "addNum"], [[]]], expected=[None, None])
    kept, dropped = filter_cases(_design_problem(), [bad])
    assert kept == []
    assert "argument lists" in dropped[0]


def test_design_expected_must_have_one_return_per_op():
    bad = _case([["MedianFinder", "addNum"], [[], [1]]], expected=[None])
    kept, dropped = filter_cases(_design_problem(), [bad])
    assert kept == []
    assert "one return per operation" in dropped[0]


def test_valid_design_case_kept():
    good = _case([["MedianFinder", "addNum"], [[], [1]]], expected=[None, None])
    kept, dropped = filter_cases(_design_problem(), [good])
    assert len(kept) == 1
    assert dropped == []


# --- parsing + end-to-end ---------------------------------------------------

def test_parse_ignores_malformed_entries():
    parsed = TestGenSubagent._parse(
        '[{"input": [[1],1], "expected": 0, "kind": "edge", "rationale": "x"},'
        ' {"nope": true},'
        ' {"input": [[2],2], "expected": 1}]'
    )
    assert len(parsed) == 2
    assert all(c.source == "generated" for c in parsed)
    assert parsed[0].rationale.startswith("edge:")


def test_parse_returns_empty_on_garbage():
    assert TestGenSubagent._parse("sorry, I can't do that") == []
    assert TestGenSubagent._parse("") == []


def test_generate_end_to_end_with_fake_provider():
    tracer = Tracer(run_id="t")
    cases = TestGenSubagent(FakeProvider(), tracer).generate(_function_problem())
    assert cases
    assert all(c.source == "generated" for c in cases)
    assert all(c.rationale for c in cases)

    span = [e for e in tracer.events if e.kind == "testgen" and e.status == "ok"][0]
    assert span.attrs["kept_count"] == len(cases)
