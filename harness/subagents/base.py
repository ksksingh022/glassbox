"""P12 — Subagents.

Rule (from the plan, called out as "the #1 thing people get wrong with
subagents"): `Subagent.run()` returns a DISTILLED `SubagentResult` — plan
steps, code, or a verdict — never the raw message transcript a subagent
used internally. The parent orchestrator's context stays small no matter
how many LLM/tool round-trips a subagent burned to get its answer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import SubagentResult, SubagentTask


class Subagent(ABC):
    role: str

    @abstractmethod
    def run(self, task: SubagentTask) -> SubagentResult: ...
