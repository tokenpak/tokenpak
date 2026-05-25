# SPDX-License-Identifier: Apache-2.0
"""tests/test_fta06_rollup.py — FTA-06: rollup_daily table + fleet status CLI.

Tests:
  1. rollup SQL: produces correct SUMs against a fixture requests table.
  2. rollup idempotency: running twice doesn't double-count (PRIMARY KEY enforces).
  3. CLI: run_fleet() returns expected table output for a fixture.
  4. CLI: --json schema matches expected keys.
  5. _parse_since: valid and invalid inputs.
  6. run_fleet: empty DB returns graceful message (no crash).
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

# Module-level imports so the 40s tokenpak.cli eager-import of _cli_core
# happens once during pytest collection, not inside each timed test.
from tokenpak.cli._impl import _saved_pct, run_fleet
from tokenpak.cli.commands.status import _parse_since

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REQUESTS_DDL = """
    CREATE TABLE requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        model TEXT NOT NULL,
        agent_id TEXT,
        host TEXT,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cache_read_tokens INTEGER DEFAULT 0,
        cache_creation_tokens INTEGER DEFAULT 0,
        estimated_cost REAL DEFAULT 0.0,
        would_have_saved INTEGER DEFAULT 0,
        attribution_source TEXT DEFAULT 'unknown'
    )
"""

ROLLUP_DDL = """
    CREATE TABLE IF NOT EXISTS rollup_daily (
        date                  TEXT NOT NULL,
        agent_id              TEXT,
        host                  TEXT,
        model                 TEXT,
        requests              INTEGER NOT NULL DEFAULT 0,
        input_tokens          INTEGER NOT NULL DEFAULT 0,
        output_tokens         INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
        cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
        estimated_cost        REAL NOT NULL DEFAULT 0.0,
        would_have_saved      INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (date, agent_id, host, model)
    )
"""

ROLLUP_SQL = """
    INSERT OR REPLACE INTO rollup_daily
    SELECT
        date(timestamp) AS date,
        agent_id,
        host,
        model,
        COUNT(*),
        COALESCE(SUM(input_tokens),          0),
        COALESCE(SUM(output_tokens),         0),
        COALESCE(SUM(cache_read_tokens),     0),
        COALESCE(SUM(cache_creation_tokens), 0),
        COALESCE(SUM(estimated_cost),      0.0),
        COALESCE(SUM(would_have_saved),      0)
    FROM requests
    WHERE timestamp >= date('now', '-7 days')
    GROUP BY date(timestamp), agent_id, host, model
"""


@pytest.fixture
def fixture_db() -> sqlite3.Connection:
    """In-memory SQLite DB with requests and rollup_daily tables pre-populated."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(REQUESTS_DDL)
    conn.execute(ROLLUP_DDL)
    # Insert 3 rows: same (date, agent, host, model) → should sum to 1 rollup row
    conn.executemany(
        """INSERT INTO requests
           (timestamp, model, agent_id, host,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, estimated_cost, would_have_saved)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("2026-05-12 10:00:00", "claude-sonnet-4-6", "agent-1", "host-a",
             1000, 200, 500, 100, 0.003, 0),
            ("2026-05-12 11:00:00", "claude-sonnet-4-6", "agent-1", "host-a",
             2000, 300, 700, 150, 0.005, 0),
            # Different agent → second rollup row
            ("2026-05-12 12:00:00", "claude-sonnet-4-6", "agent-2", "host-b",
             1500, 250, 600, 120, 0.004, 50),
        ],
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Test 1: rollup SQL correctness
# ---------------------------------------------------------------------------

@pytest.mark.oss
def test_rollup_sums_correctly(fixture_db: sqlite3.Connection) -> None:
    """Rollup aggregates rows per (date, agent_id, host, model) correctly."""
    fixture_db.execute(ROLLUP_SQL)
    fixture_db.commit()

    rows = fixture_db.execute(
        "SELECT * FROM rollup_daily ORDER BY agent_id"
    ).fetchall()
    assert len(rows) == 2, f"expected 2 rollup rows, got {len(rows)}"

    a1_row = next(r for r in rows if r["agent_id"] == "agent-1")
    assert a1_row["requests"] == 2
    assert a1_row["input_tokens"] == 3000  # 1000 + 2000
    assert a1_row["output_tokens"] == 500  # 200 + 300
    assert a1_row["cache_read_tokens"] == 1200  # 500 + 700
    assert abs(a1_row["estimated_cost"] - 0.008) < 1e-6  # 0.003 + 0.005

    a2_row = next(r for r in rows if r["agent_id"] == "agent-2")
    assert a2_row["requests"] == 1
    assert a2_row["would_have_saved"] == 50


# ---------------------------------------------------------------------------
# Test 2: rollup idempotency
# ---------------------------------------------------------------------------

@pytest.mark.oss
def test_rollup_idempotent(fixture_db: sqlite3.Connection) -> None:
    """Running rollup twice doesn't double-count (PRIMARY KEY → INSERT OR REPLACE)."""
    fixture_db.execute(ROLLUP_SQL)
    fixture_db.commit()
    first_count = fixture_db.execute("SELECT COUNT(*) FROM rollup_daily").fetchone()[0]

    fixture_db.execute(ROLLUP_SQL)
    fixture_db.commit()
    second_count = fixture_db.execute("SELECT COUNT(*) FROM rollup_daily").fetchone()[0]

    assert first_count == second_count, "double-run must not add duplicate rows"

    a1_row = fixture_db.execute(
        "SELECT requests FROM rollup_daily WHERE agent_id='agent-1'"
    ).fetchone()
    assert a1_row["requests"] == 2, "re-run must not double the request count"


# ---------------------------------------------------------------------------
# Test 3: run_fleet table output for a fixture DB
# ---------------------------------------------------------------------------

@contextmanager
def _fixture_db_file(tmp_path: Path) -> Generator[str, None, None]:
    """Create a fixture monitor.db file and yield its path."""
    db_path = str(tmp_path / "monitor.db")
    conn = sqlite3.connect(db_path)
    conn.execute(REQUESTS_DDL)
    conn.execute(ROLLUP_DDL)
    conn.executemany(
        """INSERT INTO requests
           (timestamp, model, agent_id, host,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, estimated_cost, would_have_saved)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("2026-05-12 10:00:00", "claude-sonnet-4-6", "agent-1", "host-a",
             1000, 200, 500, 100, 0.003, 0),
        ],
    )
    # Populate rollup_daily
    conn.execute("""
        INSERT INTO rollup_daily
        VALUES ('2026-05-12', 'agent-1', 'host-a', 'claude-sonnet-4-6',
                1, 1000, 200, 500, 100, 0.003, 0)
    """)
    conn.commit()
    conn.close()
    yield db_path


@pytest.mark.oss
def test_run_fleet_table_output(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """run_fleet() prints a table with expected columns and at least one row."""
    with _fixture_db_file(tmp_path) as db_path:
        run_fleet(since_days=7, as_json=False, db_path=db_path)

    captured = capsys.readouterr()
    out = captured.out

    assert "Fleet status" in out
    assert "agent-1" in out
    assert "host-a" in out
    assert "claude-sonnet-4-6" in out
    # Header columns present
    assert "agent" in out
    assert "runtime" in out
    assert "model" in out
    assert "reqs" in out


# ---------------------------------------------------------------------------
# Test 4: --json schema
# ---------------------------------------------------------------------------

@pytest.mark.oss
def test_run_fleet_json_schema(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """run_fleet(as_json=True) emits valid JSON with expected top-level keys."""
    with _fixture_db_file(tmp_path) as db_path:
        run_fleet(since_days=7, as_json=True, db_path=db_path)

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert "since_days" in data
    assert "source" in data
    assert "row_count" in data
    assert "rows" in data
    assert isinstance(data["rows"], list)
    assert len(data["rows"]) >= 1

    row = data["rows"][0]
    for key in ("date", "agent_id", "host", "model", "requests",
                "input_tokens", "output_tokens", "cache_read_tokens",
                "cache_creation_tokens", "estimated_cost", "would_have_saved",
                "saved_pct"):
        assert key in row, f"missing key '{key}' in JSON row"


# ---------------------------------------------------------------------------
# Test 5: _parse_since
# ---------------------------------------------------------------------------

@pytest.mark.oss
@pytest.mark.parametrize("value,expected", [
    ("7d", 7),
    ("14d", 14),
    ("1d", 1),
    ("30d", 30),
    ("7", 7),     # bare integer
    ("bad", 7),   # fallback default
    ("0d", 1),    # min clamp
])
def test_parse_since(value: str, expected: int) -> None:
    """_parse_since handles valid and invalid inputs."""
    assert _parse_since(value) == expected


# ---------------------------------------------------------------------------
# Test 6: empty DB → graceful message
# ---------------------------------------------------------------------------

@pytest.mark.oss
def test_run_fleet_empty_db(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """run_fleet() handles a DB with no rollup or requests rows gracefully."""
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute(REQUESTS_DDL)
    conn.execute(ROLLUP_DDL)
    conn.commit()
    conn.close()

    run_fleet(since_days=7, as_json=False, db_path=db_path)
    out = capsys.readouterr().out
    # Should not crash and should print a user-facing message
    assert "No data" in out or "Fleet status" in out


# ---------------------------------------------------------------------------
# Test 7: saved_pct TBD when cost=0 and would_have_saved>0
# ---------------------------------------------------------------------------

@pytest.mark.oss
def test_saved_pct_tbd_for_pricing_unknown() -> None:
    """_saved_pct returns 'TBD' when estimated_cost=0 but would_have_saved>0."""
    assert _saved_pct(0.0, 100) == "TBD"


@pytest.mark.oss
def test_saved_pct_na_for_zero_data() -> None:
    """_saved_pct returns 'n/a' when both inputs are zero."""
    assert _saved_pct(0.0, 0) == "n/a"
