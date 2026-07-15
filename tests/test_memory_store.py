"""P14 — AttemptStore (SQLite durable memory)."""
import tempfile
from pathlib import Path

from harness.memory.store import AttemptStore


def _store():
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    return AttemptStore(db_path=tmp)


def test_record_and_recent():
    store = _store()
    store.record_attempt(
        kata_id="binary_search", kata_category="algorithms", session_id="s1",
        attempt_no=1, passed=False, duration_ms=12.0, tokens_used=100,
        trace_id="t1", failure_reason="off-by-one on boundary",
    )
    store.record_attempt(
        kata_id="binary_search", kata_category="algorithms", session_id="s1",
        attempt_no=2, passed=True, duration_ms=8.0, tokens_used=90, trace_id="t1",
    )

    recent = store.recent(limit=10)
    assert len(recent) == 2
    assert recent[0]["attempt_no"] == 2  # most recent first


def test_stats_computes_pass_rate_per_kata():
    store = _store()
    store.record_attempt(kata_id="k", kata_category="c", session_id="s", attempt_no=1, passed=False, duration_ms=1, tokens_used=1, trace_id="t")
    store.record_attempt(kata_id="k", kata_category="c", session_id="s", attempt_no=2, passed=True, duration_ms=1, tokens_used=1, trace_id="t")

    stats = store.stats()
    assert stats["per_kata"]["k"]["attempt_count"] == 2
    assert stats["per_kata"]["k"]["pass_rate"] == 0.5
    assert stats["total_attempts"] == 2


def test_recent_failure_hint_returns_most_recent_reason_for_category():
    store = _store()
    store.record_attempt(kata_id="a", kata_category="algorithms", session_id="s", attempt_no=1, passed=False, duration_ms=1, tokens_used=1, trace_id="t", failure_reason="wrong boundary")
    store.record_attempt(kata_id="b", kata_category="algorithms", session_id="s", attempt_no=1, passed=False, duration_ms=1, tokens_used=1, trace_id="t", failure_reason="off by one")

    hint = store.recent_failure_hint("algorithms")
    assert hint is not None
    assert "off by one" in hint


def test_recent_failure_hint_none_when_no_failures():
    store = _store()
    assert store.recent_failure_hint("nonexistent-category") is None
