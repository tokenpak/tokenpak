# SPDX-License-Identifier: Apache-2.0
"""``RecallStore.open`` / ``open_recall_store`` factory behaviour."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.companion.recall import (
    RecallStore,
    default_recall_db_path,
    open_recall_store,
)


def test_open_creates_parent_directory(tmp_path: Path, require_fts5: None) -> None:
    """Parent dir is created on first open if missing."""
    nested = tmp_path / "deep" / "nested" / "recall.db"
    assert not nested.parent.exists()
    with RecallStore.open(nested) as store:
        assert store.path == nested
    assert nested.exists()


def test_open_uses_default_when_path_is_none(tmp_path: Path, require_fts5: None) -> None:
    """``RecallStore.open(None)`` resolves to ``default_recall_db_path()``."""
    custom = tmp_path / "recall_default.db"
    with patch.dict(os.environ, {"TOKENPAK_RECALL_DB": str(custom)}):
        with RecallStore.open(None) as store:
            assert store.path == custom
    assert custom.exists()


def test_default_path_respects_env_var(tmp_path: Path) -> None:
    """The default path honours ``TOKENPAK_RECALL_DB``."""
    target = tmp_path / "elsewhere.db"
    with patch.dict(os.environ, {"TOKENPAK_RECALL_DB": str(target)}):
        assert default_recall_db_path() == target


def test_default_path_falls_back_to_companion_dir() -> None:
    """Without the env var the default sits under ``~/.tokenpak/companion/``."""
    env_without = {k: v for k, v in os.environ.items() if k != "TOKENPAK_RECALL_DB"}
    with patch.dict(os.environ, env_without, clear=True):
        path = default_recall_db_path()
    assert path.name == "recall.db"
    assert path.parent.name == "companion"


def test_open_recall_store_returns_recallstore_instance(tmp_path: Path, require_fts5: None) -> None:
    """The module-level wrapper returns a ``RecallStore``."""
    db_path = tmp_path / "recall.db"
    with open_recall_store(db_path) as store:
        assert isinstance(store, RecallStore)


def test_close_is_idempotent(tmp_path: Path, require_fts5: None) -> None:
    """Closing twice does not raise."""
    db_path = tmp_path / "recall.db"
    store = RecallStore.open(db_path)
    store.close()
    store.close()  # must be a no-op


def test_conn_property_exposes_underlying_connection(tmp_path: Path, require_fts5: None) -> None:
    """``store.conn`` is the actual sqlite3 connection."""
    import sqlite3

    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        assert isinstance(store.conn, sqlite3.Connection)


def test_pragmas_busy_timeout_applied(tmp_path: Path, require_fts5: None) -> None:
    """``busy_timeout`` PRAGMA is set to 5000 ms."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        row = store.conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 5000


def test_pragmas_synchronous_normal(tmp_path: Path, require_fts5: None) -> None:
    """``synchronous`` PRAGMA is NORMAL (1) under WAL."""
    db_path = tmp_path / "recall.db"
    with RecallStore.open(db_path) as store:
        row = store.conn.execute("PRAGMA synchronous").fetchone()
    # 1 == NORMAL per sqlite3 docs.
    assert row[0] == 1
