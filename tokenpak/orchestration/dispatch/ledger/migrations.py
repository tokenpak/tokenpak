"""Run Ledger schema migrations (versioned, idempotent).

Mirrors the ``tokenpak/proxy/monitor.py`` migration idiom: a numbered ladder of
``CREATE TABLE IF NOT EXISTS`` / ``ALTER TABLE ADD COLUMN`` steps applied in
order, each guarded so a re-run is a no-op. Schema version is tracked via
SQLite's ``PRAGMA user_version`` (a single integer stored in the database
header — no bookkeeping table required).

Every Dispatch record class gets one table. Each
table stores the record's identity / index columns as typed SQLite columns plus
a ``payload`` ``TEXT`` column holding the full ``model.model_dump_json()`` blob
(acceptance criterion 8). The Run Ledger stores Dispatch **execution records
only** — it never promotes a record to a canonical Pak type (criterion 7).

The migration ladder is the single source of truth for the on-disk schema.
``migrate(conn)`` is the only public entry point; it is safe to call on every
connection open (idempotent), and it advances ``user_version`` from whatever it
finds to :data:`SCHEMA_VERSION`.
"""

from __future__ import annotations

import sqlite3

# Current target schema version. Bump this and append a migration function to
# ``_MIGRATIONS`` when the schema changes; never edit a landed migration.
SCHEMA_VERSION = 1


def get_current_schema_version(conn: sqlite3.Connection) -> int:
    """Return the database's recorded schema version (``PRAGMA user_version``)."""

    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Stamp ``PRAGMA user_version`` (cannot be parameterized — version is int)."""

    conn.execute(f"PRAGMA user_version = {int(version)}")


# ---------------------------------------------------------------------------
# Migration v0 -> v1: create the ten Dispatch record tables.
# ---------------------------------------------------------------------------
#
# Each table carries:
#   * ``id``        — the record's "<prefix>_<ulid>" primary key
#   * indexed identity/foreign-key columns lifted from the record's fields
#     (job_id / run_id / station_run_id / status / created_at-style fields),
#     kept faithful to each record's identity per acceptance criterion 8
#   * ``payload``   — the full model_dump_json() blob (faithful round-trip)
#
# The DDL is split into per-table statements so a partial create on an older DB
# is filled in idempotently (CREATE TABLE IF NOT EXISTS) on the next open.

_V1_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS dispatch_jobs (
        id TEXT PRIMARY KEY,
        source_task_packet_id TEXT,
        detected_intent TEXT,
        autonomy_mode TEXT,
        status TEXT,
        created_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_manifests (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        route_id TEXT,
        status TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_routes (
        id TEXT PRIMARY KEY,
        name TEXT,
        default_risk TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_runs (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        manifest_id TEXT,
        route_id TEXT,
        status TEXT,
        started_at TEXT,
        ended_at TEXT,
        receipt_id TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_station_runs (
        id TEXT PRIMARY KEY,
        run_id TEXT,
        station_id TEXT,
        worker_id TEXT,
        status TEXT,
        attempt_number INTEGER,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_decisions (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        scope TEXT,
        status TEXT,
        risk_level TEXT,
        created_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_artifacts (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        station_run_id TEXT,
        kind TEXT,
        content_hash TEXT,
        created_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_receipts (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        run_id TEXT,
        route_id TEXT,
        final_status TEXT,
        created_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispatch_effects (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        station_run_id TEXT,
        tool_name TEXT,
        target_type TEXT,
        target TEXT,
        status TEXT,
        created_at TEXT,
        finalized_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS late_results (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        station_run_id TEXT,
        received_at TEXT,
        payload TEXT NOT NULL
    )
    """,
)

# Helpful secondary indexes for the foreign-key columns the Run Ledger queries
# by (run_id / station_run_id / job_id / status). All ``IF NOT EXISTS``.
_V1_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_manifests_job ON dispatch_manifests(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_job ON dispatch_runs(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_station_runs_run ON dispatch_station_runs(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_job ON dispatch_decisions(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_job ON dispatch_artifacts(job_id)",
    "CREATE INDEX IF NOT EXISTS idx_receipts_run ON dispatch_receipts(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_effects_station_run ON dispatch_effects(station_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_effects_status ON dispatch_effects(status)",
    "CREATE INDEX IF NOT EXISTS idx_late_results_job ON late_results(job_id)",
)


def _migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """Create the ten Dispatch record tables + their indexes (idempotent)."""

    for stmt in _V1_TABLES:
        conn.execute(stmt)
    for stmt in _V1_INDEXES:
        conn.execute(stmt)


# Ordered migration ladder: (target_version, migration_fn). Append-only.
_MIGRATIONS: tuple[tuple[int, "object"], ...] = (
    (1, _migrate_v0_to_v1),
)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every pending migration in order; return the resulting version.

    Idempotent: a database already at :data:`SCHEMA_VERSION` is untouched
    (the loop body runs no statements). The whole upgrade runs inside a single
    transaction so a failed migration rolls back cleanly and leaves the
    recorded ``user_version`` unchanged.
    """

    current = get_current_schema_version(conn)
    if current >= SCHEMA_VERSION:
        return current

    try:
        for target_version, migration_fn in _MIGRATIONS:
            if current < target_version:
                migration_fn(conn)  # type: ignore[operator]
                _set_schema_version(conn, target_version)
                current = target_version
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return current


__all__ = [
    "SCHEMA_VERSION",
    "get_current_schema_version",
    "migrate",
]
