"""TokenPak Agent Telemetry Replay Store — Phase 1 implementation.

Captures request/response metadata so sessions can be replayed with
a different model or settings (tokenpak replay list/show/<id>).

Usage::

    store = ReplayStore("~/.tokenpak/replay.db")
    store.capture(ReplayEntry(...))
    entries = store.list(limit=20)
    entry = store.get("abc123")
    store.delete("abc123")
    store.prune(days=7)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tp_replay (
    replay_id       TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    input_tokens_raw    INTEGER NOT NULL DEFAULT 0,
    input_tokens_sent   INTEGER NOT NULL DEFAULT 0,
    tokens_saved        INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0.0,
    messages_json   TEXT,
    response_json   TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);
"""


@dataclass
class ReplayEntry:
    """Metadata snapshot of a single proxied request for replay.

    ``messages`` and ``response`` are opt-in content fields. They are
    ``None`` by default and only populated when content capture is
    explicitly enabled.
    """

    replay_id: str
    timestamp: datetime
    provider: str
    model: str
    input_tokens_raw: int
    input_tokens_sent: int
    tokens_saved: int
    cost_usd: float = 0.0
    # Opt-in content capture — None means "not captured"
    messages: Optional[list] = None
    response: Optional[dict] = None
    metadata: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Constructors / serialisation
    # ------------------------------------------------------------------

    @classmethod
    def new(
        cls,
        provider: str,
        model: str,
        input_tokens_raw: int,
        input_tokens_sent: int,
        tokens_saved: int,
        cost_usd: float = 0.0,
        messages: Optional[list] = None,
        response: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> "ReplayEntry":
        """Create a new entry with a fresh UUID and current timestamp."""
        return cls(
            replay_id=str(uuid.uuid4())[:8],
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            provider=provider,
            model=model,
            input_tokens_raw=input_tokens_raw,
            input_tokens_sent=input_tokens_sent,
            tokens_saved=tokens_saved,
            cost_usd=cost_usd,
            messages=messages,
            response=response,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        return {
            "replay_id": self.replay_id,
            "timestamp": self.timestamp.isoformat(),
            "provider": self.provider,
            "model": self.model,
            "input_tokens_raw": self.input_tokens_raw,
            "input_tokens_sent": self.input_tokens_sent,
            "tokens_saved": self.tokens_saved,
            "cost_usd": self.cost_usd,
            "messages": self.messages,
            "response": self.response,
            "metadata": self.metadata,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ReplayEntry":
        messages = json.loads(row["messages_json"]) if row["messages_json"] else None
        response = json.loads(row["response_json"]) if row["response_json"] else None
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        return cls(
            replay_id=row["replay_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            provider=row["provider"],
            model=row["model"],
            input_tokens_raw=row["input_tokens_raw"],
            input_tokens_sent=row["input_tokens_sent"],
            tokens_saved=row["tokens_saved"],
            cost_usd=row["cost_usd"],
            messages=messages,
            response=response,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def savings_pct(self) -> float:
        if self.input_tokens_raw == 0:
            return 0.0
        return round(self.tokens_saved / self.input_tokens_raw * 100, 1)

    def summary_line(self) -> str:
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        has_content = "📦" if self.messages is not None else "  "
        return (
            f"{has_content} [{self.replay_id}] {ts}  "
            f"{self.provider}/{self.model}  "
            f"{self.input_tokens_raw}→{self.input_tokens_sent} tokens "
            f"(-{self.savings_pct}%)"
        )


class ReplayStore:
    """SQLite-backed store for capturing and retrieving replay entries.

    Thread-safe via per-thread connections (WAL mode).

    Args:
        db_path: Path to SQLite file.  Pass ``":memory:"`` for ephemeral
                 (useful in tests).
    """

    def __init__(self, db_path: str = ":memory:"):
        self._path = db_path
        self._local = threading.local()
        self._init_db()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.executescript(_DDL)
        conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capture(self, entry: ReplayEntry) -> None:
        """Persist a replay entry to the store."""
        conn = self._conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO tp_replay
                (replay_id, timestamp, provider, model,
                 input_tokens_raw, input_tokens_sent, tokens_saved, cost_usd,
                 messages_json, response_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.replay_id,
                entry.timestamp.isoformat(),
                entry.provider,
                entry.model,
                entry.input_tokens_raw,
                entry.input_tokens_sent,
                entry.tokens_saved,
                entry.cost_usd,
                json.dumps(entry.messages) if entry.messages is not None else None,
                json.dumps(entry.response) if entry.response is not None else None,
                json.dumps(entry.metadata),
            ),
        )
        conn.commit()

    def list(self, limit: int = 20, provider: Optional[str] = None) -> list:
        """Return recent entries, most recent first.

        Args:
            limit: Maximum entries to return.
            provider: Optional filter by provider name.
        """
        conn = self._conn()
        if provider:
            rows = conn.execute(
                "SELECT * FROM tp_replay WHERE provider=? ORDER BY timestamp DESC LIMIT ?",
                (provider, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tp_replay ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [ReplayEntry.from_row(r) for r in rows]

    def get(self, replay_id: str) -> Optional[ReplayEntry]:
        """Retrieve a single entry by id. Returns ``None`` if not found."""
        conn = self._conn()
        row = conn.execute("SELECT * FROM tp_replay WHERE replay_id=?", (replay_id,)).fetchone()
        return ReplayEntry.from_row(row) if row else None

    def delete(self, replay_id: str) -> bool:
        """Delete an entry. Returns True if a row was removed."""
        conn = self._conn()
        cursor = conn.execute("DELETE FROM tp_replay WHERE replay_id=?", (replay_id,))
        conn.commit()
        return cursor.rowcount > 0

    def prune(self, days: int = 7) -> int:
        """Delete entries older than *days* days. Returns count removed (default 7 days)."""
        conn = self._conn()
        cursor = conn.execute(
            "DELETE FROM tp_replay WHERE timestamp < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
        return cursor.rowcount

    def count(self) -> int:
        """Return total number of stored entries."""
        conn = self._conn()
        row = conn.execute("SELECT COUNT(*) AS n FROM tp_replay").fetchone()
        return row["n"] if row else 0

    def clear(self) -> int:
        """Delete ALL entries from the store. Returns count removed."""
        conn = self._conn()
        n = self.count()
        conn.execute("DELETE FROM tp_replay")
        conn.commit()
        return n

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[ReplayStore] = None
_store_path: str = ":memory:"


def get_replay_store(db_path: Optional[str] = None) -> ReplayStore:
    """Return (or initialise) the process-level singleton replay store.

    Pass *db_path* once to configure the backing file; subsequent calls
    with no argument return the same instance.
    """
    global _store, _store_path
    if db_path is not None and db_path != _store_path:
        if _store is not None:
            _store.close()
        _store = ReplayStore(db_path)
        _store_path = db_path
    if _store is None:
        _store = ReplayStore(_store_path)
    return _store
