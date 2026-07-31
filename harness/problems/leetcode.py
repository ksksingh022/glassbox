"""P15 — LeetCode problem source.

LeetCode has no documented public API. Its GraphQL endpoint serves public
problem content without auth, which is what this uses; that is the genuinely
fragile part of this design and the reason `ProblemSource` is a strategy
(`PastedTextSource` is the always-works fallback when this breaks).

Security: `ALLOWED_HOSTS` is enforced on every URL this class will touch, so a
user-supplied "problem reference" can't turn `POST /solve` into an SSRF proxy
for arbitrary internal addresses. Fetched statements are *untrusted text* —
they're passed downstream as delimited data for the Extractor to parse, never
executed, and never treated as instructions to the harness.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

from harness.models import RawProblem
from harness.problems.base import ProblemFetchError, ProblemSource, html_to_text

GRAPHQL_URL = "https://leetcode.com/graphql"
PROBLEM_INDEX_URL = "https://leetcode.com/api/problems/all/"
PROBLEM_URL_TEMPLATE = "https://leetcode.com/problems/{slug}/"

# Hard allowlist — the only hosts this harness will ever fetch from.
ALLOWED_HOSTS = frozenset({"leetcode.com", "www.leetcode.com"})

_INDEX_CACHE = Path(__file__).parent.parent.parent / "data" / "leetcode_index.json"
_INDEX_TTL_S = 7 * 24 * 3600

_QUESTION_QUERY = """
query getQuestion($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    questionFrontendId
    title
    titleSlug
    content
    difficulty
    topicTags { name }
    codeSnippets { langSlug code }
    exampleTestcases
  }
}
"""

# "295", "leetcode 295", "lc 295", "#295"
_NUMBER_REF = re.compile(r"^(?:leetcode|lc|problem)?\s*#?\s*(\d{1,4})$", re.IGNORECASE)
# "find-median-from-data-stream", optionally prefixed
_SLUG_REF = re.compile(r"^(?:leetcode|lc)?\s*([a-z0-9]+(?:-[a-z0-9]+)+)$", re.IGNORECASE)
_URL_REF = re.compile(r"^https?://", re.IGNORECASE)
_URL_SLUG = re.compile(r"leetcode\.com/problems/([a-z0-9\-]+)", re.IGNORECASE)


def assert_host_allowed(url: str) -> None:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ProblemFetchError(
            f"refusing to fetch from {host or url!r}: only {sorted(ALLOWED_HOSTS)} are allowed"
        )


class LeetCodeSource(ProblemSource):
    name = "leetcode"

    def __init__(self, timeout_s: float = 20.0, index_path: Path | None = None):
        self._timeout_s = timeout_s
        self._index_path = index_path or _INDEX_CACHE
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=15.0, pool=10.0),
            headers={
                # LeetCode 403s requests without a browser-ish UA.
                "User-Agent": "Mozilla/5.0 (compatible; GlassboxHarness/1.0)",
                "Referer": "https://leetcode.com",
                "Content-Type": "application/json",
            },
            follow_redirects=True,
        )

    # -- ref handling --------------------------------------------------------

    def can_handle(self, ref: str) -> bool:
        ref = ref.strip()
        if _URL_REF.match(ref):
            return bool(_URL_SLUG.search(ref))
        return bool(_NUMBER_REF.match(ref) or _SLUG_REF.match(ref))

    def resolve_slug(self, ref: str) -> str:
        ref = ref.strip()
        if _URL_REF.match(ref):
            assert_host_allowed(ref)
            match = _URL_SLUG.search(ref)
            if not match:
                raise ProblemFetchError(f"could not find a problem slug in {ref!r}")
            return match.group(1).lower()

        number = _NUMBER_REF.match(ref)
        if number:
            return self._slug_for_number(int(number.group(1)))

        slug = _SLUG_REF.match(ref)
        if slug:
            return slug.group(1).lower()
        raise ProblemFetchError(f"unrecognized LeetCode reference: {ref!r}")

    def _load_index(self) -> dict[str, str]:
        """number -> slug, cached on disk. One fetch covers every problem, so
        a numeric ref costs nothing after the first lookup."""
        if self._index_path.exists():
            age = time.time() - self._index_path.stat().st_mtime
            if age < _INDEX_TTL_S:
                try:
                    return json.loads(self._index_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass  # fall through and refetch

        assert_host_allowed(PROBLEM_INDEX_URL)
        try:
            resp = self._client.get(PROBLEM_INDEX_URL)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ProblemFetchError(f"could not load the LeetCode problem index: {exc}") from exc

        index = {
            str(row["stat"]["frontend_question_id"]): row["stat"]["question__title_slug"]
            for row in data.get("stat_status_pairs", [])
            if row.get("stat", {}).get("question__title_slug")
        }
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            self._index_path.write_text(json.dumps(index))
        except OSError:
            pass  # cache is an optimization, not a requirement
        return index

    def _slug_for_number(self, number: int) -> str:
        slug = self._load_index().get(str(number))
        if not slug:
            raise ProblemFetchError(f"LeetCode problem #{number} not found in the index")
        return slug

    # -- fetch ---------------------------------------------------------------

    def fetch(self, ref: str) -> RawProblem:
        slug = self.resolve_slug(ref)
        assert_host_allowed(GRAPHQL_URL)
        try:
            resp = self._client.post(
                GRAPHQL_URL,
                json={"query": _QUESTION_QUERY, "variables": {"titleSlug": slug}},
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ProblemFetchError(f"LeetCode fetch failed for {slug!r}: {exc}") from exc

        return self.parse_payload(ref, slug, payload)

    @staticmethod
    def parse_payload(ref: str, slug: str, payload: dict) -> RawProblem:
        """Split out from `fetch` so tests can exercise parsing against a
        recorded payload without touching the network."""
        question = (payload or {}).get("data", {}).get("question")
        if not question:
            errors = (payload or {}).get("errors")
            raise ProblemFetchError(
                f"LeetCode returned no question for {slug!r}" + (f": {errors}" if errors else "")
            )

        content = question.get("content") or ""
        if not content:
            raise ProblemFetchError(
                f"{slug!r} has no public content (premium-only problems can't be fetched)"
            )

        starter = ""
        for snippet in question.get("codeSnippets") or []:
            if snippet.get("langSlug") == "python3":
                starter = snippet.get("code") or ""
                break

        return RawProblem(
            ref=ref,
            title=question.get("title") or slug,
            statement_html=content,
            statement_text=html_to_text(content),
            difficulty=(question.get("difficulty") or "unknown").lower(),
            topics=[t.get("name", "") for t in question.get("topicTags") or [] if t.get("name")],
            starter_code=starter,
            example_testcases=question.get("exampleTestcases") or "",
            source="leetcode",
            url=PROBLEM_URL_TEMPLATE.format(slug=slug),
        )
