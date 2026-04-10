"""TokenPak Pro — Usage audit log stored in SQLite.

This module provides two classes:

* ``AuditLog`` — the original feature-usage event log stored in ``audit.db``
  (schema: ts, adapter, model, feature, metadata).  Kept intact for backward compat.

* ``ProxyAuditLog`` — the compliance-grade request audit log stored in
  ``monitor.db`` (the proxy's shared database).  Added by TRIX-08.
  Schema: 9 compliance columns + original 5, all new columns nullable so old rows
  remain valid.

Schema migration (TRIX-08 / AC-2.3)
------------------------------------
``ProxyAuditLog`` creates ``audit_events`` in ``monitor.db`` if it does not exist, then
applies additive ALTER TABLE statements for each new column, catching
``OperationalError`` if the column is already present.  A ``schema_version`` row
(version=5) is written after the migration.  Running the migration on an
already-migrated DB is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Original AuditLog (unchanged — backward compat)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ProxyAuditLog — compliance audit log in monitor.db (TRIX-08 / AC-2.3)
# ---------------------------------------------------------------------------

# Schema version written to monitor.db.schema_version after TRIX-08 migration.
_AUDIT_SCHEMA_VERSION = 5
_AUDIT_SCHEMA_DESC = "TRIX-08: audit_events table + 9 compliance columns"

_CREATE_AUDIT_EVENTS = """
CREATE TABLE IF NOT EXISTS audit_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                    TEXT    NOT NULL,
    adapter               TEXT,
    model                 TEXT,
    feature               TEXT,
    metadata              TEXT,
    user_id               TEXT,
    client_ip             TEXT,
    request_id            TEXT,
    endpoint              TEXT,
    tokens_in             INTEGER,
    tokens_out            INTEGER,
    cost_usd              REAL,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER
);
"""

# Additive columns — applied with try/except so migration is idempotent
_AUDIT_EVENTS_NEW_COLUMNS: list[tuple[str, str]] = [
    ("user_id",               "TEXT"),
    ("client_ip",             "TEXT"),
    ("request_id",            "TEXT"),
    ("endpoint",              "TEXT"),
    ("tokens_in",             "INTEGER"),
    ("tokens_out",            "INTEGER"),
    ("cost_usd",              "REAL"),
    ("cache_read_tokens",     "INTEGER"),
    ("cache_creation_tokens", "INTEGER"),
]

_AUDIT_EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_events_ts         ON audit_events (ts);",
    "CREATE INDEX IF NOT EXISTS idx_audit_events_request_id ON audit_events (request_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_events_user_id    ON audit_events (user_id);",
    "CREATE INDEX IF NOT EXISTS idx_audit_events_client_ip  ON audit_events (client_ip);",
]


def _migrate_audit_events(conn: sqlite3.Connection) -> None:
    """Idempotent migration: create audit_events + add 9 compliance columns.

    Safe to call on an already-migrated database — every ALTER TABLE is wrapped
    in a try/except that swallows OperationalError("duplicate column name").
    """
    # Check if already at this version
    try:
        row = conn.execute(
            "SELECT version FROM schema_version WHERE version = ?",
            (_AUDIT_SCHEMA_VERSION,),
        ).fetchone()
        if row is not None:
            return  # already applied
    except sqlite3.OperationalError:
        pass  # schema_version table may not exist yet — continue

    # Ensure schema_version table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL,
            description TEXT
        )
    """)

    # Create audit_events (no-op if already exists)
    conn.execute(_CREATE_AUDIT_EVENTS)

    # Additive ALTER TABLE for each compliance column
    for col_name, col_type in _AUDIT_EVENTS_NEW_COLUMNS:
        try:
            conn.execute(
                f"ALTER TABLE audit_events ADD COLUMN {col_name} {col_type}"
            )
        except sqlite3.OperationalError:
            pass  # column already present — idempotent

    # Create indexes
    for idx_sql in _AUDIT_EVENTS_INDEXES:
        conn.execute(idx_sql)

    # Record migration
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, applied_at, description) VALUES (?, ?, ?)",
        (_AUDIT_SCHEMA_VERSION, datetime.now(timezone.utc).isoformat(), _AUDIT_SCHEMA_DESC),
    )
    conn.commit()


def _hash_user_id(raw_token: str) -> Optional[str]:
    """Return a stable SHA-256 hex identifier for a raw auth token.

    The raw token is NEVER stored.  Returns None for empty/None input.
    """
    if not raw_token:
        return None
    return hashlib.sha256(raw_token.encode()).hexdigest()[:16]


class ProxyAuditLog:
    """Compliance-grade per-request audit log stored in monitor.db.

    Thread-safe: uses a per-instance lock around every write.  Designed to be
    used as a module-level singleton from proxy.py — ``get_instance()`` lazily
    initialises one instance pointing at ``monitor.db``.
    """

    _singleton: Optional["ProxyAuditLog"] = None
    _singleton_lock: threading.Lock = threading.Lock()

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            _migrate_audit_events(conn)
        finally:
            conn.close()

    @classmethod
    def get_instance(cls, db_path: str) -> "ProxyAuditLog":
        """Return the module-level singleton, creating it if needed."""
        if cls._singleton is None:
            with cls._singleton_lock:
                if cls._singleton is None:
                    cls._singleton = cls(db_path)
        return cls._singleton

    # ── write path ────────────────────────────────────────────────────────────

    def write_event(
        self,
        *,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        client_ip: Optional[str] = None,
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        cost_usd: Optional[float] = None,
        cache_read_tokens: Optional[int] = None,
        cache_creation_tokens: Optional[int] = None,
        adapter: Optional[str] = None,
        feature: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Insert one audit_events row.  Fails silently — never raises."""
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None
        try:
            with self._lock:
                conn = sqlite3.connect(self._db_path)
                try:
                    conn.execute(
                        """
                        INSERT INTO audit_events
                          (ts, adapter, model, feature, metadata,
                           user_id, client_ip, request_id, endpoint,
                           tokens_in, tokens_out, cost_usd,
                           cache_read_tokens, cache_creation_tokens)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            ts,
                            adapter,
                            model,
                            feature,
                            meta_json,
                            user_id,
                            client_ip,
                            request_id,
                            endpoint,
                            tokens_in,
                            tokens_out,
                            cost_usd,
                            cache_read_tokens,
                            cache_creation_tokens,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass  # fail-open: never break a request over audit write

    # ── query path ────────────────────────────────────────────────────────────

    def query(
        self,
        since: Optional[str] = None,
        user: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query audit_events with optional filters.

        Args:
            since: ISO date/datetime string (inclusive lower bound on ``ts``).
            user:  Partial or full ``user_id`` filter (substring match).
            limit: Maximum rows returned.

        Returns:
            List of dicts, most-recent first.
        """
        sql = "SELECT * FROM audit_events WHERE 1=1"
        params: list = []
        if since:
            sql += " AND ts >= ?"
            params.append(since)
        if user:
            sql += " AND user_id LIKE ?"
            params.append(f"%{user}%")
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, params).fetchall()
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
            finally:
                conn.close()
        except Exception:
            return []
