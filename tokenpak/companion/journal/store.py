"""SQLite-backed journal store for the tokenpak companion.

Persists session events across invocations.  Designed for the companion's
pre-send hook and MCP tools (journal_read / journal_write).

Schema
------
sessions  — one row per session_id, with start timestamp.
entries   — typed log entries linked to a session.

Entry types: ``auto`` (hook-written), ``user`` (manual), ``milestone``,
``cost``, ``capsule``.

Wave 2 (COMP-05, COMP-06) will extend this with budget columns and cost
entries.  This module intentionally keeps schema minimal — no migrations
needed for an additive column.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_DB_PATH = Path.home() / ".tokenpak" / "companion" / "journal.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id  TEXT PRIMARY KEY,
    started_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    entry_type  TEXT    NOT NULL DEFAULT 'auto',
    content     TEXT    NOT NULL,
    timestamp   REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS entries_session_idx ON entries(session_id);
CREATE INDEX IF NOT EXISTS entries_type_idx    ON entries(entry_type);
"""


class JournalStore:
    """Read/write companion journal entries via SQLite.

    Args:
        db_path: Path to the SQLite database file.  Created (along with any
            parent directories) on first use if it does not exist.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH

    # ------------------------------------------------------------------
    # Connection / schema setup
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        """Create tables if they do not already exist."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def ensure_session(self, session_id: str) -> None:
        """Insert a session row if one does not already exist."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, started_at) VALUES (?, ?)",
                (session_id, time.time()),
            )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_entry(
        self,
        session_id: str,
        content: str,
        entry_type: str = "auto",
    ) -> int:
        """Insert a new journal entry.

        Automatically creates the session row if absent.

        Args:
            session_id: Identifier for the Claude Code session.
            content: Free-form text content for this entry.
            entry_type: Semantic type tag (``auto``, ``user``, ``milestone``,
                ``cost``, ``capsule``).

        Returns:
            The ``rowid`` of the new entry.
        """
        self.ensure_schema()
        self.ensure_session(session_id)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO entries (session_id, entry_type, content, timestamp) VALUES (?, ?, ?, ?)",
                (session_id, entry_type, content, time.time()),
            )
            # SC-02: forward a TIP-shaped row to any installed observer.
            # Schema is companion-journal-row (registry
            # schemas/tip/companion-journal-row.schema.json).
            try:
                from datetime import datetime as _dt
                from datetime import timezone as _tz

                from tokenpak.core.contracts import (
                    tip_version as _tip_version,
                )
                from tokenpak.services.diagnostics import (
                    conformance as _conformance,
                )
                _conformance.notify_companion_journal_row({
                    "session_id": session_id,
                    "timestamp": _dt.now(_tz.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "tip_version": _tip_version.CURRENT,
                    "entry_type": entry_type,
                    "source": "companion.journal.store",
                    "note": content if content else None,
                })
            except Exception:
                pass
            return cur.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_entries(
        self,
        session_id: Optional[str] = None,
        entry_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Fetch journal entries, optionally filtered.

        Args:
            session_id: Restrict to this session.  ``None`` returns entries
                from all sessions.
            entry_type: Restrict to this entry type.  ``None`` returns all
                types.
            limit: Maximum number of rows to return (most recent first).

        Returns:
            List of dicts with keys ``id``, ``session_id``, ``entry_type``,
            ``content``, ``timestamp``.
        """
        self.ensure_schema()
        conditions: List[str] = []
        params: List[Any] = []

        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if entry_type is not None:
            conditions.append("entry_type = ?")
            params.append(entry_type)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT id, session_id, entry_type, content, timestamp FROM entries {where} ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
