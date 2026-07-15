---
name: edge-cases
description: Standard edge cases to check before declaring code done
triggers: data-structures, warmup, conditionals
---

# Edge case checklist

- Empty input (empty string, empty list) — does the function still return
  a sensible value instead of raising?
- Single-element input — many off-by-one bugs only show up here.
- Input with only "closing" tokens and no "opening" ones (for stack-based
  problems like bracket matching).
- Repeated/duplicate values — don't assume inputs are distinct unless the
  problem statement says so.
