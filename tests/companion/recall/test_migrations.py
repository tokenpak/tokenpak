# SPDX-License-Identifier: Apache-2.0
"""Forward-only migration runner tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tokenpak.companion.recall import RecallStore
from tokenpak.companion.recall.migrations import (
    MIGRATIONS,
    Migration,
    apply_migrations,
    current_version,
)
from tokenpak.companion.recall.schema import SCHEMA_VERSION


def test_fresh_db_advances_to_latest(tmp_path: Path, require_fts5: None) -> None:
    """A brand-new DB ends at the latest known version."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        assert store.schema_version == SCHEMA_VERSION


def test_reopen_is_idempotent(tmp_path: Path, require_fts5: None) -> None:
    """Opening the same DB twice doesn't rerun migrations or change rows."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as first:
        first.conn.execute(
            "INSERT INTO paks (pak_id, pak_type, source_type, authority, "
            "title, content_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "vault://b",
                "vault",
                "code",
                "llm_generated",
                "T",
                "h",
                "2026-05-11T00:00:00Z",
                "2026-05-11T00:00:00Z",
            ),
        )
        first.conn.commit()
        version_after_seed = first.schema_version
    # Reopen — should not advance the version and should not lose the row.
    with RecallStore.open(db_path) as second:
        assert second.schema_version == version_after_seed
        n = second.conn.execute("SELECT COUNT(*) FROM paks").fetchone()[0]
    assert n == 1


def test_legacy_db_missing_schema_version_row_is_recovered(
    tmp_path: Path, require_fts5: None
) -> None:
    """A legacy DB with no version row is treated as v0 and migrated up."""
    db_path = tmp_path / "recall.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # Pretend an older build created the table but not the row.
        conn.execute(
            "CREATE TABLE schema_version ("
            "  id INTEGER PRIMARY KEY CHECK (id = 1), "
            "  version INTEGER NOT NULL, "
            "  applied_at TEXT NOT NULL"
            ")"
        )
        conn.commit()
    finally:
        conn.close()
    with RecallStore.open(db_path) as store:
        assert store.schema_version == SCHEMA_VERSION


def test_apply_migrations_returns_final_version(tmp_path: Path, require_fts5: None) -> None:
    """Direct ``apply_migrations`` call returns the new version number."""
    db_path = tmp_path / "recall.db"
    conn = sqlite3.connect(str(db_path))
    try:
        final = apply_migrations(conn)
    finally:
        conn.close()
    assert final == SCHEMA_VERSION


def test_migrations_are_strictly_increasing_versions() -> None:
    """The migration list must be ordered with strictly increasing versions."""
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)
    assert versions[0] == 1


def test_failure_inside_migration_does_not_advance_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    require_fts5: None,
) -> None:
    """A SQL failure mid-migration leaves ``schema_version`` at the prior value."""
    db_path = tmp_path / "recall.db"

    bad_migration = Migration(
        version=99,
        name="deliberately_broken",
        statements=("SELECT 1", "THIS IS NOT VALID SQL"),
    )
    # Replace MIGRATIONS for this test only, keeping the v1 baseline first.
    monkeypatch.setattr(
        "tokenpak.companion.recall.migrations.MIGRATIONS",
        (*MIGRATIONS, bad_migration),
    )
    conn = sqlite3.connect(str(db_path))
    try:
        # First call gets us to v1 cleanly (the bad migration is v99 only).
        from tokenpak.companion.recall import migrations as mig

        before = current_version(conn)
        with pytest.raises(sqlite3.Error):
            mig.apply_migrations(conn)
        after = current_version(conn)
    finally:
        conn.close()
    # The bad migration should NOT have advanced the version past v1.
    assert before == 0
    assert after == SCHEMA_VERSION  # v1 applied before v99 errored
    assert after < 99


def test_db_at_newer_version_than_code_is_left_alone(tmp_path: Path, require_fts5: None) -> None:
    """If someone ran a newer build, an older build must not regress them."""
    db_path = tmp_path / "recall.db"
    # Initialise normally first, then bump the version artificially.
    with RecallStore.open(db_path) as store:
        store.conn.execute(
            "UPDATE schema_version SET version = ?, applied_at = ? WHERE id = 1",
            (999, "2099-01-01T00:00:00Z"),
        )
        store.conn.commit()
    # Re-open with the current (older) code — must not regress.
    with RecallStore.open(db_path) as store:
        assert store.schema_version == 999
