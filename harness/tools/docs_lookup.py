"""P3 — `lookup_docs` tool: offline Python stdlib signature/docstring lookup.

This is the scoped, safe stand-in for the web-search tool Decision #4 cut
("weakly motivated for katas; a decorative tool undermines the
everything-has-a-purpose premise"). A general web-search tool is open-ended
and network-dependent; a stdlib-only introspection tool answers a real,
narrow question ("what does bisect.bisect_left do?") entirely offline, by
inspecting modules already installed — no network call, nothing external to
trust.
"""
from __future__ import annotations

import importlib
import inspect

from harness.models import ToolResult
from harness.tools.base import Tool

# Deliberately narrow: only modules genuinely useful for algorithmic kata
# code, and all safe to introspect (no I/O, no subprocess, no os/sys).
ALLOWED_MODULES = frozenset({
    "math", "itertools", "functools", "collections", "bisect", "heapq",
    "re", "string", "statistics", "operator", "typing", "array", "copy",
})

_MAX_DOC_CHARS = 1200


class DocsLookupTool(Tool):
    name = "lookup_docs"
    description = "Look up the signature and docstring of a Python standard library function or class."

    def run(self, **kwargs) -> ToolResult:
        symbol: str = kwargs.get("symbol", "").strip()
        if not symbol:
            return ToolResult(success=False, output=None, error="symbol is required")

        module_name, _, attr_path = symbol.partition(".")
        if module_name not in ALLOWED_MODULES:
            return ToolResult(
                success=False, output=None,
                error=f"'{module_name}' is not in the allowed offline docs set: {sorted(ALLOWED_MODULES)}",
            )

        try:
            obj = importlib.import_module(module_name)
            for part in attr_path.split(".") if attr_path else []:
                obj = getattr(obj, part)
        except (ImportError, AttributeError) as exc:
            return ToolResult(success=False, output=None, error=f"could not resolve '{symbol}': {exc}")

        try:
            signature = str(inspect.signature(obj))
        except (TypeError, ValueError):
            signature = ""
        doc = (inspect.getdoc(obj) or "").strip()
        if len(doc) > _MAX_DOC_CHARS:
            doc = doc[:_MAX_DOC_CHARS] + "\n... [truncated]"

        return ToolResult(
            success=True,
            output={"symbol": symbol, "signature": signature, "doc": doc or "(no docstring)"},
        )
