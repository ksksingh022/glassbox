"""Loads Kata definitions from `harness/verification/katas/*.json`."""
from __future__ import annotations

import json
from pathlib import Path

from harness.models import Kata, TestCase

_KATAS_DIR = Path(__file__).parent / "katas"


def _parse(data: dict) -> Kata:
    return Kata(
        id=data["id"],
        title=data["title"],
        prompt=data["prompt"],
        function_name=data["function_name"],
        function_signature=data["function_signature"],
        # Hand-written cases are ground truth, same authority tier as
        # LeetCode's own published examples (see the authority ladder in
        # DECISIONS.md) — they set pass/fail outright.
        test_cases=[
            TestCase(input=tc["input"], expected=tc["expected"], source="curated")
            for tc in data["test_cases"]
        ],
        category=data["category"],
        difficulty=data["difficulty"],
        kind=data.get("kind", "function"),
        constraints=data.get("constraints", []),
        max_n=data.get("max_n"),
        topics=data.get("topics", []),
        class_name=data.get("class_name", ""),
        source_ref="curated",
    )


def load_all() -> dict[str, Kata]:
    katas = {}
    for path in sorted(_KATAS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        kata = _parse(data)
        katas[kata.id] = kata
    return katas


def load(kata_id: str) -> Kata:
    katas = load_all()
    if kata_id not in katas:
        raise KeyError(f"unknown kata_id: {kata_id}")
    return katas[kata_id]
