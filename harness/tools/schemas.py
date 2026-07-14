"""JSON-Schema descriptions of the tools offered to the model, provider-
agnostic (`ToolSchema`) so any function-calling-capable provider can use
them without the Orchestrator caring about wire format.

Six tools, each genuinely different in kind — not six variations on "run
the code":
  - run_tests           correctness oracle (necessary — this is how an
                         attempt is actually judged)
  - run_code             scratch execution (arbitrary snippet, stdout/stderr)
  - trace_execution      dynamic debugging (line-by-line variable trace for
                         one specific call, sandboxed)
  - lookup_docs           knowledge retrieval (offline stdlib introspection)
  - analyze_complexity    static analysis (AST-only, no execution, Big-O guess)
  - algorithm_hint        strategy knowledge (curated technique hint by category)
"""
from __future__ import annotations

from harness.models import ToolSchema

RUN_TESTS_SCHEMA = ToolSchema(
    name="run_tests",
    description=(
        "Run your current candidate implementation against the kata's test "
        "oracle. Returns whether it passed and, if not, which cases failed "
        "with expected vs. actual values. Call this to check your work "
        "before giving your final answer."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The full function implementation to test."},
        },
        "required": ["code"],
    },
)

RUN_CODE_SCHEMA = ToolSchema(
    name="run_code",
    description=(
        "Execute an arbitrary Python snippet in the sandbox (e.g. to print "
        "and check something) and get back stdout/stderr."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python code to execute."},
        },
        "required": ["code"],
    },
)

TRACE_EXECUTION_SCHEMA = ToolSchema(
    name="trace_execution",
    description=(
        "Run one specific call to your code with a line-by-line trace of "
        "local variables at each step — a real debugger step-through, more "
        "detailed than run_code. Use this to find exactly where an off-by-one "
        "or boundary-case bug happens, not just whether it happens."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The full function implementation to trace."},
            "call": {"type": "string", "description": "A Python expression invoking the function, e.g. \"is_match('aa', 'a*')\"."},
        },
        "required": ["code", "call"],
    },
)

LOOKUP_DOCS_SCHEMA = ToolSchema(
    name="lookup_docs",
    description=(
        "Look up the signature and docstring of a Python standard library "
        "function or class (offline — no network). E.g. symbol="
        "\"bisect.bisect_left\" or \"itertools.groupby\"."
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Dotted stdlib symbol, e.g. 'bisect.bisect_left'."},
        },
        "required": ["symbol"],
    },
)

ANALYZE_COMPLEXITY_SCHEMA = ToolSchema(
    name="analyze_complexity",
    description=(
        "Statically analyze code (no execution — safe even on code that "
        "might infinite-loop) for loop nesting depth and recursion, with a "
        "rough Big-O guess. Use this to sanity-check efficiency before "
        "committing to an approach, independent of correctness."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The code to analyze."},
        },
        "required": ["code"],
    },
)

ALGORITHM_HINT_SCHEMA = ToolSchema(
    name="algorithm_hint",
    description=(
        "Get a short strategic hint (which technique usually applies — "
        "e.g. two pointers, DP, stack) for a kata's category. Does not "
        "reveal the solution itself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "The kata's category, e.g. 'algorithms', 'data-structures', 'sorting'."},
            "difficulty": {"type": "string", "description": "The kata's difficulty, e.g. 'easy', 'medium', 'hard'."},
        },
        "required": ["category"],
    },
)
