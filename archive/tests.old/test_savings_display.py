"""test_savings_display.py — Tests for enhanced savings display in status and savings commands."""

import pytest
from unittest.mock import patch, MagicMock
from tokenpak.pricing import estimate_savings, get_rates, MODEL_RATES
from tokenpak.agent.cli.commands import status, savings


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

        # Without TokenPak: 1M tokens * $15/MTok = $15.00
        assert result["cost_without_tokenpak"] == pytest.approx(15.00, abs=0.01)

        # With TokenPak:
        # - Compression reduces to 900k tokens
        # - 200k from cache at $1.50, 700k fresh at $15.00
        # = (200k * 1.5 + 700k * 15) / 1M = $10.80
        expected_with = (200_000 * 1.50 + 700_000 * 15.0) / 1_000_000
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

    @patch("tokenpak.agent.cli.commands.status._fetch")
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
            status.run(proxy_base="http://127.0.0.1:8766")
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

    @patch("tokenpak.agent.cli.commands.status._fetch")
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
                status.run(proxy_base="http://127.0.0.1:8766")
            output = captured_output.getvalue()
        finally:
            sys.stdout = sys.__stdout__

        # Check for helpful error message
        assert "unreachable" in output or "Proxy not running" in output.lower()


class TestSavingsCommand:
    """Test the savings command with before/after comparison."""

    @patch("tokenpak.agent.cli.commands.savings._connect")
    def test_savings_summary_with_before_after(self, mock_connect):
        """Test savings summary includes before/after comparison."""
        # Mock the database connection
        mock_conn = MagicMock()

        # Mock the query result
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: {
            "requests": 100,
            "avg_raw": 50_000,
            "avg_compressed": 47_500,
            "total_raw": 5_000_000,
            "total_compressed": 4_750_000,
            "total_cost": 14.25,  # 4.75M tokens * $3/MTok
        }[key]

        mock_conn.execute.return_value.fetchone.return_value = mock_row
        mock_connect.return_value = mock_conn

        result = savings._query_savings(period="24h")

        # Check for before/after fields
        assert "cost_without_tokenpak" in result
        assert "cost_with_tokenpak" in result
        assert "cost_reduction_pct" in result

        # Verify the values are sensible
        assert result["cost_without_tokenpak"] == pytest.approx(15.0, abs=0.1)
        assert result["cost_with_tokenpak"] == pytest.approx(14.25, abs=0.1)
        assert result["cost_reduction_pct"] > 0

    @patch("tokenpak.agent.cli.commands.savings._connect")
    def test_savings_no_data(self, mock_connect):
        """Test savings gracefully handles no data."""
        mock_connect.return_value = None

        result = savings._query_savings(period="24h")

        assert "error" in result
        assert result["error"] == "DB not found"

    @patch("tokenpak.agent.cli.commands.savings._connect")
    def test_savings_by_model(self, mock_connect):
        """Test per-model breakdown includes cost reduction."""
        mock_conn = MagicMock()

        # Mock per-model rows
        mock_rows = [
            MagicMock(**{
                "__getitem__": lambda self, key: {
                    "model": "claude-sonnet-4-6",
                    "requests": 50,
                    "avg_raw": 40_000,
                    "avg_compressed": 38_000,
                    "total_raw": 2_000_000,
                    "total_compressed": 1_900_000,
                    "total_cost": 5.70,
                }[key]
            })
        ]

        mock_conn.execute.return_value.fetchall.return_value = mock_rows
        mock_connect.return_value = mock_conn

        result = savings._query_by_model(period="24h")

        assert len(result) > 0
        assert "cost_reduction_pct" in result[0]
        assert result[0]["model"] == "claude-sonnet-4-6"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
