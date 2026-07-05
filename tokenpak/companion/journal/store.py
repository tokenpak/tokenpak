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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .. import _sqlite as _db


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
        self._write_lock = threading.RLock()
        self._write_conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        # Canonical schema lives in companion._sqlite — shared with the
        # pre-send hook so there is exactly one DDL for these tables.
        _db.ensure_journal_schema(conn)
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        """Open the journal DB via the shared companion connection factory
        (busy_timeout is applied before the WAL switch there, so concurrent
        first-openers wait for the conversion instead of failing)."""
        return _db.connect(self._db_path, check_same_thread=False)

    def _writer(self) -> sqlite3.Connection:
        if self._write_conn is None:
            self._write_conn = self._connect()
        return self._write_conn

    def start_session(
        self,
        session_id: str,
        project_dir: str = "",
        model: str = "",
    ) -> None:
        """Record a new session start.

        Re-entry safe: a duplicate start event (resume, retried hook) must
        not wipe accumulated totals, so this is INSERT-or-keep rather than
        INSERT OR REPLACE. Only the descriptive fields refresh, and only
        when the caller actually provided them.
        """
        with self._write_lock:
            conn = self._writer()
            conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (session_id, started_at, project_dir, model)
                   VALUES (?, ?, ?, ?)""",
                (session_id, time.time(), project_dir, model),
            )
            conn.execute(
                """UPDATE sessions
                   SET project_dir = COALESCE(NULLIF(?, ''), project_dir),
                       model = COALESCE(NULLIF(?, ''), model)
                   WHERE session_id = ?""",
                (project_dir, model, session_id),
            )
            conn.commit()

    def end_session(self, session_id: str) -> None:
        """Record session end and update totals."""
        with self._write_lock:
            conn = self._writer()
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                (time.time(), session_id),
            )
            conn.commit()

    def add_entry(
        self,
        session_id: str,
        entry_type: str,
        content: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append a journal entry to a session.

        Idempotent per event: rows carry a content hash under a UNIQUE
        index, so a duplicate delivery of the same (type, content,
        metadata) event within a session collapses to one row.
        """
        import json
        metadata_json = json.dumps(metadata or {}, default=str)
        with self._write_lock:
            conn = self._writer()
            conn.execute(
                """INSERT OR IGNORE INTO entries
                   (session_id, timestamp, entry_type, content, metadata_json, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, time.time(), entry_type, content, metadata_json,
                 _db.entry_content_hash(entry_type, content, metadata_json)),
            )
            conn.commit()

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        """Retrieve a session record."""
        conn = self._connect()
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
        conn = self._connect()
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

    def record_savings(
        self,
        session_id: str,
        tool: str,
        tokens_avoided: int,
        cost_avoided_usd: float,
        model_hint: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Persist a companion-layer savings event (prompt-side, pre-wire).

        Savings here are tokens that never reached the provider because a
        companion tool (prune_context, load_capsule, …) replaced or compressed
        content before it was sent. Keep this platform-agnostic: `tool` is a
        free-form identifier, not an enum.
        """
        meta = {
            "tool": tool,
            "tokens_avoided": int(max(0, tokens_avoided)),
            "cost_avoided_usd": float(max(0.0, cost_avoided_usd)),
        }
        if model_hint:
            meta["model_hint"] = model_hint
        if extra:
            meta.update(extra)
        self.add_entry(
            session_id=session_id,
            entry_type="companion_savings",
            content=f"{tool}: -{meta['tokens_avoided']:,} tokens (~${meta['cost_avoided_usd']:.4f})",
            metadata=meta,
        )

    def session_savings(self, session_id: str) -> dict[str, Any]:
        """Aggregate companion savings for a session.

        Returns: {tokens_avoided, cost_avoided_usd, by_tool: {tool: {...}}}
        Reads from entries table; no caching — caller decides frequency.
        """
        import json
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT metadata_json FROM entries "
                "WHERE session_id = ? AND entry_type = 'companion_savings'",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
        total_tokens = 0
        total_cost = 0.0
        by_tool: dict[str, dict[str, Any]] = {}
        for (meta_json,) in rows:
            try:
                m = json.loads(meta_json or "{}")
            except Exception:
                continue
            tool = m.get("tool", "unknown")
            tok = int(m.get("tokens_avoided", 0) or 0)
            cost = float(m.get("cost_avoided_usd", 0.0) or 0.0)
            total_tokens += tok
            total_cost += cost
            bucket = by_tool.setdefault(tool, {"tokens_avoided": 0, "cost_avoided_usd": 0.0, "events": 0})
            bucket["tokens_avoided"] += tok
            bucket["cost_avoided_usd"] += cost
            bucket["events"] += 1
        return {
            "tokens_avoided": total_tokens,
            "cost_avoided_usd": round(total_cost, 6),
            "by_tool": by_tool,
        }

    def recent_sessions(self, limit: int = 10) -> list[SessionRecord]:
        """List recent sessions, newest first."""
        conn = self._connect()
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
