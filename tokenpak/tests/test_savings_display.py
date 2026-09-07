"""test_savings_display.py — Tests for enhanced savings display in status and savings commands."""

from unittest.mock import patch

import pytest

from tokenpak.cli.commands import status
from tokenpak.telemetry.pricing import estimate_savings, get_rates


class TestPricingModule:
    """Test the pricing module."""

    def test_get_rates_known_model(self):
        """Test getting rates for a known model."""
        rates = get_rates("claude-sonnet-4-6")
        assert rates["input"] == 3.0
        assert rates["cached"] == 0.30
        assert rates["output"] == 15.0

    def test_get_rates_unknown_model(self):
        """Test getting rates for an unknown model falls back to default."""
        rates = get_rates("unknown-model-xyz")
        assert rates == {"input": 3.0, "cached": 0.30, "output": 15.0}

    def test_get_rates_none(self):
        """Test getting rates with None uses default."""
        rates = get_rates(None)
        assert rates == {"input": 3.0, "cached": 0.30, "output": 15.0}

    def test_estimate_savings_with_compression(self):
        """Test savings calculation with compression."""
        stats = {
            "tokens_raw": 1_000_000,
            "tokens_saved": 50_000,  # 5% compression
            "cache_read_tokens": 100_000,
            "cache_write_tokens": 200_000,
            "model": "claude-sonnet-4-6",
        }
        result = estimate_savings(stats)

        # Compression: 50k tokens at $3/MTok = $0.15
        assert result["compression_cost_saved"] == pytest.approx(0.15, abs=0.01)

        # Cache: 100k tokens * (3.0 - 0.30) / 1M = $0.27
        assert result["cache_cost_saved"] == pytest.approx(0.27, abs=0.01)

        assert result["compression_tokens_saved"] == 50_000
        assert result["cache_tokens_saved"] == 100_000
        assert result["total_tokens_saved"] == 150_000

    def test_estimate_savings_before_after(self):
        """Test before/after cost calculation."""
        stats = {
            "tokens_raw": 1_000_000,
            "tokens_saved": 100_000,  # 10% compression
            "cache_read_tokens": 200_000,
            "cache_write_tokens": 0,
            "model": "claude-opus-4-6",
        }
        result = estimate_savings(stats)

        # Without TokenPak: 1M tokens * $5/MTok = $5.00
        assert result["cost_without_tokenpak"] == pytest.approx(5.00, abs=0.01)

        # With TokenPak:
        # - Compression reduces to 900k tokens
        # - 200k from cache at $0.50, 700k fresh at $5.00
        # = (200k * 0.5 + 700k * 5) / 1M = $3.60
        expected_with = (200_000 * 0.50 + 700_000 * 5.0) / 1_000_000
        assert result["cost_with_tokenpak"] == pytest.approx(expected_with, abs=0.01)

        # Reduction should be positive
        assert result["reduction_percent"] > 0

    def test_estimate_savings_no_compression(self):
        """Test savings with no compression but cache hits."""
        stats = {
            "tokens_raw": 1_000_000,
            "tokens_saved": 0,  # No compression
            "cache_read_tokens": 300_000,  # 30% cache hits
            "cache_write_tokens": 700_000,
            "model": "claude-sonnet-4-6",
        }
        result = estimate_savings(stats)

        assert result["compression_tokens_saved"] == 0
        assert result["cache_tokens_saved"] == 300_000

        # Total savings: 300k * (3.0 - 0.30) / 1M = $0.81
        assert result["total_cost_saved"] == pytest.approx(0.81, abs=0.01)


class TestStatusCommand:
    """Test the status command with savings display."""

    @patch("tokenpak.cli.commands.status._fetch")
    def test_status_with_savings(self, mock_fetch):
        """Test status command includes savings summary."""
        # Mock the /health endpoint
        health_response = {
            "is_degraded": False,
            "uptime_seconds": 15780,  # 4h 23m
            "version": "v1.2.3",
            "compression_ratio_avg": 0.945,
        }

        # Mock the /stats/session endpoint
        session_response = {
            "session_requests": 1342,
            "tokens_raw": 41_955_704,
            "tokens_saved": 2_333_947,
            "cache_read_tokens": 92_520_228,
            "cache_write_tokens": 0,
            "session_total_saved": 256.81,
            "avg_savings_pct": 5.6,
            "errors": 0,
            "model": "claude-sonnet-4-6",
        }

        def fetch_side_effect(url, timeout=5):
            if "/health" in url:
                return health_response
            elif "/stats/session" in url:
                return session_response
            elif "/degradation" in url:
                return {"recent_events": [], "status": "ok"}
            return None

        mock_fetch.side_effect = fetch_side_effect

        # Capture output
        import io
        import sys

        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            # `status.run()` is now the savings-first *default* view (a
            # different, lighter render that reads /stats + /cache-stats,
            # not /stats/session). The "Session Savings" block asserted
            # below is part of `status.run_full()`, the explicit --full /
            # backward-compatible surface — see its docstring
            # ("original output, backward compat").
            status.run_full(proxy_base="http://127.0.0.1:8766")
            output = captured_output.getvalue()
        finally:
            sys.stdout = sys.__stdout__

        # Check that savings section is present
        assert "💰  Session Savings" in output
        assert "Requests:" in output
        assert "1,342" in output
        assert "Input tokens:" in output
        assert "41,955,704" in output
        assert "Tokens saved:" in output
        assert "Est. saved:" in output

    @patch("tokenpak.cli.commands.status._fetch")
    def test_status_proxy_down(self, mock_fetch):
        """Test status gracefully handles when proxy is down."""
        mock_fetch.return_value = None

        import io
        import sys

        captured_output = io.StringIO()
        sys.stdout = captured_output
        sys.stderr = io.StringIO()

        try:
            with pytest.raises(SystemExit):
                status.run_full(proxy_base="http://127.0.0.1:8766")
            output = captured_output.getvalue()
        finally:
            sys.stdout = sys.__stdout__

        # Check for helpful error message
        assert "unreachable" in output or "Proxy not running" in output.lower()


# ---------------------------------------------------------------------------
# TestSavingsCommand — removed 2026-09-01.
#
# These three tests (`test_savings_summary_with_before_after`,
# `test_savings_no_data`, `test_savings_by_model`) patched
# `tokenpak.cli.commands.savings._connect`, a zero-arg helper that opened a
# `sqlite3.Connection` against a module-level `_MONITOR_DB` path and returned
# `None` when the DB file was missing. That helper — and the before/after
# dollar-cost fields it fed (`cost_without_tokenpak`, `cost_with_tokenpak`,
# `cost_reduction_pct`) — belonged to the pre-deprecation `tokenpak savings`
# command (see its history: introduced 2026-03-11, superseded 2026-04-09 when
# `savings.py` became a deprecation wrapper that delegates to
# `tokenpak status`; `_connect` was never carried over).
#
# `savings.py` still exports `_query_savings()` / `_query_by_model()` — kept
# for `run_savings_cmd`'s optional module surface — but they were rebuilt
# 2026-04-10 (`f77d4f6db4`) against a different DB schema (`requests` table
# with `input_tokens` / `compressed_tokens` columns, read via
# `sqlite3.connect(_MONITOR_DB)` directly, no `_connect` seam) and a
# different return shape (`avg_raw_tokens`, `avg_compressed_tokens`,
# `tokens_saved_total`, `reduction_pct` — no cost fields at all). Rewriting
# these three tests against that shape would drop the very thing they were
# written to check (before/after cost comparison), so the honest move is
# deletion, not a reskin.
#
# The current `_query_savings()` / `_query_by_model()` business logic (the
# `_MONITOR_DB`-patched seam, token-based fields) is exercised by
# `tests/test_savings_cmd.py` (top-level `tests/`, part of the default CI
# suite). The savings *display* — before/after cost comparison included —
# now lives in `status.run_full()` and is covered by
# `TestStatusCommand.test_status_with_savings` above.
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
