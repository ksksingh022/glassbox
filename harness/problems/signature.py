"""Parse LeetCode's python3 starter snippet into an entry point.

This is deliberately **deterministic** rather than asking the LLM. LeetCode
hands us the exact signature it expects, so parsing it is transcription, not
judgment — and getting the entry point wrong breaks every downstream step.
The LLM is only asked for the parts that genuinely need interpretation
(test-case values), and only as a fallback when there's no starter code at
all (pasted problems).

The convention LeetCode uses:
  * `class Solution:` with one method  -> a "function" problem; that method
    is the entry point.
  * any other class name               -> a "design" problem; the class is
    instantiated and its methods replayed as an operation sequence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_CLASS = re.compile(r"^\s*class\s+(\w+)", re.MULTILINE)
# Bodies in LeetCode snippets are empty, so this must not rely on `ast`
# (an empty `def f():` with no body is a SyntaxError).
_METHOD = re.compile(r"^\s+def\s+(\w+)\s*\((.*?)\)\s*(->\s*[^:\n]+?)?\s*:", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class EntryPoint:
    kind: str                       # "function" | "design"
    function_name: str = ""
    class_name: str = ""
    signature: str = ""
    methods: list[str] = field(default_factory=list)


def parse_starter_code(code: str) -> EntryPoint | None:
    """None when the snippet can't be understood — caller falls back to LLM."""
    if not code or not code.strip():
        return None

    class_match = _CLASS.search(code)
    methods = [
        (name, args.strip(), (ret or "").strip())
        for name, args, ret in _METHOD.findall(code)
    ]
    if not methods:
        return None

    if class_match:
        class_name = class_match.group(1)
        if class_name == "Solution":
            # Function-style: the first non-dunder method is the entry point.
            for name, args, ret in methods:
                if name.startswith("__"):
                    continue
                return EntryPoint(
                    kind="function",
                    function_name=name,
                    signature=f"def {name}({args}){' ' + ret if ret else ''}:",
                    methods=[name],
                )
            return None
        # Design-style: the whole class is the artifact.
        return EntryPoint(
            kind="design",
            class_name=class_name,
            signature=code.strip(),
            methods=[n for n, _a, _r in methods],
        )

    # A bare function snippet (no class) — some non-LeetCode sources look
    # like this, and so do our curated katas.
    name, args, ret = methods[0]
    return EntryPoint(
        kind="function",
        function_name=name,
        signature=f"def {name}({args}){' ' + ret if ret else ''}:",
        methods=[name],
    )


_BARE_DEF = re.compile(r"^\s*def\s+(\w+)\s*\((.*?)\)\s*(->\s*[^:\n]+?)?\s*:", re.MULTILINE | re.DOTALL)


def parse_bare_signature(sig: str) -> EntryPoint | None:
    """For curated katas, whose `function_signature` is a top-level def."""
    match = _BARE_DEF.search(sig or "")
    if not match:
        return None
    name, args, ret = match.group(1), match.group(2).strip(), (match.group(3) or "").strip()
    return EntryPoint(
        kind="function",
        function_name=name,
        signature=f"def {name}({args}){' ' + ret if ret else ''}:",
        methods=[name],
    )


def strip_self(signature: str) -> str:
    """LeetCode methods take `self`; the oracle calls a free function, so the
    signature shown to the model drops it."""
    return re.sub(r"\(\s*self\s*,\s*", "(", re.sub(r"\(\s*self\s*\)", "()", signature))
