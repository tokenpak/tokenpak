"""Regression test for the additive reasoning-usage + stream-mode
monitor.db columns introduced by the Provider-Native Compatibility
Foundation initiative.

The columns are additively added via ALTER TABLE in
``Monitor._init_db``. This test verifies:

1. A fresh database created by Monitor has all expected columns.
2. An existing database missing the columns gains them on
   Monitor instantiation (idempotent migration).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from tokenpak.proxy.monitor import Monitor

EXPECTED_REASONING_COLUMNS = {
    "reasoning_tokens",
    "visible_output_tokens",
    "total_billable_tokens",
    "reasoning_effort",
    "reasoning_usage_source",
    "provider_usage_ref",
}

EXPECTED_STREAM_COLUMNS = {
    "stream_mode",
    "event_transform_applied",
}


def _columns(db_path: Path) -> set:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("PRAGMA table_info(requests)")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def test_fresh_monitor_db_has_reasoning_and_stream_columns():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "monitor.db"
        Monitor(db_path=str(db_path))
        cols = _columns(db_path)
        assert EXPECTED_REASONING_COLUMNS <= cols
        assert EXPECTED_STREAM_COLUMNS <= cols


def test_existing_db_without_columns_gets_columns_added():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "monitor.db"
        # Pre-create a requests table missing the new columns.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER
            )
            """
        )
        conn.commit()
        conn.close()

        Monitor(db_path=str(db_path))
        cols = _columns(db_path)
        assert EXPECTED_REASONING_COLUMNS <= cols
        assert EXPECTED_STREAM_COLUMNS <= cols


def test_migration_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "monitor.db"
        Monitor(db_path=str(db_path))
        Monitor(db_path=str(db_path))  # second pass — must not raise
        cols = _columns(db_path)
        assert EXPECTED_REASONING_COLUMNS <= cols
        assert EXPECTED_STREAM_COLUMNS <= cols
