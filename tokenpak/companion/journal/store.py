# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed session journal store.

Journals capture what happened in a session in a way that survives compaction
and session boundaries.  They're the raw material for capsule building.

Schema
------
sessions: one row per ``tokenpak claude`` invocation
entries:  timestamped notes within a session (auto or manual)
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class JournalEntry:
    """Single journal entry within a session."""

    timestamp: float
    entry_type: str  # "auto", "user", "milestone", "cost", "capsule"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionRecord:
    """Summary of a journaled session."""

    session_id: str
    started_at: float
    ended_at: Optional[float] = None
    project_dir: str = ""
    model: str = ""
    total_requests: int = 0
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    entry_count: int = 0
    capsule_path: Optional[str] = None


class JournalStore:
    """Persistent session journal backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at REAL NOT NULL,
                ended_at REAL,
                project_dir TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                total_requests INTEGER NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0.0,
                total_input_tokens INTEGER NOT NULL DEFAULT 0,
                total_output_tokens INTEGER NOT NULL DEFAULT 0,
                capsule_path TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                entry_type TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(timestamp)")
        conn.commit()
        conn.close()

    def start_session(
        self,
        session_id: str,
        project_dir: str = "",
        model: str = "",
    ) -> None:
        """Record a new session start."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, started_at, project_dir, model)
               VALUES (?, ?, ?, ?)""",
            (session_id, time.time(), project_dir, model),
        )
        conn.commit()
        conn.close()

    def end_session(self, session_id: str) -> None:
        """Record session end and update totals."""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
            (time.time(), session_id),
        )
        conn.commit()
        conn.close()

    def add_entry(
        self,
        session_id: str,
        entry_type: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append a journal entry to a session."""
        import json
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            """INSERT INTO entries (session_id, timestamp, entry_type, content, metadata_json)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, time.time(), entry_type, content,
             json.dumps(metadata or {}, default=str)),
        )
        conn.commit()
        conn.close()

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        """Retrieve a session record."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        entry_count = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        conn.close()
        if not row:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            project_dir=row["project_dir"],
            model=row["model"],
            total_requests=row["total_requests"],
            total_cost_usd=row["total_cost_usd"],
            total_input_tokens=row["total_input_tokens"],
            total_output_tokens=row["total_output_tokens"],
            entry_count=entry_count,
            capsule_path=row["capsule_path"],
        )

    def get_entries(
        self,
        session_id: str,
        entry_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[JournalEntry]:
        """Retrieve journal entries for a session."""
        import json
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        if entry_type:
            rows = conn.execute(
                "SELECT * FROM entries WHERE session_id = ? AND entry_type = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, entry_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entries WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        conn.close()
        return [
            JournalEntry(
                timestamp=r["timestamp"],
                entry_type=r["entry_type"],
                content=r["content"],
                metadata=json.loads(r["metadata_json"]),
            )
            for r in rows
        ]

    def recent_sessions(self, limit: int = 10) -> list[SessionRecord]:
        """List recent sessions, newest first."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [
            SessionRecord(
                session_id=r["session_id"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                project_dir=r["project_dir"],
                model=r["model"],
                total_requests=r["total_requests"],
                total_cost_usd=r["total_cost_usd"],
                total_input_tokens=r["total_input_tokens"],
                total_output_tokens=r["total_output_tokens"],
                capsule_path=r["capsule_path"],
            )
            for r in rows
        ]
