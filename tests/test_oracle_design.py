"""P5 — design-problem oracle (LeetCode's 'implement a class' shape).

The operation-sequence harness is what makes problems like #295
(MedianFinder) runnable at all; the function-call template can't express them.
"""
from harness.models import Problem, TestCase
from harness.sandbox.executor import SubprocessSandbox
from harness.verification.oracle import TestOracle, values_equal

_MEDIAN_FINDER = """
import heapq
class MedianFinder:
    def __init__(self):
        self.lo = []
        self.hi = []
    def addNum(self, num):
        heapq.heappush(self.lo, -num)
        heapq.heappush(self.hi, -heapq.heappop(self.lo))
        if len(self.hi) > len(self.lo):
            heapq.heappush(self.lo, -heapq.heappop(self.hi))
    def findMedian(self):
        if len(self.lo) > len(self.hi):
            return -self.lo[0]
        return (-self.lo[0] + self.hi[0]) / 2.0
"""


def _problem() -> Problem:
    return Problem(
        id="find_median_from_data_stream",
        title="Find Median from Data Stream",
        prompt="Implement MedianFinder.",
        function_name="",
        function_signature="class MedianFinder: ...",
        kind="design",
        class_name="MedianFinder",
        category="design",
        difficulty="hard",
        test_cases=[TestCase(
            input=[
                ["MedianFinder", "addNum", "addNum", "findMedian", "addNum", "findMedian"],
                [[], [1], [2], [], [3], []],
            ],
            expected=[None, None, None, 1.5, None, 2.0],
            source="official",
        )],
    )


def _run(code: str):
    sandbox = SubprocessSandbox()
    return TestOracle().verify(_problem(), code, lambda s: sandbox.execute(s, 5.0, 256))


def test_correct_design_solution_passes():
    report = _run(_MEDIAN_FINDER)
    assert report.oracle_passed is True
    assert report.score == 1.0


def test_buggy_design_solution_fails_with_diff():
    buggy = _MEDIAN_FINDER.replace(
        "return (-self.lo[0] + self.hi[0]) / 2.0", "return -self.lo[0]"
    )
    report = _run(buggy)
    assert report.oracle_passed is False
    failed = report.failed_cases[0]
    assert failed.expected == [None, None, None, 1.5, None, 2.0]
    assert failed.actual == [None, None, None, 1, None, 2]


def test_missing_class_reports_error_not_crash():
    report = _run("class SomethingElse:\n    pass\n")
    assert report.oracle_passed is False
    assert report.failed_cases


def test_design_harness_uses_class_name():
    script = TestOracle().build_harness(_problem(), _MEDIAN_FINDER)
    assert "MedianFinder(*_args)" in script
    assert "getattr(_obj, _op)" in script


# --- float tolerance --------------------------------------------------------

def test_float_comparison_is_tolerant():
    # LeetCode: "Answers within 10^-5 of the actual answer will be accepted."
    assert values_equal(1.5000000001, 1.5) is True
    assert values_equal(1.6, 1.5) is False


def test_float_tolerance_applies_inside_lists():
    assert values_equal([None, 1.4999999999, 2.0], [None, 1.5, 2.0]) is True


def test_bools_are_not_treated_as_numbers():
    # bool subclasses int — without a guard, True would compare equal to 1.0.
    assert values_equal(True, 1.0) is False
    assert values_equal(True, True) is True


def test_exact_comparison_still_holds_for_ints_and_strings():
    assert values_equal(3, 3) is True
    assert values_equal(3, 4) is False
    assert values_equal("ab", "ab") is True
    assert values_equal([1, 2], [1, 2]) is True
    assert values_equal([1, 2], [2, 1]) is False
