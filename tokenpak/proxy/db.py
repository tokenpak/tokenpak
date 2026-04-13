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
    "request_id",
    "session_id",
    "timestamp",
    "pre_hash",
    "post_hash",
    "rules_applied",
    "cache_risk",
    "rollback_possible",
    "mode",
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

    # ── mutation_audit table (CCG-06 10-column schema) ────────────────────
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mutation_audit (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id        INTEGER,
            session_id        TEXT,
            timestamp         TEXT    NOT NULL,
            pre_hash          TEXT,
            post_hash         TEXT,
            rules_applied     TEXT,
            cache_risk        TEXT,
            rollback_possible INTEGER,
            mode              TEXT
        )
        """
    )
    # Migration: add new columns to existing tables that have the old schema
    for col_def in (
        "pre_hash TEXT",
        "post_hash TEXT",
        "rules_applied TEXT",
        "cache_risk TEXT",
        "rollback_possible INTEGER",
        "mode TEXT",
    ):
        try:
            conn.execute(f"ALTER TABLE mutation_audit ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ma_session_id  ON mutation_audit(session_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ma_request_id  ON mutation_audit(request_id)"
    )


def insert_mutation_audit(
    conn: sqlite3.Connection,
    *,
    request_id: Optional[int] = None,
    session_id: Optional[str] = None,
    timestamp: str,
    pre_hash: Optional[str] = None,
    post_hash: Optional[str] = None,
    rules_applied: Optional[str] = None,
    cache_risk: Optional[str] = None,
    rollback_possible: Optional[int] = None,
    mode: Optional[str] = None,
) -> int:
    """Insert one row into mutation_audit; return the new rowid."""
    cur = conn.execute(
        """
        INSERT INTO mutation_audit
            (request_id, session_id, timestamp, pre_hash, post_hash,
             rules_applied, cache_risk, rollback_possible, mode)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (request_id, session_id, timestamp, pre_hash, post_hash,
         rules_applied, cache_risk, rollback_possible, mode),
    )
    return cur.lastrowid
