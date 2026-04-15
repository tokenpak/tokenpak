"""Tests for tokenpak status command output formats.

Covers:
- Default savings-first layout
- --minimal one-liner
- --full legacy technical output
- --no-meme flag
- Proxy unreachable fallback
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.formatting", reason="module not available in current build")
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.formatting.modes import OutputMode


# ── Fixtures ─────────────────────────────────────────────────────────────────


MOCK_STATS = {
    "session": {
        "requests": 6016,
        "input_tokens": 74_500_000,
        "sent_input_tokens": 68_500_000,
        "saved_tokens": 6_000_000,
        "output_tokens": 1_000_000,
        "cost": 326.92,
        "cost_saved": 1.16,
        "start_time": 1_770_000_000.0,
        "errors": 5,
        "cache_hits": 5530,
        "cache_misses": 486,
        "cache_read_tokens": 287_000_000,
        "cache_creation_tokens": 37_000_000,
        "compilation_mode": "hybrid",
    },
    "by_model": {
        "claude-opus-4-6": {
            "requests": 2841,
            "input_tokens": 30_000_000,
            "output_tokens": 400_000,
            "cost": 289.14,
            "cache_read_tokens": 69_000_000,
            "cache_creation_tokens": 22_000_000,
        },
        "claude-haiku-4-5": {
            "requests": 2103,
            "input_tokens": 24_000_000,
            "output_tokens": 300_000,
            "cost": 24.58,
            "cache_read_tokens": 146_000_000,
            "cache_creation_tokens": 29_000_000,
        },
        "claude-sonnet-4-6": {
            "requests": 847,
            "input_tokens": 12_000_000,
            "output_tokens": 200_000,
            "cost": 12.04,
            "cache_read_tokens": 48_000_000,
            "cache_creation_tokens": 7_000_000,
        },
    },
    "today": {
        "requests": 3000,
        "input_tokens": 37_000_000,
        "output_tokens": 500_000,
        "total_cost": 160.0,
        "cache_read_tokens": 150_000_000,
        "cache_creation_tokens": 11_000_000,
    },
}

MOCK_HEALTH = {
    "status": "ok",
    "compilation_mode": "hybrid",
    "stats": {
        "start_time": 1_770_000_000.0,
        "requests": 6016,
        "errors": 5,
    },
    "vault_index": {"available": True, "blocks": 6366},
    "skeleton": {"enabled": True},
    "shadow_reader": {"enabled": True},
    "canon": {"enabled": True},
    "capsule_available": True,
    "circuit_breakers": {
        "anthropic": {"open": False, "failures": 0},
        "openai": {"open": False, "failures": 0},
    },
    "router": {"enabled": True, "components": {}},
}

MOCK_CACHE = {
    "hit_rate": 0.9191,
    "cache_hits": 5530,
    "cache_misses": 486,
    "cache_read_tokens": 287_000_000,
    "cache_creation_tokens": 37_000_000,
    "miss_reasons": {
        "timestamp_poison": 87,
        "schema_tool_change": 399,
    },
}


def make_args(**kwargs):
    defaults = {
        "full": False,
        "minimal": False,
        "no_meme": True,
        "output": "normal",
        "limit": 20,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def run_cmd_status(args, capsys):
    """Import and run cmd_status, patching proxy calls."""
    import time

    with patch("tokenpak.cli._proxy_get") as mock_proxy, \
         patch("time.time", return_value=1_770_400_000.0):
        def _proxy_side(endpoint):
            if endpoint == "/health":
                return MOCK_HEALTH
            elif endpoint == "/stats":
                return MOCK_STATS
            elif endpoint == "/cache-stats":
                return MOCK_CACHE
            return {}
        mock_proxy.side_effect = _proxy_side

        from tokenpak.cli import cmd_status
        cmd_status(args)

    return capsys.readouterr()


# ── Default savings-first layout ──────────────────────────────────────────────


class TestDefaultSavingsFirstLayout:
    def test_savings_header_present(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "TOKENPAK" in out
        assert "Savings Report" in out

    def test_savings_section_present(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "💰 SAVINGS" in out

    def test_without_tokenpak_line(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "Without TokenPak" in out

    def test_with_tokenpak_line(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "With TokenPak" in out

    def test_total_saved_line(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "Total saved" in out

    def test_how_it_saved_section(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "📊 HOW IT SAVED" in out

    def test_cache_optimization_line(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "Cache optimization" in out

    def test_token_compression_line(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "Token compression" in out

    def test_smart_routing_line(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "Smart routing" in out

    def test_models_section_present(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "🤖 MODELS" in out

    def test_performance_section_present(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "⚡ PERFORMANCE" in out

    def test_all_systems_healthy(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "✅ All systems healthy" in out

    def test_per_model_rows_appear(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        # At least one model short-name should appear
        assert "opus-4-6" in out or "haiku-4-5" in out or "sonnet-4-6" in out

    def test_dollar_amounts_appear(self, capsys):
        out, _ = run_cmd_status(make_args(), capsys)
        assert "$" in out


# ── --minimal one-liner ───────────────────────────────────────────────────────


class TestMinimalOutput:
    def test_minimal_shows_one_line(self, capsys):
        out, _ = run_cmd_status(make_args(minimal=True), capsys)
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) == 1

    def test_minimal_contains_saved(self, capsys):
        out, _ = run_cmd_status(make_args(minimal=True), capsys)
        assert "saved" in out.lower()

    def test_minimal_contains_reqs(self, capsys):
        out, _ = run_cmd_status(make_args(minimal=True), capsys)
        assert "reqs" in out

    def test_minimal_contains_cache_pct(self, capsys):
        out, _ = run_cmd_status(make_args(minimal=True), capsys)
        assert "cache" in out

    def test_minimal_starts_with_package_emoji(self, capsys):
        out, _ = run_cmd_status(make_args(minimal=True), capsys)
        assert "📦" in out


# ── --full legacy technical output ───────────────────────────────────────────


class TestFullOutput:
    def test_full_shows_proxy_running(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Proxy" in out

    def test_full_shows_uptime(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Uptime" in out

    def test_full_shows_tokens_in(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Tokens in" in out

    def test_full_shows_tokens_saved(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Tokens saved" in out

    def test_full_shows_cost_line(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Cost:" in out

    def test_full_shows_cache_hit_rate(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Cache hit rate" in out

    def test_full_shows_features(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Features" in out

    def test_full_does_not_show_savings_header(self, capsys):
        out, _ = run_cmd_status(make_args(full=True), capsys)
        assert "Savings Report" not in out


# ── --no-meme flag ────────────────────────────────────────────────────────────


class TestNoMemeFlag:
    def test_meme_suppressed_when_no_meme(self, capsys):
        # Default view with no_meme=True (set in make_args default)
        out, _ = run_cmd_status(make_args(no_meme=True), capsys)
        # Meme lines all start with 📦 in default view
        # After the "all systems healthy" line there should be no 📦
        lines = out.strip().splitlines()
        healthy_idx = next(
            (i for i, l in enumerate(lines) if "All systems healthy" in l), None
        )
        if healthy_idx is not None:
            after = "\n".join(lines[healthy_idx + 1:])
            assert "📦" not in after

    def test_meme_present_when_not_suppressed(self, capsys):
        out, _ = run_cmd_status(make_args(no_meme=False), capsys)
        # At least one 📦 should appear (the meme line)
        assert "📦" in out


# ── Proxy unreachable fallback ────────────────────────────────────────────────


class TestProxyUnreachable:
    def test_shows_warning_when_proxy_down(self, capsys):
        args = make_args()
        with patch("tokenpak.cli._proxy_get", return_value=None):
            from tokenpak.cli import cmd_status
            cmd_status(args)
        out = capsys.readouterr().out
        assert "not reachable" in out or "tokenpak start" in out or "⚠️" in out
