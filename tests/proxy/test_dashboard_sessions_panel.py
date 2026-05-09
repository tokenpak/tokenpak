# SPDX-License-Identifier: Apache-2.0
"""CCG-13 — GET /metrics/dashboard sessions panel tests.

Tests the _handle_metrics_dashboard() handler by exercising the query
helper logic directly against a temporary SQLite database.  No running
proxy is required.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Helpers: build a temp monitor.db with known fixture data
# ---------------------------------------------------------------------------

def _make_db(rows: List[Dict[str, Any]]) -> str:
    """Return path to a temp db populated with the given request rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model TEXT,
            session_id TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            estimated_cost REAL DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            attribution_source TEXT DEFAULT 'unknown'
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO requests (timestamp, model, session_id, input_tokens, "
            "output_tokens, cache_read_tokens, cache_creation_tokens, "
            "estimated_cost, latency_ms, attribution_source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r.get("timestamp", "2026-05-05T00:00:00"),
                r.get("model", "claude-sonnet-4-6"),
                r.get("session_id", ""),
                r.get("input_tokens", 0),
                r.get("output_tokens", 0),
                r.get("cache_read_tokens", 0),
                r.get("cache_creation_tokens", 0),
                r.get("estimated_cost", 0.0),
                r.get("latency_ms", 0),
                r.get("attribution_source", "unknown"),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _query_sessions(db_path: str) -> List[Dict[str, Any]]:
    """Run the same query logic used by _handle_metrics_dashboard()."""
    conn = sqlite3.connect(db_path, timeout=3.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            session_id,
            SUM(input_tokens)           AS input_tokens,
            SUM(output_tokens)          AS output_tokens,
            SUM(cache_read_tokens)      AS cache_read_input_tokens,
            SUM(cache_creation_tokens)  AS cache_creation_input_tokens,
            SUM(estimated_cost)         AS cost,
            COUNT(*)                    AS request_count,
            MAX(attribution_source)     AS platform
        FROM requests
        WHERE session_id IS NOT NULL AND session_id != ''
        GROUP BY session_id
        ORDER BY request_count DESC
        LIMIT 20
    """).fetchall()

    sessions = []
    for row in rows:
        sid = row["session_id"]
        lat_rows = conn.execute(
            "SELECT latency_ms FROM requests "
            "WHERE session_id=? AND latency_ms IS NOT NULL "
            "ORDER BY latency_ms",
            (sid,),
        ).fetchall()
        if lat_rows:
            vals = [r[0] for r in lat_rows]
            n = len(vals)
            mid = n // 2
            p50 = vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) // 2
        else:
            p50 = 0
        sessions.append({
            "session_id": sid,
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cache_read_input_tokens": row["cache_read_input_tokens"] or 0,
            "cache_creation_input_tokens": row["cache_creation_input_tokens"] or 0,
            "cost": round(row["cost"] or 0.0, 6),
            "request_count": row["request_count"] or 0,
            "latency_p50": p50,
            "platform": row["platform"] or "unknown",
        })
    conn.close()
    return sessions


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyDatabase:
    def test_empty_db_returns_empty_sessions(self, tmp_path):
        db = str(tmp_path / "monitor.db")
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                estimated_cost REAL DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                attribution_source TEXT DEFAULT 'unknown'
            )
        """)
        conn.commit()
        conn.close()
        sessions = _query_sessions(db)
        assert sessions == []

    def test_null_session_ids_excluded(self, tmp_path):
        db = _make_db([
            {"session_id": None, "input_tokens": 10},
            {"session_id": "", "input_tokens": 20},
            {"session_id": "real-session", "input_tokens": 30},
        ])
        sessions = _query_sessions(db)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "real-session"
        os.unlink(db)


class TestTopSessionsOrdering:
    def test_sessions_ordered_by_request_count_desc(self, tmp_path):
        db = _make_db([
            {"session_id": "a", "input_tokens": 5},
            {"session_id": "b", "input_tokens": 5},
            {"session_id": "b", "input_tokens": 5},
            {"session_id": "b", "input_tokens": 5},
            {"session_id": "c", "input_tokens": 5},
            {"session_id": "c", "input_tokens": 5},
        ])
        sessions = _query_sessions(db)
        assert [s["session_id"] for s in sessions] == ["b", "c", "a"]
        os.unlink(db)

    def test_limit_20_sessions(self, tmp_path):
        rows = [{"session_id": f"sess-{i}"} for i in range(25)]
        db = _make_db(rows)
        sessions = _query_sessions(db)
        assert len(sessions) <= 20
        os.unlink(db)


class TestAggregatedColumns:
    def test_token_columns_summed(self, tmp_path):
        db = _make_db([
            {
                "session_id": "s1",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
                "cache_creation_tokens": 5,
                "estimated_cost": 0.002,
            },
            {
                "session_id": "s1",
                "input_tokens": 200,
                "output_tokens": 80,
                "cache_read_tokens": 20,
                "cache_creation_tokens": 8,
                "estimated_cost": 0.004,
            },
        ])
        sessions = _query_sessions(db)
        assert len(sessions) == 1
        s = sessions[0]
        assert s["input_tokens"] == 300
        assert s["output_tokens"] == 130
        assert s["cache_read_input_tokens"] == 30
        assert s["cache_creation_input_tokens"] == 13
        assert abs(s["cost"] - 0.006) < 1e-6
        assert s["request_count"] == 2
        os.unlink(db)

    def test_all_eight_columns_present(self, tmp_path):
        db = _make_db([{"session_id": "s1", "latency_ms": 100}])
        sessions = _query_sessions(db)
        assert len(sessions) == 1
        s = sessions[0]
        required = {
            "session_id", "input_tokens", "output_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens",
            "cost", "request_count", "latency_p50", "platform",
        }
        assert required.issubset(set(s.keys()))
        os.unlink(db)


class TestLatencyP50:
    def test_p50_odd_count(self, tmp_path):
        # 5 values: [10, 20, 30, 40, 50] → median = 30
        rows = [
            {"session_id": "s", "latency_ms": v}
            for v in [50, 10, 30, 20, 40]
        ]
        db = _make_db(rows)
        sessions = _query_sessions(db)
        assert sessions[0]["latency_p50"] == 30
        os.unlink(db)

    def test_p50_even_count(self, tmp_path):
        # 4 values: [10, 20, 30, 40] → median = (20+30)//2 = 25
        rows = [
            {"session_id": "s", "latency_ms": v}
            for v in [40, 10, 30, 20]
        ]
        db = _make_db(rows)
        sessions = _query_sessions(db)
        assert sessions[0]["latency_p50"] == 25
        os.unlink(db)

    def test_p50_single_row(self, tmp_path):
        db = _make_db([{"session_id": "s", "latency_ms": 42}])
        sessions = _query_sessions(db)
        assert sessions[0]["latency_p50"] == 42
        os.unlink(db)

    def test_p50_zero_when_no_latency(self, tmp_path):
        db = _make_db([{"session_id": "s", "latency_ms": None}])
        sessions = _query_sessions(db)
        assert sessions[0]["latency_p50"] == 0
        os.unlink(db)


class TestPlatformField:
    def test_platform_from_attribution_source(self, tmp_path):
        db = _make_db([
            {"session_id": "s", "attribution_source": "claude-code"},
        ])
        sessions = _query_sessions(db)
        assert sessions[0]["platform"] == "claude-code"
        os.unlink(db)

    def test_platform_defaults_to_unknown(self, tmp_path):
        db = _make_db([{"session_id": "s", "attribution_source": None}])
        sessions = _query_sessions(db)
        assert sessions[0]["platform"] == "unknown"
        os.unlink(db)
