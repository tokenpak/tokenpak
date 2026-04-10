"""
Tests for failover footer indicator (task F.6).
"""

import pytest
import logging
from datetime import datetime
from unittest.mock import patch

from tokenpak.telemetry.proxy_collector import RequestStats
from tokenpak.telemetry.footer import (
    render_footer,
    render_footer_oneline,
    render_footer_with_failover,
    render_footer_compact,
    log_failover_event,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def stats_no_failover():
    """Request stats with no failover."""
    return RequestStats(
        request_id="req-001",
        timestamp=datetime.now(),
        input_tokens_raw=1000,
        input_tokens_sent=800,
        tokens_saved=200,
        percent_saved=20.0,
        cost_saved=0.004,
        failover_chain=[],
        original_provider=None,
        final_provider=None,
    )


@pytest.fixture
def stats_single_failover():
    """Request stats with single failover (2-hop chain)."""
    return RequestStats(
        request_id="req-002",
        timestamp=datetime.now(),
        input_tokens_raw=1000,
        input_tokens_sent=800,
        tokens_saved=200,
        percent_saved=20.0,
        cost_saved=0.004,
        failover_chain=["anthropic", "openai"],
        original_provider="anthropic",
        final_provider="openai",
    )


@pytest.fixture
def stats_multi_failover():
    """Request stats with multi-hop failover (3-hop chain)."""
    return RequestStats(
        request_id="req-003",
        timestamp=datetime.now(),
        input_tokens_raw=1000,
        input_tokens_sent=800,
        tokens_saved=200,
        percent_saved=20.0,
        cost_saved=0.004,
        failover_chain=["anthropic", "openai", "google"],
        original_provider="anthropic",
        final_provider="google",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Failover Indicator Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFailoverIndicator:
    """Test the failover_indicator property on RequestStats."""

    def test_no_failover_returns_none(self, stats_no_failover):
        assert stats_no_failover.failover_indicator is None

    def test_single_provider_returns_none(self):
        """Single provider in chain = no failover (original succeeded)."""
        stats = RequestStats(
            request_id="req-x",
            timestamp=datetime.now(),
            input_tokens_raw=100,
            input_tokens_sent=100,
            tokens_saved=0,
            percent_saved=0,
            cost_saved=0,
            failover_chain=["anthropic"],  # Only one = no failover
        )
        assert stats.failover_indicator is None

    def test_two_providers_shows_chain(self, stats_single_failover):
        indicator = stats_single_failover.failover_indicator
        assert indicator == "⚠️ failover:anthropic→openai"

    def test_three_providers_shows_full_chain(self, stats_multi_failover):
        indicator = stats_multi_failover.failover_indicator
        assert indicator == "⚠️ failover:anthropic→openai→google"

    def test_arrow_separator(self, stats_multi_failover):
        indicator = stats_multi_failover.failover_indicator
        assert "→" in indicator
        assert indicator.count("→") == 2


# ─────────────────────────────────────────────────────────────────────────────
# Footer One-Line Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFooterOneline:
    """Test footer_oneline property includes failover when present."""

    def test_no_failover_clean_footer(self, stats_no_failover):
        line = stats_no_failover.footer_oneline
        assert "⚡ TokenPak:" in line
        assert "failover" not in line

    def test_single_failover_appended(self, stats_single_failover):
        line = stats_single_failover.footer_oneline
        assert "⚡ TokenPak:" in line
        assert "⚠️ failover:anthropic→openai" in line
        assert "|" in line  # Separator between stats and failover

    def test_multi_failover_appended(self, stats_multi_failover):
        line = stats_multi_failover.footer_oneline
        assert "anthropic→openai→google" in line


# ─────────────────────────────────────────────────────────────────────────────
# Render Footer Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderFooterWithFailover:
    """Test render_footer_with_failover function."""

    def test_no_indicator_same_as_base(self, stats_no_failover):
        base = render_footer(stats_no_failover)
        with_failover = render_footer_with_failover(stats_no_failover, None)
        assert base == with_failover

    def test_indicator_inserted_before_separator(self, stats_no_failover):
        indicator = "⚠️ failover:test→fallback"
        result = render_footer_with_failover(stats_no_failover, indicator)
        lines = result.split("\n")
        # Indicator should be second-to-last line (before final separator)
        assert indicator in lines[-2]
        assert lines[-1].startswith("─")  # Final line is separator

    def test_empty_indicator_ignored(self, stats_no_failover):
        base = render_footer(stats_no_failover)
        with_empty = render_footer_with_failover(stats_no_failover, "")
        assert base == with_empty


# ─────────────────────────────────────────────────────────────────────────────
# To Dict Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestToDictWithFailover:
    """Test to_dict includes failover fields when present."""

    def test_no_failover_omits_fields(self, stats_no_failover):
        d = stats_no_failover.to_dict()
        assert "failover_chain" not in d
        assert "original_provider" not in d
        assert "final_provider" not in d

    def test_failover_includes_all_fields(self, stats_single_failover):
        d = stats_single_failover.to_dict()
        assert d["failover_chain"] == ["anthropic", "openai"]
        assert d["original_provider"] == "anthropic"
        assert d["final_provider"] == "openai"
        assert d["failover_indicator"] == "⚠️ failover:anthropic→openai"


# ─────────────────────────────────────────────────────────────────────────────
# Logging Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFailoverLogging:
    """Test log_failover_event function."""

    def test_logs_at_info_level(self, caplog):
        with caplog.at_level(logging.INFO):
            log_failover_event(
                chain=["anthropic", "openai"],
                original="anthropic",
                final="openai",
                reason="rate_limit",
            )
        assert "Failover: anthropic→openai (rate_limit)" in caplog.text

    def test_logs_without_reason(self, caplog):
        with caplog.at_level(logging.INFO):
            log_failover_event(
                chain=["a", "b", "c"],
                original="a",
                final="c",
                reason="",
            )
        assert "Failover: a→b→c" in caplog.text
        assert "()" not in caplog.text  # No empty parens

    def test_chain_joined_with_arrow(self, caplog):
        with caplog.at_level(logging.INFO):
            log_failover_event(
                chain=["p1", "p2", "p3", "p4"],
                original="p1",
                final="p4",
            )
        assert "p1→p2→p3→p4" in caplog.text


# ─────────────────────────────────────────────────────────────────────────────
# Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_chain_no_indicator(self):
        stats = RequestStats(
            request_id="x",
            timestamp=datetime.now(),
            input_tokens_raw=100,
            input_tokens_sent=100,
            tokens_saved=0,
            percent_saved=0,
            cost_saved=0,
            failover_chain=[],
        )
        assert stats.failover_indicator is None
        assert "failover" not in stats.footer_oneline

    def test_zero_savings_with_failover(self):
        """Failover indicator should appear even when no token savings."""
        stats = RequestStats(
            request_id="x",
            timestamp=datetime.now(),
            input_tokens_raw=100,
            input_tokens_sent=100,
            tokens_saved=0,
            percent_saved=0,
            cost_saved=0,
            failover_chain=["a", "b"],
            original_provider="a",
            final_provider="b",
        )
        line = stats.footer_oneline
        assert "0 tokens saved" in line
        assert "⚠️ failover:a→b" in line

    def test_very_long_chain(self):
        """Test with unusually long failover chain."""
        chain = ["p1", "p2", "p3", "p4", "p5", "p6"]
        stats = RequestStats(
            request_id="x",
            timestamp=datetime.now(),
            input_tokens_raw=100,
            input_tokens_sent=100,
            tokens_saved=0,
            percent_saved=0,
            cost_saved=0,
            failover_chain=chain,
            original_provider="p1",
            final_provider="p6",
        )
        indicator = stats.failover_indicator
        assert indicator == "⚠️ failover:p1→p2→p3→p4→p5→p6"
        assert indicator.count("→") == 5
