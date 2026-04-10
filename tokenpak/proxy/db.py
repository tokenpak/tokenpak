"""
tokenpak/proxy/db.py — DB schema helpers for Claude Code gateway additions.

Exposes:
  ensure_schema(conn)        — idempotent migration: session_id on requests +
                               mutation_audit table creation
  insert_mutation_audit(...) — insert a row into mutation_audit
  MUTATION_AUDIT_COLUMNS     — tuple of column names for verification

These are the Wave-1 / CCG-02 additions that underpin per-session telemetry
(CCG-03) and the mutation audit write path (CCG-06).
"""

import sqlite3
from typing import Optional

# Column order for mutation_audit (mirrors CREATE TABLE below)
MUTATION_AUDIT_COLUMNS: tuple[str, ...] = (
    "id",
    "timestamp",
    "session_id",
    "request_id",
    "mutation_type",
    "file_path",
    "diff_summary",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply CCG-02 schema changes idempotently.

    Safe to call on:
    - A brand-new empty database (creates everything from scratch).
    - An existing database that already has the requests table without
      session_id (adds the column without touching existing rows).
    - A database that already has all CCG-02 changes (no-ops).

    Callers are responsible for committing after this returns if they want
    the changes written to disk (the function does not commit).
    """
    # ── requests: add session_id column ──────────────────────────────────
    # CREATE TABLE IF NOT EXISTS is handled by Monitor._init_db; we only
    # ensure the column exists here (additive ALTER TABLE is idempotent via
    # the try/except pattern established by the existing migration helpers).
    try:
        conn.execute(
            "ALTER TABLE requests ADD COLUMN session_id TEXT"
        )
    except sqlite3.OperationalError:
        # Column already exists — expected on any DB that has run this before
        pass

    # ── mutation_audit table ──────────────────────────────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mutation_audit (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            session_id    TEXT,
            request_id    TEXT,
            mutation_type TEXT,
            file_path     TEXT,
            diff_summary  TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ma_session_id  ON mutation_audit(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ma_request_id  ON mutation_audit(request_id)"
    )


def insert_mutation_audit(
    conn: sqlite3.Connection,
    *,
    timestamp: str,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    mutation_type: Optional[str] = None,
    file_path: Optional[str] = None,
    diff_summary: Optional[str] = None,
) -> int:
    """Insert one row into mutation_audit; return the new rowid."""
    cur = conn.execute(
        """
        INSERT INTO mutation_audit
            (timestamp, session_id, request_id, mutation_type, file_path, diff_summary)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (timestamp, session_id, request_id, mutation_type, file_path, diff_summary),
    )
    return cur.lastrowid
