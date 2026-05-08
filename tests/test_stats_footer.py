"""
Tests for the TokenPak stats footer feature.

Covers:
- Footer renders correctly with real compression data
- Footer disabled (TOKENPAK_STATS_FOOTER=0) suppresses output
- Zero-compression edge case produces correct message
- config.get_stats_footer_enabled() respects env var priority
- config.get_stats_footer_enabled() reads from config file when no env var set
- Footer line is printed to stderr on a live proxy request (integration smoke)
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# WS-A residual import guard — TSR-01-followup.
# tokenpak._internal is the canonical closed-source namespace per
# Std 25 §1.1 + Std 32 §1.3 (slim OSS surface excludes
# `tokenpak._internal/*`). Tests in this file reach into it via the
# stats-footer integration test; skip cleanly on slim install.
pytest.importorskip(
    "tokenpak._internal",
    reason="tokenpak._internal is closed-source per Std 25 §1.1 — absent on slim OSS",
)

from tokenpak.telemetry.proxy_collector import RequestStats, TelemetryCollector
from tokenpak.telemetry.footer import (
    render_footer_oneline,
    render_footer,
    render_footer_compact,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stats(
    tokens_raw: int = 1715,
    tokens_sent: int = 1403,
    cost_saved: float = 0.003,
) -> RequestStats:
    saved = max(0, tokens_raw - tokens_sent)
    pct = round(saved / tokens_raw * 100, 1) if tokens_raw > 0 else 0.0
    return RequestStats(
        request_id="req-test-001",
        timestamp=datetime.now(),
        input_tokens_raw=tokens_raw,
        input_tokens_sent=tokens_sent,
        tokens_saved=saved,
        percent_saved=pct,
        cost_saved=cost_saved,
    )


# ---------------------------------------------------------------------------
# 1. Footer with real compression data
# ---------------------------------------------------------------------------

class TestFooterWithRealData:

    def test_oneline_contains_tokens_saved(self):
        stats = _make_stats(tokens_raw=1715, tokens_sent=1403)
        line = render_footer_oneline(stats)
        assert "312" in line, f"Expected 312 in: {line}"

    def test_oneline_contains_percent(self):
        stats = _make_stats(tokens_raw=1715, tokens_sent=1403)
        line = render_footer_oneline(stats)
        # 312/1715 ≈ 18%
        assert "18" in line, f"Expected ~18% in: {line}"

    def test_oneline_contains_cost(self):
        stats = _make_stats(tokens_raw=1715, tokens_sent=1403, cost_saved=0.003)
        line = render_footer_oneline(stats)
        assert "0.003" in line, f"Expected cost 0.003 in: {line}"

    def test_oneline_is_single_line(self):
        stats = _make_stats()
        line = render_footer_oneline(stats)
        assert "\n" not in line, "One-liner should not contain newlines"

    def test_multiline_footer_has_separator(self):
        stats = _make_stats()
        block = render_footer(stats)
        assert "─" in block, "Multi-line footer missing separator"

    def test_multiline_footer_with_session(self):
        from tokenpak.telemetry.proxy_collector import SessionStats
        stats = _make_stats()
        session = SessionStats(
            session_requests=5,
            session_total_tokens_raw=8000,
            session_total_tokens_sent=6400,
            session_total_saved=1600,
            session_total_cost_saved=0.015,
        )
        block = render_footer(stats, session=session)
        assert "5 reqs" in block
        assert "1,600" in block

    def test_compact_footer_prefix(self):
        stats = _make_stats(tokens_raw=1000, tokens_sent=800)
        compact = render_footer_compact(stats)
        assert compact.startswith("⚡-")
        assert "200" in compact

    def test_footer_negative_delta_clipped_to_zero(self):
        """If sent > raw (shouldn't happen, but edge case), tokens_saved=0."""
        stats = _make_stats(tokens_raw=100, tokens_sent=150)
        # tokens_saved is set in _make_stats via max(0, ...)
        assert stats.tokens_saved == 0
        line = render_footer_oneline(stats)
        assert "0 tokens saved" in line


# ---------------------------------------------------------------------------
# 2. Footer disabled case
# ---------------------------------------------------------------------------

class TestFooterDisabled:

    def test_get_stats_footer_disabled_by_env_var_zero(self):
        from tokenpak._internal.config import get_stats_footer_enabled
        with patch.dict(os.environ, {"TOKENPAK_STATS_FOOTER": "0"}):
            assert get_stats_footer_enabled() is False

    def test_get_stats_footer_disabled_by_env_var_false(self):
        from tokenpak._internal.config import get_stats_footer_enabled
        with patch.dict(os.environ, {"TOKENPAK_STATS_FOOTER": "false"}):
            assert get_stats_footer_enabled() is False

    def test_get_stats_footer_disabled_by_default(self):
        """Without any config or env var, footer is off (opt-in)."""
        from tokenpak._internal.config import get_stats_footer_enabled
        env = {k: v for k, v in os.environ.items() if k != "TOKENPAK_STATS_FOOTER"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tokenpak._internal.config._load", return_value={}):
                assert get_stats_footer_enabled() is False

    def test_get_stats_footer_enabled_by_env_var(self):
        from tokenpak._internal.config import get_stats_footer_enabled
        with patch.dict(os.environ, {"TOKENPAK_STATS_FOOTER": "1"}):
            assert get_stats_footer_enabled() is True

    def test_get_stats_footer_enabled_by_config_file(self):
        from tokenpak._internal.config import get_stats_footer_enabled
        env = {k: v for k, v in os.environ.items() if k != "TOKENPAK_STATS_FOOTER"}
        with patch.dict(os.environ, env, clear=True):
            with patch("tokenpak._internal.config._load", return_value={"stats_footer": True}):
                assert get_stats_footer_enabled() is True

    def test_env_var_overrides_config_file(self):
        """Env var=0 wins even if config file says True."""
        from tokenpak._internal.config import get_stats_footer_enabled
        with patch.dict(os.environ, {"TOKENPAK_STATS_FOOTER": "0"}):
            with patch("tokenpak._internal.config._load", return_value={"stats_footer": True}):
                assert get_stats_footer_enabled() is False


# ---------------------------------------------------------------------------
# 3. Zero-compression edge case
# ---------------------------------------------------------------------------

class TestZeroCompression:

    def test_oneline_zero_compression_message(self):
        stats = _make_stats(tokens_raw=500, tokens_sent=500, cost_saved=0.0)
        line = render_footer_oneline(stats)
        assert "0 tokens saved" in line, f"Expected zero-tokens message, got: {line}"

    def test_compact_zero_compression(self):
        stats = _make_stats(tokens_raw=500, tokens_sent=500, cost_saved=0.0)
        compact = render_footer_compact(stats)
        assert compact == "⚡0", f"Expected '⚡0', got: {compact}"

    def test_multiline_zero_compression_no_crash(self):
        stats = _make_stats(tokens_raw=0, tokens_sent=0, cost_saved=0.0)
        block = render_footer(stats)
        assert "0 tokens saved" in block


# ---------------------------------------------------------------------------
# 4. TelemetryCollector integration
# ---------------------------------------------------------------------------

class TestCollectorFooter:

    def test_record_returns_stats_with_footer(self):
        collector = TelemetryCollector()
        stats = collector.record(
            request_id="req-x",
            input_tokens_raw=2000,
            input_tokens_sent=1500,
            cost_saved=0.005,
        )
        assert stats.tokens_saved == 500
        assert stats.percent_saved == pytest.approx(25.0, abs=0.1)
        line = render_footer_oneline(stats)
        assert "500" in line
        assert "25" in line

    def test_collector_get_last_provides_footer(self):
        collector = TelemetryCollector()
        collector.record("r1", 1000, 800, 0.002)
        last = collector.get_last()
        assert last is not None
        assert render_footer_oneline(last) is not None


# ---------------------------------------------------------------------------
# 5. config set/get round-trip
# ---------------------------------------------------------------------------

class TestConfigRoundTrip:

    def test_set_and_get_stats_footer_true(self, tmp_path):
        config_file = tmp_path / "config.json"
        with patch("tokenpak._internal.config.CONFIG_PATH", config_file):
            from tokenpak._internal.config import set_config, _load
            set_config("stats_footer", True)
            data = _load()
            assert data["stats_footer"] is True

    def test_set_and_get_stats_footer_false(self, tmp_path):
        config_file = tmp_path / "config.json"
        with patch("tokenpak._internal.config.CONFIG_PATH", config_file):
            from tokenpak._internal.config import set_config, _load
            set_config("stats_footer", False)
            data = _load()
            assert data["stats_footer"] is False

    def test_load_returns_empty_if_missing(self, tmp_path):
        config_file = tmp_path / "nonexistent.json"
        with patch("tokenpak._internal.config.CONFIG_PATH", config_file):
            from tokenpak._internal.config import _load
            assert _load() == {}

    def test_load_returns_empty_on_corrupt_json(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT JSON {{{")
        with patch("tokenpak._internal.config.CONFIG_PATH", config_file):
            from tokenpak._internal.config import _load
            assert _load() == {}
