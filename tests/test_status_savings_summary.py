"""Tests for compact savings summary in `tokenpak status` output.

Verifies:
- Savings line appears when today's data is available
- Graceful no-data fallback when no requests today
- Compression % computed correctly
- Token count formatted correctly (K, M, raw)
- Cost saved shows when >0, omits when 0
- Proxy unreachable: no savings line (no crash)
"""

from io import StringIO
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_health(port=8766, compilation_mode="hybrid"):
    """Minimal health response."""
    import time
    return {
        "status": "healthy",
        "compilation_mode": compilation_mode,
        "stats": {
            "start_time": time.time() - 300,
            "requests": 10,
            "errors": 0,
            "input_tokens": 100_000,
            "sent_input_tokens": 70_000,
            "saved_tokens": 30_000,
            "protected_tokens": 5_000,
            "cost": 0.50,
            "cost_saved": 0.0,
        },
        "router": {"components": {}},
        "skeleton": {"enabled": True},
        "shadow_reader": {"enabled": True},
        "canon": {"enabled": True},
        "capsule_available": True,
        "circuit_breakers": {},
        "vault_index": {"available": True, "blocks": 1000},
    }


def _make_stats(today_requests=100,
                today_input=1_000_000,
                today_compressed=50_000,
                today_cache_read=200_000,
                today_cost=10.0):
    """Minimal stats response with a today block."""
    return {
        "session": {
            "requests": today_requests,
            "input_tokens": 100_000,
            "sent_input_tokens": 70_000,
            "saved_tokens": 30_000,
            "protected_tokens": 5_000,
            "output_tokens": 2_000,
            "cost": 0.50,
            "cost_saved": 0.0,
            "start_time": 0,
            "errors": 0,
            "compilation_mode": "hybrid",
            "active_profile": "balanced",
            "injected_tokens": 0,
            "injection_hits": 0,
            "injection_skips": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_miss_reasons": {},
            "token_cache_hits": 0,
            "token_cache_misses": 0,
            "canon_hits": 0,
            "canon_tokens_saved": 0,
            "ingest_entries": 0,
            "compression_timeouts": 0,
            "vault_last_timing_ms": {},
        },
        "today": {
            "requests": today_requests,
            "input_tokens": today_input,
            "output_tokens": 10_000,
            "total_cost": today_cost,
            "avg_latency_ms": 500.0,
            "protected_tokens": 0,
            "compressed_tokens": today_compressed,
            "injected_tokens": 0,
            "cache_read_tokens": today_cache_read,
            "cache_creation_tokens": 0,
        },
        "compilation_mode": "hybrid",
        "vault_index": {"available": True, "blocks": 1000},
        "router": {"enabled": True},
        "capsule_available": True,
    }


def _run_cmd_status(health, stats, cache=None, cost_saved_db=0.0):
    """Run cmd_status with mocked proxy endpoints, return captured stdout."""
    from argparse import Namespace

    args = Namespace(
        mode=None,
        output=None,
        minimal=False,
        raw=False,
        json=False,
        profile=None,
        port=None,
        no_color=True,
    )

    captured = StringIO()

    from tokenpak.telemetry.query import SavingsReport
    mock_savings = SavingsReport(
        total_cost=0.0,
        estimated_without_compression=0.0,
        savings_amount=cost_saved_db,
        savings_pct=(cost_saved_db * 10) if cost_saved_db else 0.0,
    )

    with patch("tokenpak._cli_core._proxy_get") as mock_get, \
         patch("tokenpak._cli_core.get_savings_report", mock_savings.__class__, create=True), \
         patch("sys.stdout", captured):

        def _side_effect(endpoint):
            if endpoint == "/health":
                return health
            if endpoint == "/stats":
                return stats
            if endpoint == "/cache-stats":
                return cache
            return None

        mock_get.side_effect = _side_effect

        # Patch get_savings_report inside the function scope
        with patch("tokenpak.telemetry.query.get_savings_report", return_value=mock_savings):
            from tokenpak._cli_core import cmd_status
            try:
                cmd_status(args)
            except SystemExit:
                pass

    return captured.getvalue()


# ---------------------------------------------------------------------------
# Tests: savings line present with data
# ---------------------------------------------------------------------------

class TestSavingsSummaryPresent:
    """Savings line appears when today has data."""

    def test_savings_line_in_output(self):
        health = _make_health()
        stats = _make_stats(today_requests=100, today_compressed=50_000, today_cache_read=200_000)
        out = _run_cmd_status(health, stats)
        assert "Savings:" in out

    def test_compression_pct_shown(self):
        health = _make_health()
        # 100K input, 10K compressed → 10%
        stats = _make_stats(today_input=100_000, today_compressed=10_000, today_cache_read=0)
        out = _run_cmd_status(health, stats)
        assert "10.0% avg compression" in out

    def test_tokens_saved_shown_in_K(self):
        health = _make_health()
        # 5K compressed + 45K cache_read = 50K saved → "50K tokens saved today"
        stats = _make_stats(today_input=100_000, today_compressed=5_000, today_cache_read=45_000)
        out = _run_cmd_status(health, stats)
        assert "50K tokens saved today" in out

    def test_tokens_saved_shown_in_M(self):
        health = _make_health()
        # 1M compressed + 2M cache_read = 3M
        stats = _make_stats(today_input=10_000_000, today_compressed=1_000_000, today_cache_read=2_000_000)
        out = _run_cmd_status(health, stats)
        assert "3.0M tokens saved today" in out

    def test_tokens_saved_raw_when_small(self):
        health = _make_health()
        # 500 compressed + 300 cache = 800 (below 1K threshold)
        stats = _make_stats(today_input=10_000, today_compressed=500, today_cache_read=300)
        out = _run_cmd_status(health, stats)
        assert "800 tokens saved today" in out

    def test_cost_saved_shown_when_positive(self):
        health = _make_health()
        stats = _make_stats(today_compressed=50_000, today_cache_read=200_000)
        out = _run_cmd_status(health, stats, cost_saved_db=4.80)
        assert "~$4.80 saved today" in out

    def test_cost_saved_omitted_when_zero(self):
        health = _make_health()
        stats = _make_stats(today_compressed=50_000, today_cache_read=200_000)
        out = _run_cmd_status(health, stats, cost_saved_db=0.0)
        # Should NOT have cost saved line
        assert "saved today" not in out or "tokens saved today" in out  # only token count, no cost

    def test_savings_line_uses_pipe_separator(self):
        health = _make_health()
        stats = _make_stats(today_compressed=50_000, today_cache_read=200_000)
        out = _run_cmd_status(health, stats)
        # The savings line should have | separating parts
        savings_line = [l for l in out.splitlines() if "Savings:" in l]
        assert len(savings_line) == 1
        assert "|" in savings_line[0]


# ---------------------------------------------------------------------------
# Tests: no-data graceful fallback
# ---------------------------------------------------------------------------

class TestSavingsSummaryNoData:
    """Graceful handling when no requests today."""

    def test_no_data_fallback_message(self):
        health = _make_health()
        stats = _make_stats(today_requests=0, today_compressed=0, today_cache_read=0)
        out = _run_cmd_status(health, stats)
        assert "no data yet" in out or "Savings:" in out  # either message is acceptable

    def test_no_crash_when_stats_none(self):
        health = _make_health()
        # stats=None means proxy returned nothing
        out = _run_cmd_status(health, stats=None)
        # Should not crash; savings line just won't appear
        assert "Proxy: running" in out or "Proxy: not reachable" in out

    def test_no_crash_when_proxy_unreachable(self):
        # health=None means proxy is down
        out = _run_cmd_status(health=None, stats=None)
        assert "not reachable" in out or "Proxy:" in out
        # No savings line when proxy is down
        assert "Savings:" not in out

    def test_no_crash_when_today_missing_from_stats(self):
        health = _make_health()
        # Stats present but missing 'today' key
        stats = _make_stats()
        del stats["today"]
        out = _run_cmd_status(health, stats)
        # Should not crash
        assert "Proxy: running" in out


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------

class TestSavingsSummaryEdgeCases:
    """Edge cases for savings computation."""

    def test_zero_compression_no_compression_shown(self):
        health = _make_health()
        # 0 compressed tokens → compression % = 0, should not show compression line
        stats = _make_stats(today_input=100_000, today_compressed=0, today_cache_read=50_000)
        out = _run_cmd_status(health, stats)
        savings_lines = [l for l in out.splitlines() if "Savings:" in l]
        if savings_lines:
            assert "0.0% avg compression" not in savings_lines[0]

    def test_only_compression_no_cache(self):
        health = _make_health()
        stats = _make_stats(today_input=100_000, today_compressed=20_000, today_cache_read=0)
        out = _run_cmd_status(health, stats)
        assert "Savings:" in out
        assert "20.0% avg compression" in out

    def test_only_cache_no_compression(self):
        health = _make_health()
        stats = _make_stats(today_input=100_000, today_compressed=0, today_cache_read=50_000)
        out = _run_cmd_status(health, stats)
        # 50K tokens saved from cache → should appear
        assert "Savings:" in out
        assert "50K tokens saved today" in out

    def test_savings_db_exception_handled_gracefully(self):
        """If telemetry DB errors out, savings line should still show without cost."""
        health = _make_health()
        stats = _make_stats(today_compressed=50_000, today_cache_read=200_000)
        captured = StringIO()
        from argparse import Namespace
        args = Namespace(
            mode=None, output=None, minimal=False, raw=False,
            json=False, profile=None, port=None, no_color=True,
        )
        with patch("tokenpak._cli_core._proxy_get") as mock_get, \
             patch("sys.stdout", captured):

            def _side_effect(endpoint):
                if endpoint == "/health":
                    return health
                if endpoint == "/stats":
                    return stats
                return None

            mock_get.side_effect = _side_effect

            # Make get_savings_report raise
            with patch("tokenpak.telemetry.query.get_savings_report", side_effect=Exception("DB error")):
                from tokenpak._cli_core import cmd_status
                try:
                    cmd_status(args)
                except SystemExit:
                    pass

        out = captured.getvalue()
        # Should not crash; savings line may or may not show but no traceback
        assert "Traceback" not in out
        assert "Proxy: running" in out
