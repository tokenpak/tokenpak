"""Tests for tokenpak savings command."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# TSR-05t deprecated-command skip reason (grep-able)
# ─────────────────────────────────────────────
# `tokenpak savings` is now deprecated. `run_savings_cmd()` emits a
# deprecation banner followed by `tokenpak status`'s default view:
#
#   ⚠️  `tokenpak savings` is deprecated.
#       All savings data is now shown in `tokenpak status` (default view).
#
# Two tests encode the pre-deprecation contract:
#   - `test_json_output` calls `json.loads(captured.out)` — banner text
#     prefixed → `JSONDecodeError`.
#   - `test_no_db_graceful` asserts `"No data" / "✖" / "not found"` —
#     deprecation banner + delegated status output replace those markers.
#
# Both contracts are gone for good (the command is deprecated). Path B
# skip with grep-able reason; the 5 live tests that don't depend on the
# deprecated wire format remain meaningful guards on the
# `query_savings()` business logic.
SKIP_SAVINGS_CMD_DEPRECATED = (
    "Test asserts the pre-deprecation `tokenpak savings` wire format. "
    "The command now emits a deprecation banner and delegates to "
    "`tokenpak status`; the old JSON / no-data output shape is gone. "
    "Live tests covering `query_savings()` business logic remain."
)


from tokenpak.cli.commands.savings import (
    _query_by_model,
    _query_savings,
    run_savings_cmd,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db(tmp_path):
    """Temp monitor.db with known rows for savings assertions."""
    db_path = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model TEXT,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms REAL,
            status_code INTEGER,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER,
            compressed_tokens INTEGER,
            injected_tokens INTEGER,
            injected_sources TEXT,
            cache_read_tokens INTEGER,
            cache_creation_tokens INTEGER
        )
    """)
    today = date.today().isoformat()
    old = (date.today() - timedelta(days=10)).isoformat()

    # Today: 2 requests, raw 10000/8000, compressed 4000/3200
    conn.executemany(
        "INSERT INTO requests (timestamp,model,input_tokens,compressed_tokens,estimated_cost)"
        " VALUES (?,?,?,?,?)",
        [
            (f"{today} 10:00:00", "claude-sonnet-4-5", 10000, 4000, 0.30),
            (f"{today} 11:00:00", "claude-sonnet-4-5", 8000,  3200, 0.24),
            (f"{today} 12:00:00", "gpt-4o",            6000,  2400, 0.18),
            # Old row (outside 24h but inside 7d and 30d)
            (f"{old} 10:00:00",  "claude-sonnet-4-5", 5000,  2000, 0.15),
        ],
    )
    conn.commit()
    conn.close()
    return str(db_path)


# ---------------------------------------------------------------------------
# Test: query shows raw + compressed + delta + %
# ---------------------------------------------------------------------------


def test_query_savings_shows_all_four_fields(temp_db):
    """AC2: summary must have raw avg, compressed avg, reduction %, and tokens saved (delta)."""
    with patch("tokenpak.cli.commands.savings._MONITOR_DB", temp_db):
        data = _query_savings(period="24h")

    assert "avg_raw_tokens" in data,       "raw avg missing"
    assert "avg_compressed_tokens" in data, "compressed avg missing"
    assert "reduction_pct" in data,         "reduction % missing"
    assert "tokens_saved_total" in data,    "delta (tokens saved) missing"

    # Validate correctness
    # 3 rows today: raw 10000+8000+6000=24000, compressed 4000+3200+2400=9600
    # avg_raw ~8000, avg_comp ~3200
    assert data["avg_raw_tokens"] == 8000
    assert data["avg_compressed_tokens"] == 3200
    assert data["tokens_saved_total"] == 14400
    assert data["reduction_pct"] == pytest.approx(60.0, abs=0.1)


# ---------------------------------------------------------------------------
# Test: period flag (24h vs 7d)
# ---------------------------------------------------------------------------


def test_period_flag_7d_includes_older_rows(temp_db):
    """AC3: --period 7d should include more rows than 24h."""
    with patch("tokenpak.cli.commands.savings._MONITOR_DB", temp_db):
        data_24h = _query_savings(period="24h")
        data_7d  = _query_savings(period="7d")

    assert data_24h["requests"] == 3
    # 7d cutoff: old row is 10 days ago, outside 7d window
    assert data_7d["requests"] == 3  # same 3 (old is 10 days ago, outside 7d)


def test_period_flag_30d_includes_old_row(temp_db):
    """AC3: --period 30d captures the 10-day-old row."""
    with patch("tokenpak.cli.commands.savings._MONITOR_DB", temp_db):
        data_30d = _query_savings(period="30d")

    assert data_30d["requests"] == 4


# ---------------------------------------------------------------------------
# Test: per-model breakdown (verbose)
# ---------------------------------------------------------------------------


def test_query_by_model_returns_breakdown(temp_db):
    """AC4 (verbose): per-model rows returned with savings fields."""
    with patch("tokenpak.cli.commands.savings._MONITOR_DB", temp_db):
        rows = _query_by_model(period="24h")

    assert len(rows) == 2  # claude-sonnet-4-5 and gpt-4o
    models = {r["model"] for r in rows}
    assert "claude-sonnet-4-5" in models
    assert "gpt-4o" in models

    for r in rows:
        assert "avg_raw_tokens" in r
        assert "avg_compressed_tokens" in r
        assert "reduction_pct" in r
        assert "tokens_saved_total" in r


# ---------------------------------------------------------------------------
# Test: --json flag produces machine-readable output
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_SAVINGS_CMD_DEPRECATED)
def test_json_output(temp_db, capsys):
    """AC5 (json flag): output must be valid JSON with summary key."""
    args = SimpleNamespace(period="24h", verbose=False, as_json=True)
    with patch("tokenpak.cli.commands.savings._MONITOR_DB", temp_db):
        run_savings_cmd(args)

    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert "summary" in out
    assert "avg_raw_tokens" in out["summary"]
    assert "avg_compressed_tokens" in out["summary"]
    assert "reduction_pct" in out["summary"]
    assert "tokens_saved_total" in out["summary"]


# ---------------------------------------------------------------------------
# Test: human-readable output contains all four values
# ---------------------------------------------------------------------------


def test_human_output_shows_four_values(temp_db, capsys):
    """AC2 via rendered output: raw/compressed/delta/% all present."""
    args = SimpleNamespace(period="24h", verbose=False, as_json=False)
    with patch("tokenpak.cli.commands.savings._MONITOR_DB", temp_db):
        run_savings_cmd(args)

    out = capsys.readouterr().out
    # Output format: "Tokens trimmed: X (▼ Y%)" or raw/compressed style
    assert "Tokens" in out or "tokens" in out.lower()
    assert "%" in out
    assert "Saved" in out or "saved" in out.lower() or "Trimmed" in out or "trimmed" in out.lower()


# ---------------------------------------------------------------------------
# Test: no-data graceful fallback
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason=SKIP_SAVINGS_CMD_DEPRECATED)
def test_no_db_graceful(tmp_path, capsys):
    """Missing DB should print error, not crash."""
    missing = str(tmp_path / "nonexistent.db")
    args = SimpleNamespace(period="24h", verbose=False, as_json=False)
    with patch("tokenpak.cli.commands.savings._MONITOR_DB", missing):
        run_savings_cmd(args)

    out = capsys.readouterr().out
    assert "No data" in out or "✖" in out or "not found" in out.lower()
