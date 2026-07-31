"""Trace event schema.

Field names follow OTel `gen_ai.*` semantic-convention spelling where an
equivalent exists, so Phase 3's "full OTel conformance" is a rename of a
stable shape rather than a rewrite (see plan §3.3.2).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

EventStatus = Literal["started", "ok", "error"]
SpanKind = Literal[
    "orchestrator",
    "instructions",
    "gen_ai.completion",
    "gen_ai.tool",
    "approval_gate",
    "sandbox_exec",
    "verification",
    "run",
    # Phase 2/3 additions
    "subagent",     # P12 — planner/coder/tester span
    "context",      # P11 — ContextManager.fit() budget math
    "skill",        # P13 — a SKILL.md loaded into a subagent's context
    "memory",       # P14 — a durable attempt-log write or a recall hint
    # Phase 4 additions
    "fetch",        # P15 — fetching a problem statement from a source
    "extract",      # P16 — turning a raw statement into a runnable Problem
    "budget",       # P17 — complexity budget derived from constraints
    "testgen",      # P18 — generated edge/happy cases
    "judge",        # P19 — verdict on a generated-case disagreement
]


@dataclass(frozen=True)
class TraceEvent:
    run_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    status: EventStatus
    attrs: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    duration_ms: float | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "attrs": self.attrs,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }
