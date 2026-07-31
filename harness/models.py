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
# Problem definition (verification input)
#
# Phase 4 generalized the old fixed-kata `Kata` into `Problem`, which can also
# describe something fetched live from LeetCode. Every field the original
# 7 curated katas didn't have carries a default, so their JSON files, the
# loader, and every existing test keep working untouched.
# ---------------------------------------------------------------------------

# "function" — implement one free function; the oracle calls fn(*args).
# "design"   — implement a class; the oracle replays an operation sequence
#              (LeetCode's ["MedianFinder","addNum"], [[],[1]] shape).
ProblemKind = Literal["function", "design"]

# Where a test case came from — drives the authority ladder in the Tester
# (see DECISIONS.md): "official"/"curated" are ground truth and set pass/fail,
# "generated" cases are proposed by the test-gen agent and can only fail a run
# via a judge verdict, never on their own guessed expected value.
CaseSource = Literal["official", "generated", "curated"]


@dataclass(frozen=True)
class TestCase:
    input: list[Any]
    expected: Any
    source: CaseSource = "curated"
    # Why the generator produced this case ("empty input", "max n boundary").
    # Empty for official/curated cases, which need no justification.
    rationale: str = ""


@dataclass(frozen=True)
class Problem:
    id: str
    title: str
    prompt: str
    function_name: str
    function_signature: str
    test_cases: list[TestCase]
    category: str
    difficulty: str

    kind: ProblemKind = "function"
    # Raw fetched statement (LeetCode returns HTML) — rendered in the UI so a
    # viewer sees the actual question, not just our extraction of it.
    statement_html: str = ""
    constraints: list[str] = field(default_factory=list)
    # Largest input bound found in `constraints`, e.g. 100000 for "n <= 10^5".
    # Drives ComplexityBudget; None when no bound could be determined.
    max_n: int | None = None
    topics: list[str] = field(default_factory=list)
    starter_code: str = ""
    source_ref: str = "curated"
    # Design problems only: the class the solution must define.
    class_name: str = ""

    @property
    def official_cases(self) -> list[TestCase]:
        """Ground-truth cases — the only ones that set pass/fail."""
        return [tc for tc in self.test_cases if tc.source in ("official", "curated")]

    @property
    def generated_cases(self) -> list[TestCase]:
        return [tc for tc in self.test_cases if tc.source == "generated"]


# The harness spoke in terms of `Kata` for Phases 1-3 and a lot of code and
# tests still do; `Problem` is a strict superset, so this alias keeps all of
# it working rather than forcing a mechanical rename with no behavior change.
Kata = Problem


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
class GeneratedFailure:
    """A generated (non-ground-truth) case the code disagreed with. Carries
    the judge's ruling once adjudicated — until then `verdict` is None."""
    input: list[Any]
    expected: Any
    actual: Any
    rationale: str = ""
    error: str | None = None
    verdict: "JudgeVerdictKind | None" = None
    verdict_reason: str = ""


@dataclass(frozen=True)
class VerificationReport:
    oracle_passed: bool
    score: float
    failed_cases: list[FailedCase]
    advisory_failures: list[str] = field(default_factory=list)
    exec_result: ExecResult | None = None
    # Generated-case disagreements (tier 3) with their judge verdicts.
    generated_failures: list[GeneratedFailure] = field(default_factory=list)

    @property
    def judged_real_bugs(self) -> list[GeneratedFailure]:
        return [gf for gf in self.generated_failures if gf.verdict == "real_bug"]

    @property
    def passed(self) -> bool:
        # Oracle precedence (Decision #5) is preserved but now two-tiered:
        # ground-truth cases must pass, AND no generated case may have been
        # judged a real bug. A generated case can never fail a run on its own
        # guessed expected value — only via an explicit `real_bug` verdict.
        return self.oracle_passed and not self.judged_real_bugs


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


# ---------------------------------------------------------------------------
# P12 — Subagents
# ---------------------------------------------------------------------------

SubagentRole = Literal["planner", "coder", "tester", "extractor", "testgen", "judge"]


@dataclass(frozen=True)
class SubagentTask:
    """Input to a `Subagent.run()` call. Every field a role doesn't need is
    just left at its default — a single shared shape is simpler than one
    dataclass per role, and the Orchestrator building it stays uniform."""
    kata: Kata
    attempt_no: int = 1
    plan_steps: list[str] = field(default_factory=list)
    previous_plan: list[str] = field(default_factory=list)  # set only on escalation
    code: str = ""
    failure_feedback: str | None = None
    skill_context: list[str] = field(default_factory=list)
    memory_hint: str | None = None
    # Phase 4: the Coder is told what Big-O the constraints actually allow.
    complexity_budget: "ComplexityBudget | None" = None


@dataclass(frozen=True)
class SubagentResult:
    """Output of a `Subagent.run()` call — DISTILLED, never a raw
    transcript (the parent orchestrator's context must stay small no
    matter how many tool calls or tokens a subagent burned internally).
    `completion` is the Coder's single final answer (already just text +
    metadata, not a transcript) — kept so the parent can build an
    `AttemptRecord` without re-deriving it."""
    role: SubagentRole
    plan_steps: list[str] = field(default_factory=list)
    code: str = ""
    tools_used: list[str] = field(default_factory=list)
    completion: "Completion | None" = None
    report: "VerificationReport | None" = None
    advisory_failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# P13 — Skills
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: list[str]
    content: str


# ---------------------------------------------------------------------------
# P15 — Problem fetching (Phase 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawProblem:
    """What a `ProblemSource` returns: the problem as published, before the
    Extractor turns it into a runnable `Problem`. Deliberately unstructured —
    fetching and understanding are separate steps so a fetch failure and an
    extraction failure are distinguishable in the trace."""
    ref: str                       # what the user typed ("leetcode 295")
    title: str
    statement_html: str            # as published (LeetCode returns HTML)
    statement_text: str            # tag-stripped, for prompting
    difficulty: str = "unknown"
    topics: list[str] = field(default_factory=list)
    starter_code: str = ""         # LeetCode's python3 snippet, if any
    example_testcases: str = ""    # LeetCode's raw exampleTestcases blob
    source: str = "unknown"        # "leetcode" | "pasted" | "curated"
    url: str = ""


# ---------------------------------------------------------------------------
# P17 — Complexity budget (Phase 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComplexityBudget:
    """What the constraints actually permit. Derived deterministically from
    the largest input bound — no LLM — and handed to the Coder so it aims at
    the right algorithm class instead of writing an O(n^2) solution for
    n = 10^5 and timing out."""
    max_n: int | None
    acceptable: str                # "O(n log n)"
    also_acceptable: list[str] = field(default_factory=list)
    too_slow: list[str] = field(default_factory=list)
    reasoning: str = ""

    def as_prompt_line(self) -> str:
        if self.max_n is None:
            return (
                "No explicit input bound was found in the constraints — prefer the "
                "asymptotically best solution you can reasonably write."
            )
        return (
            f"Input size can reach n = {self.max_n:,}. Target {self.acceptable} or better; "
            f"{', '.join(self.too_slow)} will be too slow."
        )


# ---------------------------------------------------------------------------
# P19 — Judge (Phase 4)
# ---------------------------------------------------------------------------

# Deliberately 3-way. The judge is an LLM and is *not* asked to compute ground
# truth (that's just re-solving the problem, and it'd be wrong exactly on the
# hard problems that matter). It only adjudicates a concrete disagreement
# between the code's output and a *generated* case's guessed expected value:
#   bad_test  -> the generated case was wrong; discard it (protects correct code)
#   real_bug  -> the code is genuinely wrong; promote to a hard failure + retry
#   uncertain -> can't tell; surface as advisory feedback only
JudgeVerdictKind = Literal["bad_test", "real_bug", "uncertain"]


@dataclass(frozen=True)
class JudgeVerdict:
    case_index: int
    verdict: JudgeVerdictKind
    reason: str
