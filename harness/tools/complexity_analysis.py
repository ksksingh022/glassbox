"""P3 — `analyze_complexity` tool: static structural analysis, no execution.

Parses the submission with `ast` (never `exec`/`eval`) and reports loop
nesting depth and recursion, with a rough heuristic Big-O guess. This is
deliberately static and cheap — a different kind of insight than
`trace_execution` (which runs the code) or `run_tests` (which checks
correctness): this one answers "how does this scale?" without running
anything, so it's safe to call even on code that might infinite-loop.
"""
from __future__ import annotations

import ast

from harness.models import ToolResult
from harness.tools.base import Tool

_MEMO_MARKERS = ("lru_cache", "cache(", "memo", "@cache")


class _LoopDepthVisitor(ast.NodeVisitor):
    def __init__(self):
        self.max_depth = 0
        self.loop_count = 0
        self._depth = 0

    def _visit_loop(self, node):
        self.loop_count += 1
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        self.generic_visit(node)
        self._depth -= 1

    def visit_For(self, node):
        self._visit_loop(node)

    def visit_While(self, node):
        self._visit_loop(node)


class _RecursionVisitor(ast.NodeVisitor):
    """Reports whether any function in the module calls itself by name."""

    def __init__(self):
        self.recursive_functions: list[str] = []

    def visit_FunctionDef(self, node):
        name = node.name
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == name:
                self.recursive_functions.append(name)
                break
        self.generic_visit(node)


def _guess_complexity(max_depth: int, is_recursive: bool, has_memo: bool) -> str:
    if is_recursive and not has_memo:
        return "recursive without an obvious memo/cache — verify it isn't exponential on overlapping subproblems"
    if is_recursive and has_memo:
        return "recursive with memoization — likely polynomial, bounded by the size of the memoized state space"
    if max_depth >= 2:
        return f"loops nested {max_depth} deep — likely O(n^{max_depth}) or worse"
    if max_depth == 1:
        return "a single loop level — likely O(n)"
    return "no loops or recursion detected — likely O(1) or a single built-in operation"


class AnalyzeComplexityTool(Tool):
    name = "analyze_complexity"
    description = "Statically analyze code (no execution) for loop nesting depth and recursion, with a rough Big-O guess."

    def run(self, **kwargs) -> ToolResult:
        code: str = kwargs.get("code", "")
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return ToolResult(success=False, output=None, error=f"syntax error: {exc}")

        loops = _LoopDepthVisitor()
        loops.visit(tree)

        recursion = _RecursionVisitor()
        recursion.visit(tree)
        is_recursive = bool(recursion.recursive_functions)
        has_memo = any(marker in code for marker in _MEMO_MARKERS)

        return ToolResult(
            success=True,
            output={
                "max_loop_nesting_depth": loops.max_depth,
                "loop_count": loops.loop_count,
                "recursive_functions": recursion.recursive_functions,
                "memoization_detected": has_memo,
                "complexity_guess": _guess_complexity(loops.max_depth, is_recursive, has_memo),
            },
        )
