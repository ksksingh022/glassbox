"""Unit tests for the four new tools beyond run_tests/run_code."""
from harness.sandbox.executor import SubprocessSandbox
from harness.tools.algorithm_hint import AlgorithmHintTool
from harness.tools.complexity_analysis import AnalyzeComplexityTool
from harness.tools.docs_lookup import DocsLookupTool
from harness.tools.trace_execution import TraceExecutionTool
from harness.tracing.tracer import Tracer


def test_lookup_docs_returns_signature_and_doc():
    tool = DocsLookupTool()
    result = tool.run(symbol="bisect.bisect_left")
    assert result.success is True
    assert "a" in result.output["signature"] or "(" in result.output["signature"]
    assert result.output["doc"]


def test_lookup_docs_rejects_disallowed_module():
    tool = DocsLookupTool()
    result = tool.run(symbol="os.system")
    assert result.success is False
    assert "not in the allowed" in result.error


def test_lookup_docs_handles_unknown_attribute_gracefully():
    tool = DocsLookupTool()
    result = tool.run(symbol="math.not_a_real_function")
    assert result.success is False
    assert result.error


def test_algorithm_hint_known_category():
    tool = AlgorithmHintTool()
    result = tool.run(category="algorithms", difficulty="hard")
    assert result.success is True
    assert "binary search" in result.output["hint"] or "DP" in result.output["hint"]
    assert "boundary" in result.output["hint"]  # difficulty note appended


def test_algorithm_hint_unknown_category():
    tool = AlgorithmHintTool()
    result = tool.run(category="quantum-computing")
    assert result.success is False


def test_analyze_complexity_detects_nested_loops():
    tool = AnalyzeComplexityTool()
    code = "def f(a):\n    for i in a:\n        for j in a:\n            print(i, j)\n"
    result = tool.run(code=code)
    assert result.success is True
    assert result.output["max_loop_nesting_depth"] == 2
    assert "O(n^2)" in result.output["complexity_guess"] or "worse" in result.output["complexity_guess"]


def test_analyze_complexity_detects_unmemoized_recursion():
    tool = AnalyzeComplexityTool()
    code = "def fib(n):\n    if n < 2:\n        return n\n    return fib(n - 1) + fib(n - 2)\n"
    result = tool.run(code=code)
    assert result.success is True
    assert result.output["recursive_functions"] == ["fib"]
    assert result.output["memoization_detected"] is False
    assert "exponential" in result.output["complexity_guess"]


def test_analyze_complexity_detects_memoized_recursion():
    tool = AnalyzeComplexityTool()
    code = (
        "from functools import lru_cache\n"
        "@lru_cache\n"
        "def fib(n):\n"
        "    if n < 2:\n        return n\n"
        "    return fib(n - 1) + fib(n - 2)\n"
    )
    result = tool.run(code=code)
    assert result.output["memoization_detected"] is True
    assert "polynomial" in result.output["complexity_guess"]


def test_analyze_complexity_rejects_syntax_error():
    tool = AnalyzeComplexityTool()
    result = tool.run(code="def f(:\n  pass")
    assert result.success is False


def test_trace_execution_reports_line_by_line_locals():
    tracer = Tracer(run_id="test-trace")
    tool = TraceExecutionTool(sandbox=SubprocessSandbox(), tracer=tracer)
    code = "def add_one(x):\n    y = x + 1\n    return y\n"
    result = tool.run(code=code, call="add_one(4)")
    assert result.success is True
    assert result.output["result"] == "5"
    assert len(result.output["steps"]) >= 2
    assert any("y" in step["locals"] for step in result.output["steps"])


def test_trace_execution_reports_exception_with_partial_trace():
    tracer = Tracer(run_id="test-trace-err")
    tool = TraceExecutionTool(sandbox=SubprocessSandbox(), tracer=tracer)
    code = "def boom(x):\n    y = 1 / x\n    return y\n"
    result = tool.run(code=code, call="boom(0)")
    assert result.success is False
    assert "ZeroDivisionError" in result.output["error"]
    assert len(result.output["steps"]) >= 1
