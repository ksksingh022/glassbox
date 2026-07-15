---
name: python-idioms
description: Idiomatic Python patterns that keep kata solutions short and correct
triggers: warmup, conditionals
---

# Python idiom notes

- String reversal: `s[::-1]` — no manual loop needed.
- Prefer `enumerate()` over manual index counters.
- Use `sorted(items, key=...)` instead of hand-rolled comparison sorts.
- A `try/except` around the whole function body is a smell — catch only
  the specific exception you expect, at the point you expect it.
