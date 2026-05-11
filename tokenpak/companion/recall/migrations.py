# SPDX-License-Identifier: Apache-2.0
"""Forward-only versioned migration runner for the recall store.

Contract
--------
- Migrations are forward-only. There is no down/revert path; rolling back
  is the user's git-history problem, not a runtime feature.
- The runner is idempotent: re-running against an already-current database
  is a no-op.
- Each migration runs inside a transaction; a failure mid-migration leaves
  the database at the previous version.
- The ``schema_version`` table is the source of truth. A missing version
  row is treated as version 0 (fresh database) and recovered automatically.

Adding a future migration
-------------------------
Append a new ``Migration`` to ``MIGRATIONS`` with a version one greater
than the prior entry. Do not edit a published migration once it has shipped.

The PR-1 schema is the union of statements in
``tokenpak.companion.recall.schema.ALL_DDL_V1``; later PRs will add
new migrations (e.g. promotion-candidate counters, embedding column,
write-side FTS triggers) as additional ``Migration`` entries.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import NamedTuple

from tokenpak.companion.recall.schema import ALL_DDL_V1, SCHEMA_VERSION


class Migration(NamedTuple):
    """One forward-only schema migration.

    ``statements`` is a tuple of SQL strings that are executed in order
    inside a single transaction with the version bump.
    """

    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="initial_schema",
        statements=ALL_DDL_V1,
    ),
)
"""Ordered tuple of all known migrations. New entries APPEND; never edit
or reorder a published migration."""


# Internal: separated so tests can stub ``_now_iso8601`` deterministically.
def _now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_version_row(conn: sqlite3.Connection) -> int:
    """Make sure ``schema_version`` has the singleton row; return current version.

    A fresh database (no rows) is initialised at version 0 so the runner
    can apply v1 through vN as normal.
    """
    # The table itself is created by every migration's first statement
    # (the v1 list starts with ``CREATE TABLE IF NOT EXISTS schema_version``).
    # But for a freshly-opened blank db we may need to create it here too,
    # so that the version probe doesn't error before any migration runs.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id           INTEGER PRIMARY KEY CHECK (id = 1),
            version      INTEGER NOT NULL,
            applied_at   TEXT    NOT NULL
        )
        """
    )
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (id, version, applied_at) VALUES (1, 0, ?)",
            (_now_iso8601(),),
        )
        return 0
    return int(row[0])


def current_version(conn: sqlite3.Connection) -> int:
    """Return the schema version currently applied to ``conn``.

    Safe to call on a brand-new database — returns 0 and ensures the
    ``schema_version`` row exists for future writers.
    """
    return _ensure_version_row(conn)


def apply_migrations(conn: sqlite3.Connection) -> int:
    """Apply any pending migrations and return the final schema version.

    Idempotent — calling against an up-to-date database returns immediately
    without writes. Each migration runs inside its own transaction; a
    failure leaves the database at the previous version.
    """
    # Use deferred transactions; we explicitly BEGIN/COMMIT per migration.
    conn.isolation_level = None  # autocommit so BEGIN is explicit

    version = _ensure_version_row(conn)
    last_known = MIGRATIONS[-1].version if MIGRATIONS else 0
    if version > last_known:  # someone ran a newer build, then downgraded
        return version
    if version == last_known:
        return version

    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        try:
            conn.execute("BEGIN")
            for stmt in migration.statements:
                conn.execute(stmt)
            conn.execute(
                "UPDATE schema_version SET version = ?, applied_at = ? WHERE id = 1",
                (migration.version, _now_iso8601()),
            )
            conn.execute("COMMIT")
        except Exception:
            # SQLite raises if there's nothing to roll back, so be defensive.
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        version = migration.version

    return version


__all__ = [
    "Migration",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "apply_migrations",
    "current_version",
]
