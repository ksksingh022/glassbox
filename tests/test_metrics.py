"""P7 — run metrics aggregation."""
from harness.metrics import compute
from harness.orchestrator import Orchestrator
from harness.instructions import InstructionBuilder
from harness.providers.fake import FakeProvider
from harness.sandbox.executor import SubprocessSandbox
from harness.tools.base import ApprovalGate
from harness.tools.run_tests import RunTestsTool
from harness.tracing.events import TraceEvent
from harness.tracing.tracer import Tracer
from harness.verification.katas_loader import load
from harness.verification.oracle import TestOracle


def _event(kind, status="ok", attrs=None, duration_ms=None, name=None, span_id="s", parent=None):
    return TraceEvent(
        run_id="r", span_id=span_id, parent_span_id=parent, name=name or kind,
        kind=kind, status=status, attrs=attrs or {}, duration_ms=duration_ms,
    )


def test_counts_attempts_and_tool_calls():
    events = [
        _event("orchestrator", "started", name="attempt"),
        _event("orchestrator", "started", name="attempt"),
        _event("gen_ai.tool", "started"),
        _event("sandbox_exec", "started"),
    ]
    m = compute(events)
    assert m.attempts == 2
    assert m.tool_calls == 1
    assert m.sandbox_execs == 1


def test_sums_tokens_and_cost():
    events = [
        _event("gen_ai.completion", attrs={
            "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 20,
            "gen_ai.usage.cost_usd": 0.5, "gen_ai.response.model": "m1", "latency_ms": 300,
        }),
        _event("gen_ai.completion", attrs={
            "gen_ai.usage.input_tokens": 50, "gen_ai.usage.output_tokens": 10,
            "gen_ai.usage.cost_usd": 0.25, "gen_ai.response.model": "m1", "latency_ms": 200,
        }),
    ]
    m = compute(events)
    assert m.llm_calls == 2
    assert m.input_tokens == 150
    assert m.output_tokens == 30
    assert m.total_tokens == 180
    assert m.cost_usd == 0.75
    assert m.llm_ms == 500
    assert m.models_used == ["m1"]


def test_attributes_tokens_per_agent_via_role_tag():
    events = [
        _event("gen_ai.completion", attrs={"role": "coder", "gen_ai.usage.input_tokens": 10,
                                           "gen_ai.usage.output_tokens": 5}),
        _event("gen_ai.completion", attrs={"role": "judge", "gen_ai.usage.input_tokens": 7,
                                           "gen_ai.usage.output_tokens": 1}),
    ]
    m = compute(events)
    assert m.per_agent["coder"].total_tokens == 15
    assert m.per_agent["judge"].total_tokens == 8


def test_attributes_nested_llm_calls_to_their_subagent():
    # An LLM call inside a subagent span carries no role of its own; it must
    # still be attributed to that subagent.
    events = [
        _event("subagent", "started", attrs={"role": "planner"}, span_id="sub1"),
        _event("gen_ai.completion", attrs={"gen_ai.usage.input_tokens": 9,
                                           "gen_ai.usage.output_tokens": 3},
               span_id="call1", parent="sub1"),
    ]
    m = compute(events)
    assert m.per_agent["planner"].total_tokens == 12


def test_phase_split_and_harness_time():
    events = [
        _event("run", duration_ms=1000.0),
        _event("fetch", duration_ms=100.0),
        _event("sandbox_exec", duration_ms=50.0),
        _event("gen_ai.completion", attrs={"latency_ms": 600}),
    ]
    m = compute(events)
    assert m.wall_ms == 1000.0
    assert m.phase_ms["fetch"] == 100.0
    assert m.phase_ms["sandbox"] == 50.0
    assert m.sandbox_ms == 50.0
    assert m.llm_ms == 600
    # Everything not spent waiting on the model is harness overhead.
    assert m.harness_ms == 400.0


def test_explicit_wall_ms_overrides_run_span():
    m = compute([_event("run", duration_ms=10.0)], wall_ms=99.0)
    assert m.wall_ms == 99.0


def test_falls_back_to_estimated_cost_when_span_lacks_it():
    events = [_event("gen_ai.completion", attrs={
        "gen_ai.response.model": "gpt-4o-mini-example-paid",
        "gen_ai.usage.input_tokens": 1000, "gen_ai.usage.output_tokens": 1000,
    })]
    assert compute(events).cost_usd > 0


def test_counts_safety_retries():
    events = [_event("gen_ai.completion", attrs={"safety_retries": 2})]
    assert compute(events).safety_retries == 2


def test_to_dict_is_json_shaped():
    m = compute([_event("gen_ai.completion", attrs={
        "role": "coder", "gen_ai.usage.input_tokens": 4, "gen_ai.usage.output_tokens": 2})])
    data = m.to_dict()
    assert data["total_tokens"] == 6
    assert data["per_agent"]["coder"]["total_tokens"] == 6
    assert "harness_ms" in data


def test_metrics_from_a_real_run():
    """End-to-end: the aggregate must reflect an actual traced run."""
    tracer = Tracer(run_id="metrics-test")
    gate = ApprovalGate(tracer=tracer, policy="auto")
    orchestrator = Orchestrator(
        provider=FakeProvider(),
        run_tests_tool=RunTestsTool(sandbox=SubprocessSandbox(), oracle=TestOracle(), tracer=tracer),
        approval_gate=gate, tracer=tracer, instruction_builder=InstructionBuilder(),
    )
    result = orchestrator.solve(load("binary_search"), max_attempts=3)

    m = compute(tracer.events, wall_ms=result.total_duration_ms)
    assert m.attempts == 2            # binary_search is scripted to fail then pass
    assert m.llm_calls >= 2
    assert m.sandbox_execs >= 2
    assert m.total_tokens > 0
    assert m.wall_ms > 0
    assert m.cost_usd == 0.0          # FakeProvider is free
    assert "sandbox" in m.phase_ms
