"""P15 — Problem sources.

Same strategy-pattern shape as `SandboxExecutor` (Decision #6): one ABC,
several interchangeable implementations, chosen at runtime by which one
recognizes the reference the user typed.

Fetching runs **in the harness process, not the sandbox**. The sandbox is
deliberately network-less (`env={"PATH": ...}` in `sandbox/executor.py`) and
stays that way — untrusted *generated code* must never reach the network,
whereas fetching a problem statement is a trusted, allowlisted harness action.
"""
from __future__ import annotations

import html as html_mod
import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser

from harness.models import RawProblem


class ProblemFetchError(RuntimeError):
    """Raised when a source recognizes a ref but can't retrieve it. Kept
    distinct from extraction failure so the trace says which step broke."""


class ProblemSource(ABC):
    name: str

    @abstractmethod
    def can_handle(self, ref: str) -> bool:
        """Cheap, offline check — does this ref look like mine?"""

    @abstractmethod
    def fetch(self, ref: str) -> RawProblem:
        ...


class _TextExtractor(HTMLParser):
    """Minimal HTML -> text. stdlib only (ground rule: no dependency unless
    stdlib genuinely can't) — we need readable prompt text, not a faithful
    DOM, so a tag stripper that preserves block structure is enough.

    `<sup>` is handled explicitly and is not cosmetic: LeetCode writes bounds
    as `10<sup>5</sup>`, and naively dropping the tag yields "105" — which
    silently turns n <= 100,000 into n <= 105 and hands the Coder a wildly
    wrong complexity budget. Emitting `^` keeps the exponent readable to both
    the model and `complexity_budget.extract_max_n`.
    """

    _BLOCK = {"p", "div", "li", "br", "pre", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BLOCK:
            self._parts.append("\n")
        if tag == "li":
            self._parts.append("- ")
        if tag == "sup":
            self._parts.append("^")

    def handle_endtag(self, tag):
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*\n+", "\n\n", joined)
        return joined.strip()


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed HTML must degrade, not crash
        return html_mod.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()
    return parser.text()


# Constraints live in their own section of a LeetCode statement; pulling them
# out separately matters because ComplexityBudget only reads these lines.
_CONSTRAINTS_HEADER = re.compile(r"^\s*constraints?\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
_STOP_HEADER = re.compile(r"^\s*(follow[- ]?up|example|note)s?\b", re.IGNORECASE)


def extract_constraints(statement_text: str) -> list[str]:
    """Pull the bullet lines under a 'Constraints:' heading."""
    match = _CONSTRAINTS_HEADER.search(statement_text)
    if not match:
        return []
    out: list[str] = []
    for line in statement_text[match.end():].splitlines():
        stripped = line.strip().lstrip("-").strip()
        if not stripped:
            continue
        if _STOP_HEADER.match(stripped):
            break
        out.append(stripped)
    return out


class ProblemFetcher:
    """Tries each source in order and returns the first that recognizes the
    ref. Order matters: curated (offline, exact ids) before LeetCode (network)
    so the demo katas never trigger a network call."""

    def __init__(self, sources: list[ProblemSource]):
        self._sources = sources

    def fetch(self, ref: str) -> RawProblem:
        ref = (ref or "").strip()
        if not ref:
            raise ProblemFetchError("empty problem reference")
        for source in self._sources:
            if source.can_handle(ref):
                return source.fetch(ref)
        raise ProblemFetchError(
            f"no problem source recognized {ref!r} — expected a LeetCode number/slug/URL, "
            "a curated kata id, or pasted problem text"
        )
