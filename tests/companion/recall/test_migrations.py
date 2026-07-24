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
    # All clean migrations apply first, then v99 errors; the version is
    # left at the latest clean migration (``SCHEMA_VERSION``), not past it.
    assert before == 0
    assert after == SCHEMA_VERSION
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


# ----- v1 → v2 upgrade coverage --------------------------------------------


def test_v2_applies_on_top_of_v1_only(tmp_path: Path, require_fts5: None) -> None:
    """A DB seeded at v1 advances to v2 without re-running v1's DDL."""
    db_path = tmp_path / "recall.db"

    # Hand-roll a v1 database by applying only the first migration.
    conn = sqlite3.connect(str(db_path))
    try:
        only_v1 = MIGRATIONS[0]
        conn.execute("BEGIN")
        for stmt in only_v1.statements:
            conn.execute(stmt)
        # Persist the v1 schema_version row by hand so the runner doesn't
        # start from 0 the next time around.
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (id, version, applied_at) VALUES (1, 1, ?)",
            ("2026-05-11T00:00:00Z",),
        )
        conn.execute("COMMIT")
        # Smoke-check the v1 state has no v2 triggers yet.
        trig_before = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "paks_ai_fts" not in trig_before

    # Now open via RecallStore: the runner should apply v2 only.
    with RecallStore.open(db_path) as store:
        assert store.schema_version == SCHEMA_VERSION  # i.e. 2
        trig_after = {
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    assert {"paks_ai_fts", "paks_au_fts", "paks_ad_fts"}.issubset(trig_after)


def test_v2_is_idempotent_on_already_v2_db(tmp_path: Path, require_fts5: None) -> None:
    """Re-opening a v2 DB does not re-run v2 (and does not error)."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as first:
        assert first.schema_version == SCHEMA_VERSION
    with RecallStore.open(db_path) as second:
        assert second.schema_version == SCHEMA_VERSION
        trig = {
            r[0]
            for r in second.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
    assert {"paks_ai_fts", "paks_au_fts", "paks_ad_fts"}.issubset(trig)


def test_schema_version_constant_is_v3() -> None:
    """``SCHEMA_VERSION`` should track the v3 migration head."""
    assert SCHEMA_VERSION == 3
    assert MIGRATIONS[-1].version == 3
    assert MIGRATIONS[-1].name == "pak_reason_codes_and_risk_flags"


# ----- v2 → v3 upgrade coverage --------------------------------------------


def test_v3_applies_on_top_of_v2_only(tmp_path: Path, require_fts5: None) -> None:
    """A DB seeded at v2 advances to v3 without re-running v1/v2 DDL.

    Verifies the Std 32 §5.4 / §5.5 addendum: the v3 migration adds two
    join tables (``pak_reason_codes`` + ``pak_risk_flags``) and three
    indexes; nothing else changes. Critically, no column is added to
    ``paks`` — that boundary is enforced by PR 2's no-Pro-leakage rule.
    """
    db_path = tmp_path / "recall.db"

    # Hand-roll a v2 database by applying v1 + v2 only.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN")
        for mig in MIGRATIONS:
            if mig.version > 2:
                break
            for stmt in mig.statements:
                conn.execute(stmt)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (id, version, applied_at) VALUES (1, 2, ?)",
            ("2026-05-11T00:00:00Z",),
        )
        conn.execute("COMMIT")
        # Snapshot the v2-era ``paks`` schema to assert it is byte-stable
        # across the v3 migration.
        paks_columns_before = [r[1] for r in conn.execute("PRAGMA table_info(paks)").fetchall()]
        # Smoke-check the v2 state has no v3 tables yet.
        tables_before = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
    finally:
        conn.close()
    assert "pak_reason_codes" not in tables_before
    assert "pak_risk_flags" not in tables_before

    # Open via RecallStore: the runner applies v3 only.
    with RecallStore.open(db_path) as store:
        assert store.schema_version == SCHEMA_VERSION  # i.e. 3
        tables_after = {
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        indexes_after = {
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        paks_columns_after = [
            r[1] for r in store.conn.execute("PRAGMA table_info(paks)").fetchall()
        ]
    assert {"pak_reason_codes", "pak_risk_flags"}.issubset(tables_after)
    assert {
        "idx_pak_reason_codes_code",
        "idx_pak_risk_flags_flag",
        "idx_pak_risk_flags_severity",
    }.issubset(indexes_after)
    # ``paks`` is unchanged — addendum's no-new-column rule.
    assert paks_columns_after == paks_columns_before


def test_v3_is_idempotent_on_already_v3_db(tmp_path: Path, require_fts5: None) -> None:
    """Re-opening a v3 DB does not re-run v3 (and does not error)."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as first:
        assert first.schema_version == SCHEMA_VERSION
    with RecallStore.open(db_path) as second:
        assert second.schema_version == SCHEMA_VERSION
        tables = {
            r[0]
            for r in second.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"pak_reason_codes", "pak_risk_flags"}.issubset(tables)
