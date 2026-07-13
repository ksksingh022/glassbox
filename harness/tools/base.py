"""P3 — Tools + approval gate."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from harness.models import ToolResult
from harness.tracing.tracer import Tracer

GatePolicy = Literal["auto", "block"]


class Tool(ABC):
    name: str
    description: str

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        ...


class ApprovalGate:
    """Gates tool execution. In `auto` mode every call is approved, but the
    gate still fires and its decision is traced — the primitive is real and
    visible even though the demo never blocks (Decision #1). Flip `policy`
    to `"block"` for a production deployment that requires a human
    approval callback (out of scope for Phase 1)."""

    def __init__(self, tracer: Tracer, policy: GatePolicy = "auto"):
        self._tracer = tracer
        self.policy = policy

    def request(self, tool: Tool, args: dict) -> bool:
        approved = self.policy == "auto"
        self._tracer.event(
            name=f"gate: {'auto-approved' if approved else 'blocked'} {tool.name}",
            kind="approval_gate",
            attrs={"tool": tool.name, "policy": self.policy, "approved": approved, **args},
        )
        return approved
