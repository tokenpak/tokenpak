"""Shared fixtures for rolling-cap spend_guard tests."""

from __future__ import annotations

import sqlite3
import time
import datetime as dt

import pytest

from tokenpak.proxy.spend_guard import rolling_caps as rc


@pytest.fixture
def tmp_monitor_db(tmp_path, monkeypatch):
    """Provide a fresh monitor.db at tmp_path with the standard schema.

    Patches `rolling_caps._DEFAULT_MONITOR_DB` so production code paths
    pick up our temp DB without needing to pass `monitor_db_path` everywhere.
    Returns the path string.
    """
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms INTEGER,
            status_code INTEGER,
            endpoint TEXT,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            session_id TEXT DEFAULT ''
        )"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(rc, "_DEFAULT_MONITOR_DB", str(db))
    rc.reset_caches_for_testing()
    yield str(db)
    rc.reset_caches_for_testing()


def insert_request(
    db_path: str,
    session_id: str,
    cost: float,
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cache_read_tokens: int = 0,
    seconds_ago: float = 0.0,
):
    """Insert one synthetic monitor row."""
    ts = (dt.datetime.now() - dt.timedelta(seconds=seconds_ago)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO requests (timestamp, model, request_type, input_tokens,
              output_tokens, estimated_cost, cache_read_tokens, cache_creation_tokens, session_id)
           VALUES (?, 'claude-opus-4-7', 'chat', ?, ?, ?, ?, 0, ?)""",
        (ts, input_tokens, output_tokens, cost, cache_read_tokens, session_id),
    )
    conn.commit()
    conn.close()
