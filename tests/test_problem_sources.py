"""P15 — problem sources: ref parsing, payload parsing, allowlist, fallbacks.

No live network anywhere in here — LeetCode parsing is exercised against a
recorded payload shape via `LeetCodeSource.parse_payload`, which is exactly
why that method is split out from `fetch`.
"""
import pytest

from harness.problems import CuratedSource, PastedTextSource, default_fetcher
from harness.problems.base import ProblemFetchError, extract_constraints, html_to_text
from harness.problems.leetcode import LeetCodeSource, assert_host_allowed

# Shape of a real leetcode.com/graphql response, trimmed to what we read.
_PAYLOAD_295 = {
    "data": {
        "question": {
            "questionFrontendId": "295",
            "title": "Find Median from Data Stream",
            "titleSlug": "find-median-from-data-stream",
            "content": (
                "<p>The <strong>median</strong> is the middle value.</p>"
                "<p>Implement the MedianFinder class:</p>"
                "<ul><li><code>MedianFinder()</code> initializes the object.</li></ul>"
                "<p><strong>Example 1:</strong></p>"
                "<pre><strong>Input</strong>\n"
                '["MedianFinder", "addNum", "findMedian"]\n[[], [1], []]\n'
                "<strong>Output</strong>\n[null, null, 1.0]\n</pre>"
                "<p><strong>Constraints:</strong></p>"
                "<ul>"
                "<li><code>-10^5 &lt;= num &lt;= 10^5</code></li>"
                "<li>At most <code>5 * 10^4</code> calls will be made to addNum.</li>"
                "</ul>"
            ),
            "difficulty": "Hard",
            "topicTags": [{"name": "Heap (Priority Queue)"}, {"name": "Design"}],
            "codeSnippets": [
                {"langSlug": "java", "code": "class MedianFinder {}"},
                {"langSlug": "python3", "code": "class MedianFinder:\n    def __init__(self):\n        pass\n"},
            ],
            "exampleTestcases": '["MedianFinder","addNum","findMedian"]\n[[],[1],[]]',
        }
    }
}


# --- ref recognition --------------------------------------------------------

@pytest.mark.parametrize("ref", ["295", "leetcode 295", "lc 295", "#295", "LeetCode 1"])
def test_leetcode_recognizes_numeric_refs(ref):
    assert LeetCodeSource().can_handle(ref) is True


@pytest.mark.parametrize("ref", [
    "find-median-from-data-stream",
    "two-sum",
    "https://leetcode.com/problems/two-sum/",
    "https://leetcode.com/problems/two-sum/description/",
])
def test_leetcode_recognizes_slug_and_url_refs(ref):
    assert LeetCodeSource().can_handle(ref) is True


def test_leetcode_ignores_unrelated_refs():
    source = LeetCodeSource()
    assert source.can_handle("https://example.com/problems/two-sum/") is False
    assert source.can_handle("reverse_string") is False  # curated id (underscores)


def test_resolve_slug_from_url():
    assert LeetCodeSource().resolve_slug(
        "https://leetcode.com/problems/find-median-from-data-stream/"
    ) == "find-median-from-data-stream"


def test_resolve_slug_passthrough():
    assert LeetCodeSource().resolve_slug("two-sum") == "two-sum"


# --- allowlist (SSRF guard) -------------------------------------------------

def test_allowlist_permits_leetcode():
    assert_host_allowed("https://leetcode.com/graphql")  # no raise


@pytest.mark.parametrize("url", [
    "https://evil.example.com/graphql",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
    "http://localhost:8000/admin",
])
def test_allowlist_rejects_everything_else(url):
    with pytest.raises(ProblemFetchError):
        assert_host_allowed(url)


def test_resolve_slug_rejects_disallowed_host_url():
    # A URL that *looks* like it has a problem slug but is off-host must not
    # be fetched, even though the slug regex would match.
    with pytest.raises(ProblemFetchError):
        LeetCodeSource().resolve_slug("https://evil.example.com/leetcode.com/problems/two-sum/")


# --- payload parsing --------------------------------------------------------

def test_parses_leetcode_payload():
    raw = LeetCodeSource.parse_payload("295", "find-median-from-data-stream", _PAYLOAD_295)
    assert raw.title == "Find Median from Data Stream"
    assert raw.difficulty == "hard"
    assert "Design" in raw.topics
    assert raw.source == "leetcode"
    assert raw.url.endswith("/find-median-from-data-stream/")
    assert "MedianFinder" in raw.starter_code       # python3 snippet, not java
    assert "class MedianFinder {}" not in raw.starter_code
    assert "median" in raw.statement_text.lower()   # html stripped to text
    assert "<p>" not in raw.statement_text


def test_missing_question_raises():
    with pytest.raises(ProblemFetchError):
        LeetCodeSource.parse_payload("999999", "nope", {"data": {"question": None}})


def test_premium_problem_without_content_raises():
    payload = {"data": {"question": {"title": "X", "content": "", "difficulty": "Hard"}}}
    with pytest.raises(ProblemFetchError, match="premium"):
        LeetCodeSource.parse_payload("1", "x", payload)


# --- html + constraints -----------------------------------------------------

def test_html_to_text_strips_tags_and_unescapes():
    text = html_to_text("<p>a &lt;= b</p><ul><li>one</li></ul>")
    assert "<p>" not in text
    assert "a <= b" in text
    assert "one" in text


def test_html_to_text_preserves_superscript_exponents():
    # Regression: LeetCode writes bounds as `10<sup>5</sup>`. Dropping the tag
    # naively yields "105", silently turning n <= 100,000 into n <= 105 and
    # handing the Coder a wildly wrong complexity budget.
    assert "10^5" in html_to_text("<li><code>1 &lt;= n &lt;= 10<sup>5</sup></code></li>")


def test_superscript_bound_feeds_correct_budget():
    from harness.complexity_budget import for_constraints
    text = html_to_text(
        "<p><strong>Constraints:</strong></p>"
        "<ul><li><code>2 &lt;= nums.length &lt;= 10<sup>4</sup></code></li></ul>"
    )
    budget = for_constraints(extract_constraints(text))
    assert budget.max_n == 10_000
    assert budget.acceptable == "O(n log n)"


def test_extract_constraints_pulls_bullets():
    raw = LeetCodeSource.parse_payload("295", "find-median-from-data-stream", _PAYLOAD_295)
    constraints = extract_constraints(raw.statement_text)
    assert any("10^5" in c or "100000" in c for c in constraints)
    assert any("calls" in c.lower() for c in constraints)


def test_extract_constraints_empty_when_absent():
    assert extract_constraints("Just a problem with no constraints section.") == []


# --- other sources + routing ------------------------------------------------

def test_curated_source_handles_kata_ids():
    source = CuratedSource()
    assert source.can_handle("binary_search") is True
    assert source.can_handle("295") is False
    raw = source.fetch("binary_search")
    assert raw.source == "curated"
    assert raw.title == "Binary Search"


def test_pasted_source_needs_substantial_text():
    source = PastedTextSource()
    assert source.can_handle("295") is False
    long_text = "Given an array of integers, return the indices of the two numbers that add up to target."
    assert source.can_handle(long_text) is True
    assert source.fetch(long_text).source == "pasted"


def test_fetcher_prefers_curated_over_network():
    # A curated id must never fall through to the LeetCode source.
    raw = default_fetcher().fetch("binary_search")
    assert raw.source == "curated"


def test_fetcher_rejects_unrecognized_ref():
    with pytest.raises(ProblemFetchError):
        default_fetcher().fetch("???")


def test_fetcher_rejects_empty_ref():
    with pytest.raises(ProblemFetchError):
        default_fetcher().fetch("   ")
