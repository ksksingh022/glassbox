"""Deterministic provider for demos and tests when no LLM API key is set.

Not a mock of the interface — a genuine `LLMProvider` implementation with a
canned "policy": given a kata id and attempt number (read off tags the
`InstructionBuilder` embeds in the prompt), it returns a scripted solution.
Some katas are scripted to fail on attempt 1 and pass on attempt 2, so the
retry story (plan §3.5) is demonstrable without live network access.
"""
from __future__ import annotations

import json
import os
import re
import time

from harness.models import Completion, Message
from harness.providers.base import LLMProvider

# kata_id -> list of code versions tried in order across attempts.
# The last version is repeated if max_attempts exceeds the list length.
_SOLUTIONS: dict[str, list[str]] = {
    "reverse_string": [
        "def reverse_string(s: str) -> str:\n    return s[::-1]\n",
    ],
    "fizzbuzz_variant": [
        "def fizzbuzz_variant(n: int) -> str:\n"
        "    out = ''\n"
        "    if n % 3 == 0:\n        out += 'Fizz'\n"
        "    if n % 5 == 0:\n        out += 'Buzz'\n"
        "    if n % 7 == 0:\n        out += 'Bang'\n"
        "    return out or str(n)\n",
    ],
    "binary_search": [
        # Buggy: exclusive-looking `high` but loop uses `<`, misses the
        # last element — a classic off-by-one, guaranteed to fail once.
        "def binary_search(arr, target):\n"
        "    low, high = 0, len(arr) - 1\n"
        "    while low < high:\n"
        "        mid = (low + high) // 2\n"
        "        if arr[mid] == target:\n            return mid\n"
        "        elif arr[mid] < target:\n            low = mid + 1\n"
        "        else:\n            high = mid - 1\n"
        "    return -1\n",
        # Fixed: `<=` so single-element ranges are still checked.
        "def binary_search(arr, target):\n"
        "    low, high = 0, len(arr) - 1\n"
        "    while low <= high:\n"
        "        mid = (low + high) // 2\n"
        "        if arr[mid] == target:\n            return mid\n"
        "        elif arr[mid] < target:\n            low = mid + 1\n"
        "        else:\n            high = mid - 1\n"
        "    return -1\n",
    ],
    "balanced_brackets": [
        "def is_balanced(s: str) -> bool:\n"
        "    pairs = {')': '(', ']': '[', '}': '{'}\n"
        "    stack = []\n"
        "    for ch in s:\n"
        "        if ch in '([{':\n            stack.append(ch)\n"
        "        elif ch in pairs:\n"
        "            if not stack or stack.pop() != pairs[ch]:\n                return False\n"
        "    return not stack\n",
    ],
    "merge_intervals": [
        "def merge_intervals(intervals):\n"
        "    if not intervals:\n        return []\n"
        "    ordered = sorted(intervals, key=lambda p: p[0])\n"
        "    merged = [list(ordered[0])]\n"
        "    for start, end in ordered[1:]:\n"
        "        if start <= merged[-1][1]:\n"
        "            merged[-1][1] = max(merged[-1][1], end)\n"
        "        else:\n"
        "            merged.append([start, end])\n"
        "    return merged\n",
    ],
    "regex_matching": [
        "def is_match(s: str, p: str) -> bool:\n"
        "    memo = {}\n"
        "    def dp(i, j):\n"
        "        if (i, j) in memo:\n            return memo[(i, j)]\n"
        "        if j == len(p):\n            ans = i == len(s)\n"
        "        else:\n"
        "            first = i < len(s) and p[j] in (s[i], '.')\n"
        "            if j + 1 < len(p) and p[j + 1] == '*':\n"
        "                ans = dp(i, j + 2) or (first and dp(i + 1, j))\n"
        "            else:\n"
        "                ans = first and dp(i + 1, j + 1)\n"
        "        memo[(i, j)] = ans\n"
        "        return ans\n"
        "    return dp(0, 0)\n",
    ],
    "regex_matching_variant": [
        "def is_match(s: str, p: str) -> bool:\n"
        "    memo = {}\n"
        "    def dp(i, j):\n"
        "        if (i, j) in memo:\n            return memo[(i, j)]\n"
        "        if j == len(p):\n            ans = i == len(s)\n"
        "        else:\n"
        "            first = i < len(s) and (p[j] == s[i] or p[j] == '.')\n"
        "            if j + 1 < len(p) and p[j + 1] == '*':\n"
        "                ans = dp(i, j + 2) or (first and dp(i + 1, j))\n"
        "            elif j + 1 < len(p) and p[j + 1] == '+':\n"
        "                ans = first and (dp(i + 1, j) or dp(i + 1, j + 2))\n"
        "            else:\n"
        "                ans = first and dp(i + 1, j + 1)\n"
        "        memo[(i, j)] = ans\n"
        "        return ans\n"
        "    return dp(0, 0)\n",
    ],
}

# Deliberately generic, kata-agnostic edge-case inputs a "tester" would
# propose — enough to demonstrate the advisory-failure path (Decision #5)
# without depending on any one kata's internals. `binary_search`'s scripted
# fixed solution handles all of these fine, so this never flips a run's
# pass/fail — only ever produces advisory text, exactly as designed.
_EDGE_CASES: dict[str, list[list]] = {
    "binary_search": [[[], 1], [[5], 5], [[1, 2, 3], 100]],
    "reverse_string": [[""], ["a"]],
    "balanced_brackets": [[""], ["((("]],
    "merge_intervals": [[[]], [[[1, 1]]]],
}

_KATA_TAG = re.compile(r"KATA_ID:\s*(\S+)")
_ATTEMPT_TAG = re.compile(r"ATTEMPT:\s*(\d+)")
_ROLE_TAG = re.compile(r"ROLE:\s*(\S+)")

_PLANNER_PLAN = (
    "1. Restate the function signature and what a correct return value looks like.\n"
    "2. Identify edge cases (empty input, single element, boundary values).\n"
    "3. Implement the core logic.\n"
    "4. Mentally trace through 2-3 examples before finalizing.\n"
)


class FakeProvider(LLMProvider):
    """No network calls. Reads `KATA_ID:`/`ATTEMPT:`/`ROLE:` tags that
    `InstructionBuilder` (Phase 1) or the Planner/Coder/Tester subagents
    (Phase 2) embed in the system message, and returns a scripted response
    matching that role — a kata-agnostic plan for `planner`, the same
    scripted fail-then-pass code for `coder` (or untagged Phase 1 calls),
    and a small JSON edge-case list for `tester`."""

    def complete(self, messages: list[Message], **kwargs) -> Completion:
        start = time.monotonic()
        blob = "\n".join(m.content for m in messages)
        kata_match = _KATA_TAG.search(blob)
        attempt_match = _ATTEMPT_TAG.search(blob)
        role_match = _ROLE_TAG.search(blob)
        kata_id = kata_match.group(1) if kata_match else ""
        attempt_no = int(attempt_match.group(1)) if attempt_match else 1
        role = role_match.group(1) if role_match else "coder"

        if role == "planner":
            latency_ms = (time.monotonic() - start) * 1000
            return Completion(
                text=_PLANNER_PLAN, model_name="fake/scripted-v1",
                input_tokens=len(blob.split()), output_tokens=len(_PLANNER_PLAN.split()),
                latency_ms=max(latency_ms, 1.0),
            )

        if role == "tester":
            edge_cases = _EDGE_CASES.get(kata_id, [])
            text = json.dumps(edge_cases)
            latency_ms = (time.monotonic() - start) * 1000
            return Completion(
                text=text, model_name="fake/scripted-v1",
                input_tokens=len(blob.split()), output_tokens=len(text.split()),
                latency_ms=max(latency_ms, 1.0),
            )

        versions = _SOLUTIONS.get(kata_id, ["def solution():\n    pass\n"])
        code = versions[min(attempt_no - 1, len(versions) - 1)]
        text = f"```python\n{code}```"

        # Demo aid only: when GLASSBOX_SIMULATE_SAFETY is set, pretend the
        # first attempt got routed to the safety model and re-routed once,
        # so the safety-retry visual (item 2) is watchable without a live
        # OpenRouter key. Never affects pass/fail or attempt count.
        safety_retries = 1 if (os.environ.get("GLASSBOX_SIMULATE_SAFETY") and attempt_no == 1) else 0

        latency_ms = (time.monotonic() - start) * 1000
        return Completion(
            text=text,
            model_name="fake/scripted-v1",
            input_tokens=len(blob.split()),
            output_tokens=len(code.split()),
            latency_ms=max(latency_ms, 1.0),
            safety_retries=safety_retries,
        )
