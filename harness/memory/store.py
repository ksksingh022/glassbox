"""P14 — Durable state + memory (cross-session attempt log).

SQLite only (Phase 3a per plan §5.2 — Postgres/Supabase is Phase 3b and
explicitly out of scope here; noted in DECISIONS.md rather than silently
dropped). Two jobs:

1. A durable attempt log — every attempt, from either orchestrator, is
   recorded so the history dashboard (P8 full) has real data across
   restarts, unlike the in-memory `_RUNS` registry in `api/main.py`.
2. Recall (the plan's "optional stretch", implemented not skipped):
   `recent_failure_hint()` looks up the most recent failed attempt in the
   same kata category and returns a one-line reason, injected into the
   Planner's prompt on attempt 1 — this is what makes it memory rather
   than just a log.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY,
    kata_id TEXT NOT NULL,
    kata_category TEXT NOT NULL,
    session_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    tokens_used INTEGER NOT NULL,
    trace_id TEXT NOT NULL,
    failure_reason TEXT,
    created_at REAL NOT NULL
);
"""

_DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "glassbox.db"


class AttemptStore:
    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def record_attempt(
        self,
        kata_id: str,
        kata_category: str,
        session_id: str,
        attempt_no: int,
        passed: bool,
        duration_ms: float,
        tokens_used: int,
        trace_id: str,
        failure_reason: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO attempts (id, kata_id, kata_category, session_id, attempt_no, "
            "passed, duration_ms, tokens_used, trace_id, failure_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex, kata_id, kata_category, session_id, attempt_no,
                int(passed), duration_ms, tokens_used, trace_id, failure_reason, time.time(),
            ),
        )
        self._conn.commit()

    def recent_failure_hint(self, kata_category: str, exclude_kata_id: str | None = None) -> str | None:
        rows = self._conn.execute(
            "SELECT kata_id, failure_reason FROM attempts "
            "WHERE kata_category = ? AND passed = 0 AND failure_reason IS NOT NULL "
            "AND (? IS NULL OR kata_id != ?) "
            "ORDER BY created_at DESC LIMIT 1",
            (kata_category, exclude_kata_id, exclude_kata_id),
        ).fetchall()
        if not rows:
            return None
        kata_id, reason = rows[0]
        return f"last time a '{kata_category}' kata ({kata_id}) failed because: {reason}"

    def stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT kata_id, passed, attempt_no FROM attempts"
        ).fetchall()
        by_kata: dict[str, dict] = {}
        for kata_id, passed, attempt_no in rows:
            entry = by_kata.setdefault(kata_id, {"runs": 0, "passed": 0, "attempt_nos": []})
            entry["attempt_nos"].append(attempt_no)
            # A "run" boundary is attempt_no == 1; count passes at the row
            # level (each row is one attempt) for a simple pass-rate signal.
            entry["runs"] += 1
            entry["passed"] += int(bool(passed))

        per_kata = {}
        for kata_id, entry in by_kata.items():
            runs = entry["runs"]
            per_kata[kata_id] = {
                "attempt_count": runs,
                "pass_rate": round(entry["passed"] / runs, 3) if runs else 0.0,
                "max_attempt_no": max(entry["attempt_nos"]) if entry["attempt_nos"] else 0,
            }
        return {"per_kata": per_kata, "total_attempts": len(rows)}

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, kata_id, kata_category, session_id, attempt_no, passed, "
            "duration_ms, tokens_used, trace_id, failure_reason, created_at "
            "FROM attempts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = [
            "id", "kata_id", "kata_category", "session_id", "attempt_no", "passed",
            "duration_ms", "tokens_used", "trace_id", "failure_reason", "created_at",
        ]
        return [dict(zip(cols, row)) for row in rows]

    def close(self) -> None:
        self._conn.close()
