"""P15 — Pasted-text problem source.

The reliability backstop. LeetCode's GraphQL endpoint is undocumented and can
block or change shape at any time; this source always works because the user
supplies the statement directly. It's also the only way to run the harness on
a problem that isn't on LeetCode at all.
"""
from __future__ import annotations

from harness.models import RawProblem
from harness.problems.base import ProblemSource

# Anything long and multi-line is treated as a pasted statement rather than a
# reference. Kept as the last source in the chain, so this only fires when no
# structured ref (curated id, LeetCode number/slug/URL) matched first.
_MIN_PASTE_LEN = 40


class PastedTextSource(ProblemSource):
    name = "pasted"

    def can_handle(self, ref: str) -> bool:
        return len(ref.strip()) >= _MIN_PASTE_LEN

    def fetch(self, ref: str) -> RawProblem:
        text = ref.strip()
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "Pasted problem")
        title = first_line[:80]
        return RawProblem(
            ref="pasted",
            title=title,
            statement_html=f"<pre>{text}</pre>",
            statement_text=text,
            difficulty="unknown",
            source="pasted",
        )
