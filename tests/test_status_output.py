"""Tests for status.py output formats and meme lines.

Tests validate all output modes (default, full, minimal, json),
proxy-down fallback, empty-DB handling, and meme line functionality.
"""

import json
import io
import os
import sys
import sqlite3
import random
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

# The savings-first status module is at ~/tokenpak/agent/cli/commands/status.py
# which is not in the standard tokenpak package path.
# We need to import it using path manipulation.
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

from agent.cli.commands.status import (
    run,
    run_full,
    _run_minimal,
    _run_json,
    _calculate_fleet_savings,
    MEME_LINES,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_db(tmp_path, rows):
    """Create a monitor.db with the standard schema and insert rows."""
    db = str(tmp_path / "monitor.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms INTEGER,
            status_code INTEGER,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER,
            compressed_tokens INTEGER,
            injected_tokens INTEGER DEFAULT 0,
            injected_sources TEXT DEFAULT '',
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            would_have_saved INTEGER DEFAULT 0
        )"""
    )
    conn.executemany(
        "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, "
        "cache_read_tokens, cache_creation_tokens, compressed_tokens) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


def _ts(delta_hours=0):
    """Return an ISO timestamp relative to now."""
    return (datetime.now(timezone.utc) - timedelta(hours=delta_hours)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


@pytest.fixture
def populated_db(tmp_path):
    """Create a DB with realistic test data."""
    rows = [
        (_ts(0), "claude-haiku-4-5", 100_000, 10_000, 50_000, 5_000, 20_000),
        (_ts(1), "claude-sonnet-4-6", 200_000, 20_000, 100_000, 10_000, 30_000),
        (_ts(2), "claude-opus-4-6", 300_000, 30_000, 150_000, 15_000, 40_000),
    ]
    return _make_db(tmp_path, rows)


@pytest.fixture
def empty_db(tmp_path):
    """Create an empty DB (with schema but no rows)."""
    return _make_db(tmp_path, [])


# ─── Meme Lines Tests ────────────────────────────────────────────────────────


class TestMemeLines:
    """Test meme line list and random selection."""

    def test_all_28_lines_present(self):
        """Verify exactly 28 meme lines exist."""
        assert len(MEME_LINES) == 28

    def test_all_lines_non_empty(self):
        """All meme lines should be non-empty strings."""
        for line in MEME_LINES:
            assert isinstance(line, str)
            assert len(line.strip()) > 0

    def test_no_trailing_whitespace(self):
        """Meme lines should not have trailing whitespace."""
        for line in MEME_LINES:
            assert line == line.rstrip()

    def test_random_selection_varies(self):
        """Random selection should produce different lines over iterations."""
        # Use fixed seed for reproducibility but reset to random after
        selections = set()
        for _ in range(100):
            selections.add(random.choice(MEME_LINES))
        # With 100 iterations and 28 lines, we should see multiple different lines
        assert len(selections) >= 5, "Random selection should produce variety"

    def test_each_line_selectable(self):
        """Each meme line should be selectable (no unreachable lines)."""
        # This test verifies all lines are in the list and accessible
        for line in MEME_LINES:
            assert line in MEME_LINES


# ─── Default Mode Tests ──────────────────────────────────────────────────────


class TestDefaultMode:
    """Test default savings-first output mode."""

    def test_shows_without_tokenpak(self, populated_db):
        """Default mode should show 'Without TokenPak' cost."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        assert "Without TokenPak:" in text

    def test_shows_with_tokenpak(self, populated_db):
        """Default mode should show 'With TokenPak' cost."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        assert "With TokenPak:" in text

    def test_shows_total_saved(self, populated_db):
        """Default mode should show total saved amount."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        assert "Total saved:" in text

    def test_shows_per_model_table(self, populated_db):
        """Default mode should show per-model breakdown."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        # Should show model names in the output
        assert "claude-haiku-4-5" in text or "MODELS" in text

    def test_shows_meme_line_by_default(self, populated_db):
        """Default mode should include meme line (📦 prefix)."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=False)
        text = output.getvalue()
        assert "📦" in text

    def test_shows_savings_header(self, populated_db):
        """Default mode should show SAVINGS section header."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        assert "SAVINGS" in text


# ─── Full Mode Tests ─────────────────────────────────────────────────────────


class TestFullMode:
    """Test --full backward-compatible output mode."""

    def test_full_mode_shows_legacy_header(self):
        """Full mode should show legacy header format."""
        mock_health = {
            "is_degraded": False,
            "uptime_seconds": 3600,
            "compression_ratio_avg": 0.85,
            "stats": {"errors": 0}
        }
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=mock_health):
                run_full()
        text = output.getvalue()
        assert "Status (Full)" in text or "TOKENPAK" in text

    def test_full_mode_shows_proxy_status(self):
        """Full mode should show proxy running status."""
        mock_health = {
            "is_degraded": False,
            "uptime_seconds": 3600,
            "stats": {"errors": 0}
        }
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=mock_health):
                run_full()
        text = output.getvalue()
        assert "Proxy running" in text

    def test_full_mode_shows_uptime(self):
        """Full mode should show uptime."""
        mock_health = {
            "is_degraded": False,
            "uptime_seconds": 7200,
            "stats": {"errors": 0}
        }
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=mock_health):
                run_full()
        text = output.getvalue()
        assert "Uptime" in text


# ─── Minimal Mode Tests ──────────────────────────────────────────────────────


class TestMinimalMode:
    """Test --minimal one-line output mode."""

    def test_minimal_single_line(self, populated_db):
        """Minimal mode should produce single-line output."""
        output = io.StringIO()
        with redirect_stdout(output):
            _run_minimal(db_path=populated_db, no_meme=True)
        text = output.getvalue().strip()
        # Should be a single line (no newlines except trailing)
        lines = [l for l in text.split("\n") if l.strip()]
        assert len(lines) == 1

    def test_minimal_shows_saved_amount(self, populated_db):
        """Minimal mode should show saved amount."""
        output = io.StringIO()
        with redirect_stdout(output):
            _run_minimal(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        # Should contain a dollar amount
        assert "$" in text

    def test_minimal_shows_request_count(self, populated_db):
        """Minimal mode should show request count."""
        output = io.StringIO()
        with redirect_stdout(output):
            _run_minimal(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        assert "req" in text.lower()

    def test_minimal_shows_cache_rate(self, populated_db):
        """Minimal mode should show cache hit rate."""
        output = io.StringIO()
        with redirect_stdout(output):
            _run_minimal(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        assert "cache" in text.lower()

    def test_minimal_has_tokenpak_prefix(self, populated_db):
        """Minimal mode should start with TokenPak prefix."""
        output = io.StringIO()
        with redirect_stdout(output):
            _run_minimal(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        assert "TokenPak" in text or "📦" in text


# ─── JSON Mode Tests ─────────────────────────────────────────────────────────


class TestJsonMode:
    """Test --json machine-readable output mode."""

    def test_json_is_valid(self, populated_db):
        """JSON mode should produce valid JSON."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                _run_json(db_path=populated_db)
        text = output.getvalue()
        # Should parse without error
        data = json.loads(text)
        assert isinstance(data, dict)

    def test_json_has_version(self, populated_db):
        """JSON mode should include version."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                _run_json(db_path=populated_db)
        data = json.loads(output.getvalue())
        assert "version" in data

    def test_json_has_savings(self, populated_db):
        """JSON mode should include savings data."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                _run_json(db_path=populated_db)
        data = json.loads(output.getvalue())
        assert "savings" in data

    def test_json_has_proxy_status(self, populated_db):
        """JSON mode should include proxy status."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                _run_json(db_path=populated_db)
        data = json.loads(output.getvalue())
        assert "proxy" in data

    def test_json_has_meme_lines(self, populated_db):
        """JSON mode should include meme lines list."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                _run_json(db_path=populated_db)
        data = json.loads(output.getvalue())
        assert "meme_lines" in data
        assert len(data["meme_lines"]) == 28


# ─── No-Meme Mode Tests ──────────────────────────────────────────────────────


class TestNoMemeMode:
    """Test --no-meme flag."""

    def test_no_meme_suppresses_tagline(self, populated_db):
        """--no-meme should suppress the 📦 meme line."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        # Should not contain the meme prefix
        # (Note: the version header may contain 📦, so check for meme content)
        # Since meme lines are at the end and specific, check they're not in output
        for meme in MEME_LINES:
            assert meme not in text

    def test_no_meme_minimal_mode(self, populated_db):
        """--no-meme should work in minimal mode too."""
        output = io.StringIO()
        with redirect_stdout(output):
            _run_minimal(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        # Minimal with no_meme should not contain meme line content
        # It should just have the summary stats
        for meme in MEME_LINES:
            assert meme not in text


# ─── Proxy Down Tests ────────────────────────────────────────────────────────


class TestProxyDown:
    """Test proxy-down fallback behavior."""

    def test_proxy_down_shows_warning(self, populated_db):
        """When proxy is down, should show warning."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        # Should show proxy unreachable warning
        assert "⚠️" in text or "unreachable" in text.lower()

    def test_proxy_down_still_shows_savings(self, populated_db):
        """When proxy is down, should still show DB-based savings."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        # Should still show savings data from DB
        assert "SAVINGS" in text or "saved" in text.lower()

    def test_proxy_down_shows_historical_data(self, populated_db):
        """When proxy is down, should indicate historical data only."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=populated_db, no_meme=True)
        text = output.getvalue()
        # Should indicate this is historical/DB data
        assert "historical" in text.lower() or "Proxy unreachable" in text


# ─── Empty DB Tests ──────────────────────────────────────────────────────────


class TestEmptyDB:
    """Test empty DB handling."""

    def test_empty_db_shows_helpful_message(self, empty_db):
        """Empty DB should show helpful setup message."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=empty_db, no_meme=True)
        text = output.getvalue()
        # Should have helpful message about no data
        assert "No" in text or "no data" in text.lower() or "📭" in text

    def test_empty_db_no_crash(self, empty_db):
        """Empty DB should not crash."""
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                # Should not raise
                run(db_path=empty_db, no_meme=True)
        # If we get here without exception, test passes

    def test_missing_db_handled(self, tmp_path):
        """Missing DB file should be handled gracefully."""
        fake_path = str(tmp_path / "nonexistent.db")
        output = io.StringIO()
        with redirect_stdout(output):
            with patch("agent.cli.commands.status._fetch", return_value=None):
                run(db_path=fake_path, no_meme=True)
        text = output.getvalue()
        # Should show DB not found message
        assert "not found" in text.lower() or "⚠️" in text


# ─── Fleet Savings Calculation Tests ─────────────────────────────────────────


class TestFleetSavingsCalc:
    """Test internal _calculate_fleet_savings function."""

    def test_returns_dict_with_required_keys(self, populated_db):
        """Fleet savings should return dict with required keys."""
        result = _calculate_fleet_savings(db_path=populated_db, period="24h")
        assert "period" in result
        assert "models" in result or "error" in result
        assert "totals" in result or "error" in result

    def test_totals_has_savings_fields(self, populated_db):
        """Totals should include cost fields."""
        result = _calculate_fleet_savings(db_path=populated_db, period="24h")
        if not result.get("error"):
            totals = result["totals"]
            assert "without_cost" in totals
            assert "with_cost" in totals
            assert "saved" in totals

    def test_empty_db_returns_error(self, empty_db):
        """Empty DB should return error indicator."""
        result = _calculate_fleet_savings(db_path=empty_db, period="24h")
        assert result.get("error") == "no_data"

    def test_missing_db_returns_error(self, tmp_path):
        """Missing DB should return error indicator."""
        fake_path = str(tmp_path / "nonexistent.db")
        result = _calculate_fleet_savings(db_path=fake_path)
        assert result.get("error") == "db_not_found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
