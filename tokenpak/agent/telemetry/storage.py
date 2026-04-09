"""TokenPak Agent Telemetry Storage — SQLite persistence for request stats."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

from .collector import RequestStats, SessionStats

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tp_requests (
    request_id      TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    tokens_raw      INTEGER NOT NULL DEFAULT 0,
    tokens_sent     INTEGER NOT NULL DEFAULT 0,
    tokens_saved    INTEGER NOT NULL DEFAULT 0,
    percent_saved   REAL NOT NULL DEFAULT 0,
    cost_saved      REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tp_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    requests        INTEGER NOT NULL DEFAULT 0,
    tokens_raw      INTEGER NOT NULL DEFAULT 0,
    tokens_sent     INTEGER NOT NULL DEFAULT 0,
    tokens_saved    INTEGER NOT NULL DEFAULT 0,
    cost_saved      REAL NOT NULL DEFAULT 0
);
"""


class TelemetryStorage:
    """Persist request stats to a local SQLite database.

    Usage::

        storage = TelemetryStorage("~/.tokenpak/telemetry.db")
        storage.save_request(stats)
        rows = storage.list_requests(limit=50)
        storage.close()
    """

    def __init__(self, db_path: str = ":memory:"):
        self._path = db_path
        self._local = threading.local()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.executescript(_DDL)
        conn.commit()

    def save_request(self, stats: RequestStats) -> None:
        """Persist a single request's stats."""
        conn = self._conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO tp_requests
                (request_id, timestamp, tokens_raw, tokens_sent, tokens_saved, percent_saved, cost_saved)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stats.request_id,
                stats.timestamp.isoformat(),
                stats.input_tokens_raw,
                stats.input_tokens_sent,
                stats.tokens_saved,
                stats.percent_saved,
                stats.cost_saved,
            ),
        )
        conn.commit()

    def list_requests(self, limit: int = 100) -> list[dict]:
        """Return recent requests as dicts, most recent first."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM tp_requests ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def save_session(self, session: SessionStats, ended_at: Optional[datetime] = None) -> int:
        """Persist session summary and return the row id."""
        conn = self._conn()
        cursor = conn.execute(
            """
            INSERT INTO tp_sessions
                (started_at, ended_at, requests, tokens_raw, tokens_sent, tokens_saved, cost_saved)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_start_time.isoformat(),
                ended_at.isoformat() if ended_at else None,
                session.session_requests,
                session.session_total_tokens_raw,
                session.session_total_tokens_sent,
                session.session_total_saved,
                session.session_total_cost_saved,
            ),
        )
        conn.commit()
        return cursor.lastrowid or 0

    def lifetime_totals(self) -> dict[str, Any]:
        """Return all-time aggregates across persisted sessions."""
        conn = self._conn()
        row = conn.execute("""
            SELECT
                COUNT(*) AS sessions,
                COALESCE(SUM(requests), 0) AS total_requests,
                COALESCE(SUM(tokens_raw), 0) AS total_tokens_raw,
                COALESCE(SUM(tokens_saved), 0) AS total_tokens_saved,
                COALESCE(SUM(cost_saved), 0.0) AS total_cost_saved
            FROM tp_sessions
            """).fetchone()
        return dict(row) if row else {}

    def prune(self, days: int = 30) -> int:
        """Delete requests older than N days. Returns number of rows deleted."""
        conn = self._conn()
        cursor = conn.execute(
            "DELETE FROM tp_requests WHERE timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


_storage: Optional[TelemetryStorage] = None


def get_telemetry_storage(db_path: str = ":memory:") -> TelemetryStorage:
    """Return the process-level singleton storage."""
    global _storage
    if _storage is None:
        _storage = TelemetryStorage(db_path)
    return _storage
