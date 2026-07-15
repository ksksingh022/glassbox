---
name: algorithms
description: Common algorithmic techniques and their off-by-one traps
triggers: algorithms, sorting, search
---

# Algorithm technique notes

- Binary search: use `low <= high` (inclusive), not `low < high` — the
  latter misses the case where the target is the single remaining element.
- When merging/sorting intervals or ranges, sort first, then do a single
  linear pass comparing against the last merged element.
- For search/traversal problems, trace through a length-0 and length-1
  input by hand before trusting the general case.
