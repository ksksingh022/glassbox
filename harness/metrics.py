"""P7 — run metrics.

Rolls the trace up into the numbers that make the harness's value legible:
how long it took, how many tokens it burned, what that cost, and where the
time actually went. Pure aggregation over spans the Tracer already emits —
no new instrumentation — so it can be computed for any run, live or replayed.

The per-phase split is the interesting part: it separates *model* time (which
scales with the model you plug in) from *harness* time (sandbox execution,
verification), which is what shows that a slow small model plus a fast harness
still lands the answer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from harness.costs import estimate_cost
from harness.tracing.events import TraceEvent

# span kind -> the phase bucket its wall time belongs to.
_PHASE_OF_KIND = {
    "fetch": "fetch",
    "extract": "extract",
    "testgen": "testgen",
    "sandbox_exec": "sandbox",
    "verification": "verify",
    "judge": "judge",
}


@dataclass
class AgentUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class RunMetrics:
    wall_ms: float = 0.0
    attempts: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    sandbox_execs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    # Wall time inside the model vs. inside the harness's own machinery.
    llm_ms: float = 0.0
    sandbox_ms: float = 0.0
    phase_ms: dict[str, float] = field(default_factory=dict)
    per_agent: dict[str, AgentUsage] = field(default_factory=dict)
    models_used: list[str] = field(default_factory=list)
    safety_retries: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def harness_ms(self) -> float:
        """Wall time not spent waiting on the model — the harness's own
        overhead, sandboxing and verification included."""
        return max(0.0, self.wall_ms - self.llm_ms)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["per_agent"] = {
            role: {**asdict(usage), "total_tokens": usage.total_tokens}
            for role, usage in self.per_agent.items()
        }
        data["total_tokens"] = self.total_tokens
        data["harness_ms"] = self.harness_ms
        return data


def _role_for(event: TraceEvent, span_roles: dict[str, str]) -> str:
    """Which agent an LLM call belongs to. Subagents tag their own calls with
    `role`; anything untagged came from the single-agent path."""
    role = event.attrs.get("role")
    if role:
        return str(role)
    parent = span_roles.get(event.parent_span_id or "")
    return parent or "agent"


def compute(events: list[TraceEvent], wall_ms: float | None = None) -> RunMetrics:
    metrics = RunMetrics()

    # subagent span_id -> role, so an LLM call nested inside a subagent is
    # attributed to it even when the call itself carries no role tag.
    span_roles: dict[str, str] = {
        e.span_id: str(e.attrs.get("role"))
        for e in events
        if e.kind == "subagent" and e.attrs.get("role")
    }

    models: list[str] = []
    for event in events:
        if event.kind == "orchestrator" and event.name == "attempt" and event.status == "started":
            metrics.attempts += 1

        if event.kind == "gen_ai.tool" and event.status == "started":
            metrics.tool_calls += 1

        if event.kind == "sandbox_exec" and event.status == "started":
            metrics.sandbox_execs += 1

        if event.status != "ok":
            continue

        if event.kind == "run" and wall_ms is None:
            metrics.wall_ms = event.duration_ms or 0.0

        phase = _PHASE_OF_KIND.get(event.kind)
        if phase and event.duration_ms:
            metrics.phase_ms[phase] = metrics.phase_ms.get(phase, 0.0) + event.duration_ms
        if event.kind == "sandbox_exec" and event.duration_ms:
            metrics.sandbox_ms += event.duration_ms

        if event.kind == "gen_ai.completion":
            metrics.llm_calls += 1
            in_tok = int(event.attrs.get("gen_ai.usage.input_tokens") or 0)
            out_tok = int(event.attrs.get("gen_ai.usage.output_tokens") or 0)
            model = str(event.attrs.get("gen_ai.response.model") or "")
            cost = event.attrs.get("gen_ai.usage.cost_usd")
            if cost is None:
                cost = estimate_cost(model, in_tok, out_tok)
            latency = float(event.attrs.get("latency_ms") or event.duration_ms or 0.0)

            metrics.input_tokens += in_tok
            metrics.output_tokens += out_tok
            metrics.cost_usd += float(cost)
            metrics.llm_ms += latency
            metrics.safety_retries += int(event.attrs.get("safety_retries") or 0)
            if model and model not in models:
                models.append(model)

            role = _role_for(event, span_roles)
            usage = metrics.per_agent.setdefault(role, AgentUsage())
            usage.calls += 1
            usage.input_tokens += in_tok
            usage.output_tokens += out_tok
            usage.cost_usd += float(cost)
            usage.latency_ms += latency

    if wall_ms is not None:
        metrics.wall_ms = wall_ms
    metrics.models_used = models
    metrics.cost_usd = round(metrics.cost_usd, 6)
    for usage in metrics.per_agent.values():
        usage.cost_usd = round(usage.cost_usd, 6)
    return metrics
