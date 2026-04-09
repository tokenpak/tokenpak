"""TokenPak Pro — Usage audit log stored in SQLite."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_DB = Path.home() / ".tokenpak" / "audit.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS usage_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    adapter   TEXT    NOT NULL,
    model     TEXT    NOT NULL,
    feature   TEXT    NOT NULL,
    metadata  TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_usage_events_ts      ON usage_events (ts);",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_adapter ON usage_events (adapter);",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_feature ON usage_events (feature);",
]


class AuditLog:
    """SQLite-backed usage audit log. No PII stored."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(_DEFAULT_DB)
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "AuditLog":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── internal helpers ─────────────────────────────────────────────────────

    def _open(self) -> None:
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            self._conn.execute(idx_sql)
        self._conn.commit()

    def _conn_or_raise(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("AuditLog is closed")
        return self._conn

    # ── public API ────────────────────────────────────────────────────────────

    def log_usage(
        self,
        adapter: str,
        model: str,
        feature: str,
        metadata: dict = None,
    ) -> None:
        """Record a feature usage event. No PII."""
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None
        conn = self._conn_or_raise()
        conn.execute(
            "INSERT INTO usage_events (ts, adapter, model, feature, metadata) VALUES (?,?,?,?,?)",
            (ts, adapter, model, feature, meta_json),
        )
        conn.commit()

    def get_feature_usage(
        self,
        feature: str = None,
        adapter: str = None,
        days: int = 7,
    ) -> list[dict]:
        """Return usage records with optional filters."""
        conn = self._conn_or_raise()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        query = "SELECT * FROM usage_events WHERE ts >= ?"
        params: list = [since]
        if feature is not None:
            query += " AND feature = ?"
            params.append(feature)
        if adapter is not None:
            query += " AND adapter = ?"
            params.append(adapter)
        query += " ORDER BY ts DESC"
        rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result

    def get_stats(
        self,
        feature: str = None,
        adapter: str = None,
    ) -> dict:
        """Return aggregate stats: counts by feature/adapter."""
        conn = self._conn_or_raise()
        query = "SELECT feature, adapter, COUNT(*) as count FROM usage_events"
        params: list = []
        clauses = []
        if feature is not None:
            clauses.append("feature = ?")
            params.append(feature)
        if adapter is not None:
            clauses.append("adapter = ?")
            params.append(adapter)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " GROUP BY feature, adapter ORDER BY count DESC"
        rows = conn.execute(query, params).fetchall()

        by_feature: dict[str, int] = {}
        by_adapter: dict[str, int] = {}
        total = 0
        breakdown = []
        for row in rows:
            f, a, c = row["feature"], row["adapter"], row["count"]
            by_feature[f] = by_feature.get(f, 0) + c
            by_adapter[a] = by_adapter.get(a, 0) + c
            total += c
            breakdown.append({"feature": f, "adapter": a, "count": c})

        return {
            "total": total,
            "by_feature": by_feature,
            "by_adapter": by_adapter,
            "breakdown": breakdown,
        }

    def export_json(self) -> str:
        """Export all records as a JSON string."""
        conn = self._conn_or_raise()
        rows = conn.execute("SELECT * FROM usage_events ORDER BY ts ASC").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("metadata"):
                try:
                    d["metadata"] = json.loads(d["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return json.dumps(result, indent=2)
