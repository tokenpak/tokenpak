# SPDX-License-Identifier: Apache-2.0
"""FTS5 virtual-table availability and basic MATCH behaviour.

PR 1 leaves the FTS5 table empty by default — no triggers, no writer.
These tests insert directly into ``paks_fts`` to validate the table
shape and tokenizer, not to lock in any insert-side contract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tokenpak.companion.recall import RecallStore


def test_fts5_extension_detected(fts5_available: bool) -> None:
    """The fixture must classify the build correctly.

    This test runs even when FTS5 is missing — it's the signal-test that
    confirms the skip path works.
    """
    if not fts5_available:
        pytest.skip("FTS5 not compiled — confirmed; remaining tests will skip.")
    # If we get here, FTS5 is present. Sanity-check the probe.
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    finally:
        conn.close()


def test_paks_fts_exists_after_open(tmp_path: Path, require_fts5: None) -> None:
    """``paks_fts`` is registered as an fts5 virtual table after open."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        row = store.conn.execute("SELECT sql FROM sqlite_master WHERE name = 'paks_fts'").fetchone()
    assert row is not None
    assert "fts5" in row[0].lower()


def test_match_returns_inserted_row(tmp_path: Path, require_fts5: None) -> None:
    """Direct INSERT into paks_fts then MATCH returns the row."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.conn.execute(
            "INSERT INTO paks_fts (pak_id, title, summary) VALUES (?, ?, ?)",
            ("vault://a", "router-not-vault credential architecture", ""),
        )
        store.conn.execute(
            "INSERT INTO paks_fts (pak_id, title, summary) VALUES (?, ?, ?)",
            ("vault://b", "unrelated heading", "totally different content"),
        )
        store.conn.commit()
        hit = store.conn.execute(
            "SELECT pak_id FROM paks_fts WHERE paks_fts MATCH 'credential'"
        ).fetchall()
    assert [r[0] for r in hit] == ["vault://a"]


def test_unicode61_remove_diacritics_tokenizer(tmp_path: Path, require_fts5: None) -> None:
    """The DDL specifies ``unicode61 remove_diacritics 2`` — confirm it's live."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        store.conn.execute(
            "INSERT INTO paks_fts (pak_id, title, summary) VALUES (?, ?, ?)",
            ("vault://c", "café résumé", ""),
        )
        store.conn.commit()
        # Without diacritics-stripping, 'cafe' would not match 'café'.
        hit = store.conn.execute(
            "SELECT pak_id FROM paks_fts WHERE paks_fts MATCH 'cafe'"
        ).fetchall()
    assert [r[0] for r in hit] == ["vault://c"]


def test_empty_index_returns_no_rows(tmp_path: Path, require_fts5: None) -> None:
    """A fresh DB has zero rows in ``paks_fts``; MATCH returns nothing."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        rows = store.conn.execute(
            "SELECT pak_id FROM paks_fts WHERE paks_fts MATCH 'anything'"
        ).fetchall()
    assert rows == []
