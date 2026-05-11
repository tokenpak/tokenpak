# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the recall storage foundation tests.

The FTS5 fixture checks that the linked SQLite build has the FTS5
extension compiled in. Production Debian 12 + GitHub Actions Ubuntu
both ship it; this fixture exists so the suite degrades cleanly on a
build that does not.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture(scope="session")
def fts5_available() -> bool:
    """``True`` iff SQLite has FTS5 compiled in.

    Tests that exercise the FTS5 virtual table use this to skip with a
    clear reason rather than crash with an opaque SQL error.
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
    return True


@pytest.fixture
def require_fts5(fts5_available: bool) -> None:
    """Skip the test if FTS5 is not available."""
    if not fts5_available:
        pytest.skip(
            "FTS5 extension not compiled into this SQLite build; skipping recall FTS5 coverage."
        )
