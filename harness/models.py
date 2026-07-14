"""Shared value objects used across every harness primitive.

Kept in one module (rather than scattered per-primitive) because these
dataclasses are the contracts that cross primitive boundaries — provider,
tools, sandbox, oracle, and orchestrator all speak in terms of them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# P1 — Provider seam
# ---------------------------------------------------------------------------

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation the model requested (from a Completion), or the
    same invocation echoed back into an assistant Message's history."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolSchema:
    """Describes a callable tool to the model, in provider-agnostic form.
    `OpenAICompatProvider` translates this to the OpenAI function-calling
    wire format; a different provider could translate it differently."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for the tool's arguments


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    # Set on an assistant message that requested tool calls (empty content
    # is normal in that case). Set on a "tool" message's tool_call_id to
    # link a tool result back to the request that produced it.
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Completion:
    text: str
    model_name: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    # How many times the provider had to re-issue the call because
    # OpenRouter routed it to a moderation/safety model that returned a
    # bare "User Safety: safe" verdict instead of a real answer (item 2).
    safety_retries: int = 0
    # Populated instead of (or alongside empty) `text` when the model chose
    # to call a tool rather than answer directly.
    tool_calls: list[ToolCall] | None = None


# ---------------------------------------------------------------------------
# Kata definition (verification input)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestCase:
    input: list[Any]
    expected: Any


@dataclass(frozen=True)
class Kata:
    id: str
    title: str
    prompt: str
    function_name: str
    function_signature: str
    test_cases: list[TestCase]
    category: str
    difficulty: str


# ---------------------------------------------------------------------------
# P4 — Sandbox
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    timed_out: bool


# ---------------------------------------------------------------------------
# P5 — Verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailedCase:
    input: list[Any]
    expected: Any
    actual: Any
    error: str | None = None


@dataclass(frozen=True)
class VerificationReport:
    oracle_passed: bool
    score: float
    failed_cases: list[FailedCase]
    advisory_failures: list[str] = field(default_factory=list)
    exec_result: ExecResult | None = None

    @property
    def passed(self) -> bool:
        # Oracle precedence over everything (Decision #5): advisory failures
        # can never flip a run the oracle passed.
        return self.oracle_passed


# ---------------------------------------------------------------------------
# P3 — Tools
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    success: bool
    output: Any
    error: str | None = None


# ---------------------------------------------------------------------------
# P6 — Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class AttemptRecord:
    attempt_no: int
    code: str
    completion: Completion
    report: VerificationReport


@dataclass
class RunContext:
    kata: Kata
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def attempt_no(self) -> int:
        return len(self.attempts) + 1


@dataclass
class RunResult:
    run_id: str
    kata_id: str
    passed: bool
    attempts: list[AttemptRecord]
    total_duration_ms: float
