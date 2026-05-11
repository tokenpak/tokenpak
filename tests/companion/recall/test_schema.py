# SPDX-License-Identifier: Apache-2.0
"""Schema-shape tests for the recall storage foundation.

These tests open a fresh database, apply the migrations, and assert that
the expected tables and indexes exist. They do not exercise any read or
write behaviour — that's PR 2+.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tokenpak.companion.recall import SCHEMA_VERSION, RecallStore
from tokenpak.companion.recall.schema import (
    ALL_DDL_V1,
    EXPECTED_INDEXES_V1,
    EXPECTED_TABLES_V1,
)


def _names(conn: sqlite3.Connection, kind: str) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,)).fetchall()
    return {r[0] for r in rows}


def test_schema_version_constant_matches_latest_migration() -> None:
    """``SCHEMA_VERSION`` constant must match the head of ``MIGRATIONS``."""
    from tokenpak.companion.recall.migrations import MIGRATIONS

    assert MIGRATIONS, "at least one migration must be registered"
    assert MIGRATIONS[-1].version == SCHEMA_VERSION


def test_open_creates_all_v1_tables(tmp_path: Path, require_fts5: None) -> None:
    """Opening a fresh DB applies v1 and produces all expected tables."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        tables = _names(store.conn, "table") | _names(store.conn, "view")
        # FTS5 backs its virtual table with shadow tables — check for the
        # main name only (the virtual table is not type='table'; it's
        # registered under sqlite_master as type='table' with a synthetic
        # row, but a portable check is the SELECT below.)
        present = {
            r[0]
            for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('schema_version','paks','paks_fts','pak_anchors','pak_relations')"
            ).fetchall()
        }
    assert EXPECTED_TABLES_V1.issubset(present)


def test_open_creates_all_v1_indexes(tmp_path: Path, require_fts5: None) -> None:
    """All named v1 indexes exist after open."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        indexes = _names(store.conn, "index")
    missing = EXPECTED_INDEXES_V1 - indexes
    assert not missing, f"missing indexes: {sorted(missing)}"


def test_ddl_statements_are_individually_idempotent(tmp_path: Path, require_fts5: None) -> None:
    """Each statement uses IF NOT EXISTS so re-applying is safe."""
    db_path = tmp_path / "recall.db"
    conn = sqlite3.connect(str(db_path))
    try:
        for stmt in ALL_DDL_V1:
            conn.execute(stmt)
        # Second pass: every statement should still succeed.
        for stmt in ALL_DDL_V1:
            conn.execute(stmt)
    finally:
        conn.close()


def test_paks_table_columns_match_contract(tmp_path: Path, require_fts5: None) -> None:
    """The ``paks`` columns are the contract Pro daemon reads; lock them down."""
    db_path = tmp_path / "recall.db"
    expected = {
        "pak_id",
        "pak_type",
        "project",
        "topic",
        "source_type",
        "authority",
        "title",
        "summary",
        "content_hash",
        "created_at",
        "updated_at",
        "superseded_by",
    }
    with RecallStore.open(db_path) as store:
        cols = {r[1] for r in store.conn.execute("PRAGMA table_info('paks')")}
    assert cols == expected


def test_pak_relations_supports_self_reference_via_pak_id(
    tmp_path: Path, require_fts5: None
) -> None:
    """``pak_relations`` should accept (pak_id, related_pak_id) inserts after
    parent rows exist; this is a structural smoke test only."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.conn.execute(
            "INSERT INTO paks (pak_id, pak_type, source_type, authority, "
            "title, content_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "vault://block/a",
                "vault",
                "code",
                "llm_generated",
                "A",
                "h1",
                "2026-05-11T00:00:00Z",
                "2026-05-11T00:00:00Z",
            ),
        )
        store.conn.execute(
            "INSERT INTO paks (pak_id, pak_type, source_type, authority, "
            "title, content_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "vault://block/b",
                "vault",
                "code",
                "llm_generated",
                "B",
                "h2",
                "2026-05-11T00:00:00Z",
                "2026-05-11T00:00:00Z",
            ),
        )
        store.conn.execute(
            "INSERT INTO pak_relations (pak_id, related_pak_id, relation_type, "
            "created_at) VALUES (?, ?, ?, ?)",
            (
                "vault://block/a",
                "vault://block/b",
                "supersedes",
                "2026-05-11T00:00:00Z",
            ),
        )
        store.conn.commit()
        rows = store.conn.execute(
            "SELECT pak_id, related_pak_id, relation_type FROM pak_relations"
        ).fetchall()
    assert rows == [("vault://block/a", "vault://block/b", "supersedes")]


def test_foreign_keys_pragma_is_on(tmp_path: Path, require_fts5: None) -> None:
    """FK enforcement must be ON so the schema's CASCADE / SET NULL works."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        row = store.conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_wal_journal_mode(tmp_path: Path, require_fts5: None) -> None:
    """WAL is required for concurrent-reader / single-writer behaviour."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        row = store.conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0].lower() == "wal"
