"""Deterministic pre-parse of a statement's worked examples.

LeetCode publishes the ground-truth answer for each example right in the
statement, so these are the *only* test cases with verified expected values —
tier 1 of the authority ladder (see DECISIONS.md). Parsing them mechanically
first means the Extractor LLM is doing repair and type-normalization on a
concrete draft, not free-form transcription, which is a much smaller job with
much less room to hallucinate an oracle.

Two published shapes:

  function style
      Input: nums = [2,7,11,15], target = 9
      Output: [0,1]

  design style
      Input
      ["MedianFinder","addNum","findMedian"]
      [[],[1],[]]
      Output
      [null,null,1.0]
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any

_EXAMPLE_SPLIT = re.compile(r"^\s*Example\s*\d*\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
_INLINE_INPUT = re.compile(r"^\s*Input\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_INLINE_OUTPUT = re.compile(r"^\s*Output\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_BLOCK_INPUT = re.compile(r"^\s*Input\s*$", re.IGNORECASE | re.MULTILINE)
_BLOCK_OUTPUT = re.compile(r"^\s*Output\s*$", re.IGNORECASE | re.MULTILINE)
_EXPLANATION = re.compile(r"^\s*Explanation\s*:?", re.IGNORECASE | re.MULTILINE)


@dataclass
class ParsedExample:
    kind: str                                    # "function" | "design"
    named_args: dict[str, Any] = field(default_factory=dict)   # function style
    ops: list[str] = field(default_factory=list)               # design style
    op_args: list[list] = field(default_factory=list)          # design style
    expected: Any = None
    raw: str = ""


def parse_literal(text: str) -> Any:
    """LeetCode literals are JSON-ish but occasionally Python-ish."""
    text = text.strip().rstrip(",").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        # `null`/`true`/`false` mixed into an otherwise Python literal.
        patched = re.sub(r"\bnull\b", "None", text)
        patched = re.sub(r"\btrue\b", "True", patched)
        patched = re.sub(r"\bfalse\b", "False", patched)
        try:
            return ast.literal_eval(patched)
        except (ValueError, SyntaxError):
            return text  # give the LLM the raw string to repair


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split on `sep` only at bracket/quote depth zero, so
    `nums = [1,2], target = 9` yields two parts, not four."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    current: list[str] = []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
            continue
        if ch in "[({":
            depth += 1
        elif ch in "])}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_named_args(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for part in split_top_level(text):
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name.isidentifier():
            out[name] = parse_literal(value)
    return out


def _parse_one(block: str) -> ParsedExample | None:
    # Design style first: bare `Input` / `Output` lines with arrays beneath.
    in_block = _BLOCK_INPUT.search(block)
    out_block = _BLOCK_OUTPUT.search(block)
    if in_block and out_block and out_block.start() > in_block.end():
        input_body = block[in_block.end():out_block.start()].strip()
        after = block[out_block.end():]
        stop = _EXPLANATION.search(after)
        output_body = (after[: stop.start()] if stop else after).strip()

        lines = [ln.strip() for ln in input_body.splitlines() if ln.strip()]
        if len(lines) >= 2:
            ops = parse_literal(lines[0])
            op_args = parse_literal(lines[1])
            expected = parse_literal(output_body.splitlines()[0] if output_body else "")
            if isinstance(ops, list) and isinstance(op_args, list):
                return ParsedExample(
                    kind="design", ops=[str(o) for o in ops], op_args=op_args,
                    expected=expected, raw=block.strip(),
                )

    # Function style: `Input: a = 1, b = 2` / `Output: 3`
    in_line = _INLINE_INPUT.search(block)
    out_line = _INLINE_OUTPUT.search(block)
    if in_line and out_line:
        named = _parse_named_args(in_line.group(1))
        expected = parse_literal(out_line.group(1))
        if named:
            return ParsedExample(
                kind="function", named_args=named, expected=expected, raw=block.strip(),
            )
    return None


def parse_examples(statement_text: str) -> list[ParsedExample]:
    """Best-effort. Returning fewer (or none) is fine — the Extractor LLM
    repairs and fills gaps; this just anchors it to the published values."""
    if not statement_text:
        return []
    blocks = _EXAMPLE_SPLIT.split(statement_text)[1:]  # drop the preamble
    out = []
    for block in blocks:
        parsed = _parse_one(block)
        if parsed:
            out.append(parsed)
    return out


def to_positional(example: ParsedExample, param_names: list[str]) -> list[Any] | None:
    """Named example args -> positional list, ordered by the signature.
    None when a declared parameter has no matching example value (better to
    hand the whole thing to the LLM than to silently mis-order arguments)."""
    if example.kind != "function":
        return None
    if not param_names:
        return list(example.named_args.values())
    args = []
    for name in param_names:
        if name not in example.named_args:
            return None
        args.append(example.named_args[name])
    return args


_PARAM = re.compile(r"def\s+\w+\s*\((.*?)\)\s*(?:->.*)?:", re.DOTALL)


def param_names_from_signature(signature: str) -> list[str]:
    match = _PARAM.search(signature or "")
    if not match:
        return []
    names = []
    for part in split_top_level(match.group(1)):
        name = part.split(":")[0].split("=")[0].strip()
        if name and name != "self" and name.isidentifier():
            names.append(name)
    return names
