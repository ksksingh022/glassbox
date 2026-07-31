"""P19 — Judge: 3-way verdict routing and its fail-safe defaults.

The invariant under test throughout: the judge must never turn silence,
garbage, or an unparseable answer into a `real_bug`, because that would fail
correct code on the strength of an invented test case.
"""
from harness.models import GeneratedFailure, Problem
from harness.providers.fake import FakeProvider
from harness.subagents.judge import JudgeSubagent
from harness.tracing.tracer import Tracer


def _problem() -> Problem:
    return Problem(
        id="binary_search", title="Binary Search", prompt="Find the target index.",
        function_name="binary_search",
        function_signature="def binary_search(arr, target) -> int:",
        test_cases=[], category="algorithms", difficulty="medium",
    )


def _failure(inp=None, expected=99, actual=4) -> GeneratedFailure:
    return GeneratedFailure(
        input=inp or [[1, 3, 5, 7, 9, 11], 9], expected=expected, actual=actual,
        rationale="edge: deliberately wrong",
    )


# --- parsing ----------------------------------------------------------------

def test_parses_valid_verdicts():
    verdicts = JudgeSubagent._parse(
        '[{"index": 0, "verdict": "bad_test", "reason": "expected is wrong"},'
        ' {"index": 1, "verdict": "real_bug", "reason": "off by one"}]'
    )
    assert [v.verdict for v in verdicts] == ["bad_test", "real_bug"]
    assert verdicts[0].case_index == 0


def test_drops_unknown_verdict_labels():
    # An invented label must not be coerced into a bug ruling.
    verdicts = JudgeSubagent._parse('[{"index": 0, "verdict": "definitely_broken"}]')
    assert verdicts == []


def test_drops_entries_without_an_integer_index():
    assert JudgeSubagent._parse('[{"verdict": "real_bug"}]') == []
    assert JudgeSubagent._parse('[{"index": "zero", "verdict": "real_bug"}]') == []


def test_parse_returns_empty_on_garbage():
    assert JudgeSubagent._parse("I'm not sure what you mean") == []
    assert JudgeSubagent._parse("") == []


# --- adjudication -----------------------------------------------------------

def test_no_failures_means_no_judge_call():
    tracer = Tracer(run_id="t")
    assert JudgeSubagent(FakeProvider(), tracer).adjudicate(_problem(), "code", []) == []
    # Not even a span — the judge is only invoked on a real disagreement.
    assert [e for e in tracer.events if e.kind == "judge"] == []


def test_adjudicates_a_disagreement():
    tracer = Tracer(run_id="t")
    judged = JudgeSubagent(FakeProvider(), tracer).adjudicate(_problem(), "code", [_failure()])
    assert len(judged) == 1
    assert judged[0].verdict == "bad_test"
    assert judged[0].verdict_reason

    span = [e for e in tracer.events if e.kind == "judge" and e.status == "ok"][0]
    assert span.attrs["verdicts"]["bad_test"] == 1


def test_missing_verdict_defaults_to_uncertain_not_real_bug():
    """A judge that returns nothing must not fail a correct solution."""
    class SilentProvider(FakeProvider):
        def complete(self, messages, tools=None, **kwargs):
            completion = super().complete(messages, tools=tools, **kwargs)
            return type(completion)(
                text="[]", model_name=completion.model_name,
                input_tokens=0, output_tokens=0, latency_ms=1.0,
            )

    judged = JudgeSubagent(SilentProvider(), Tracer(run_id="t")).adjudicate(
        _problem(), "code", [_failure(), _failure()]
    )
    assert [j.verdict for j in judged] == ["uncertain", "uncertain"]
    assert all("no verdict" in j.verdict_reason for j in judged)


def test_preserves_case_details_through_adjudication():
    judged = JudgeSubagent(FakeProvider(), Tracer(run_id="t")).adjudicate(
        _problem(), "code", [_failure(expected=42, actual=7)]
    )
    assert judged[0].expected == 42
    assert judged[0].actual == 7
    assert judged[0].rationale == "edge: deliberately wrong"


def test_report_passed_requires_no_real_bug():
    """The authority ladder: a real_bug verdict fails a run whose ground-truth
    cases all passed; bad_test and uncertain do not."""
    from harness.models import VerificationReport

    def report(verdict):
        return VerificationReport(
            oracle_passed=True, score=1.0, failed_cases=[],
            generated_failures=[GeneratedFailure(
                input=[1], expected=2, actual=3, verdict=verdict,
            )],
        )

    assert report("real_bug").passed is False
    assert report("bad_test").passed is True
    assert report("uncertain").passed is True
    # And with no generated failures at all, ground truth alone decides.
    assert VerificationReport(oracle_passed=True, score=1.0, failed_cases=[]).passed is True
    assert VerificationReport(oracle_passed=False, score=0.0, failed_cases=[]).passed is False
