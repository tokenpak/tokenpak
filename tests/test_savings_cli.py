"""Tests for tokenpak.agent.cli.commands.savings."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.agent.cli.commands.savings import (
    _estimate_cost_saved,
    _fmt_cost,
    _fmt_pct,
    _fmt_tokens,
    _period_to_days,
    _query_metrics_summary,
    cmd_show,
)
from tokenpak.telemetry.anon_metrics import MetricsRecord, MetricsStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_metrics_db():
    """Create a temporary metrics database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "metrics.db"
        store = MetricsStore(db_path=db_path)
        yield store, db_path


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


class TestPeriodToDays:
    """Test period string parsing."""

    def test_24h(self):
        assert _period_to_days("24h") == 1

    def test_7d(self):
        assert _period_to_days("7d") == 7

    def test_30d(self):
        assert _period_to_days("30d") == 30

    def test_lowercase_normalization(self):
        assert _period_to_days("7D") == 7
        assert _period_to_days("24H") == 1

    def test_aliases(self):
        assert _period_to_days("today") == 1
        assert _period_to_days("week") == 7
        assert _period_to_days("month") == 30

    def test_numeric_suffix(self):
        assert _period_to_days("14d") == 14
        assert _period_to_days("3d") == 3

    def test_invalid_defaults_to_1(self):
        assert _period_to_days("xyz") == 1
        assert _period_to_days("") == 1


class TestFormatters:
    """Test output formatting helpers."""

    def test_fmt_tokens_zero(self):
        assert _fmt_tokens(0) == "0"

    def test_fmt_tokens_thousands(self):
        assert _fmt_tokens(1000) == "1,000"
        assert _fmt_tokens(1_000_000) == "1,000,000"

    def test_fmt_pct_zero(self):
        assert _fmt_pct(0.0) == "0.0%"

    def test_fmt_pct_full(self):
        assert _fmt_pct(1.0) == "100.0%"

    def test_fmt_pct_partial(self):
        assert _fmt_pct(0.25) == "25.0%"

    def test_fmt_cost_small(self):
        result = _fmt_cost(0.005)
        assert result.startswith("$")
        assert "0.005" in result

    def test_fmt_cost_large(self):
        assert _fmt_cost(10.0) == "$10.00"
        assert _fmt_cost(1.5) == "$1.50"


class TestEstimateCostSaved:
    """Test cost estimation for saved tokens."""

    def test_gpt4o_estimate(self):
        # 1M input tokens @ $0.0025/1K
        cost = _estimate_cost_saved(1_000_000, "gpt-4o")
        assert abs(cost - 2.5) < 0.01

    def test_claude_haiku_estimate(self):
        # 1M input tokens @ $0.00025/1K
        cost = _estimate_cost_saved(1_000_000, "claude-haiku-3-5")
        assert abs(cost - 0.25) < 0.01

    def test_default_fallback(self):
        cost = _estimate_cost_saved(1000, "unknown-model")
        # Should use default gpt-4o rate
        assert cost > 0

    def test_prefix_matching(self):
        # "claude-sonnet-4-5-20250101" should match "claude-sonnet"
        cost1 = _estimate_cost_saved(1_000_000, "claude-sonnet-4-5")
        cost2 = _estimate_cost_saved(1_000_000, "claude-sonnet-4-5-20250101")
        assert abs(cost1 - cost2) < 0.01

    def test_zero_tokens(self):
        assert _estimate_cost_saved(0) == 0.0


# ---------------------------------------------------------------------------
# Integration tests with MetricsStore
# ---------------------------------------------------------------------------


class TestQueryMetricsSummary:
    """Test metrics aggregation from the store."""

    def test_empty_store(self, temp_metrics_db):
        """Empty metrics store returns zero values."""
        store, db_path = temp_metrics_db

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            result = _query_metrics_summary(days=30)

        assert result["request_count"] == 0
        assert result["tokens_saved"] == 0
        assert result["input_tokens_raw"] == 0
        assert result["compression_ratio"] == 0.0

    def test_single_record(self, temp_metrics_db):
        """Single metrics record is aggregated correctly."""
        store, db_path = temp_metrics_db

        # Insert one record
        rec = MetricsRecord(
            input_tokens=1000,
            output_tokens=100,
            tokens_saved=250,
            compression_ratio=0.25,
            latency_ms=150.0,
            model="gpt-4o",
        )
        store.record(rec)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            result = _query_metrics_summary(days=30)

        assert result["request_count"] == 1
        assert result["input_tokens_raw"] == 1000
        assert result["tokens_saved"] == 250
        assert abs(result["compression_ratio"] - 0.25) < 0.01
        assert result["avg_latency_ms"] == 150.0

    def test_multiple_records_aggregation(self, temp_metrics_db):
        """Multiple records are aggregated correctly."""
        store, db_path = temp_metrics_db

        # Insert multiple records
        for i in range(3):
            rec = MetricsRecord(
                input_tokens=1000,
                output_tokens=100,
                tokens_saved=200,
                compression_ratio=0.20,
                latency_ms=100.0,
                model="gpt-4o",
            )
            store.record(rec)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            result = _query_metrics_summary(days=30)

        assert result["request_count"] == 3
        assert result["input_tokens_raw"] == 3000
        assert result["tokens_saved"] == 600
        assert abs(result["compression_ratio"] - 0.20) < 0.01
        assert result["avg_latency_ms"] == 100.0

    def test_per_model_breakdown(self, temp_metrics_db):
        """Per-model stats are computed correctly."""
        store, db_path = temp_metrics_db

        # Insert records for multiple models
        for model, saved in [("gpt-4o", 100), ("claude-haiku", 50)]:
            for _ in range(2):
                rec = MetricsRecord(
                    input_tokens=1000,
                    output_tokens=100,
                    tokens_saved=saved,
                    compression_ratio=saved / 1000,
                    latency_ms=100.0,
                    model=model,
                )
                store.record(rec)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            result = _query_metrics_summary(days=30)

        assert len(result["per_model"]) == 2
        assert result["per_model"]["gpt-4o"]["tokens_saved"] == 200
        assert result["per_model"]["claude-haiku"]["tokens_saved"] == 100
        assert result["per_model"]["gpt-4o"]["requests"] == 2


class TestCmdShow:
    """Test cmd_show function."""

    def test_default_period_24h(self, temp_metrics_db, capsys):
        """Default period is 24h."""
        store, db_path = temp_metrics_db

        rec = MetricsRecord(
            input_tokens=1000,
            output_tokens=100,
            tokens_saved=200,
            compression_ratio=0.20,
            latency_ms=100.0,
            model="gpt-4o",
        )
        store.record(rec)

        args = MagicMock(period="24h", verbose=False, json=False)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            cmd_show(args)

        captured = capsys.readouterr()
        assert "Compression Savings" in captured.out
        assert "Tokens Saved:" in captured.out

    def test_verbose_output(self, temp_metrics_db, capsys):
        """Verbose flag shows per-model breakdown."""
        store, db_path = temp_metrics_db

        for model in ["gpt-4o", "claude-haiku"]:
            rec = MetricsRecord(
                input_tokens=1000,
                output_tokens=100,
                tokens_saved=100,
                compression_ratio=0.10,
                latency_ms=100.0,
                model=model,
            )
            store.record(rec)

        args = MagicMock(period="24h", verbose=True, json=False)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            cmd_show(args)

        captured = capsys.readouterr()
        assert "Per-Model Breakdown" in captured.out
        assert "gpt-4o" in captured.out
        assert "claude-haiku" in captured.out

    def test_json_output(self, temp_metrics_db, capsys):
        """JSON output is valid and complete."""
        store, db_path = temp_metrics_db

        rec = MetricsRecord(
            input_tokens=1000,
            output_tokens=100,
            tokens_saved=200,
            compression_ratio=0.20,
            latency_ms=100.0,
            model="gpt-4o",
        )
        store.record(rec)

        args = MagicMock(period="24h", verbose=False, json=True)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            cmd_show(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["request_count"] == 1
        assert output["tokens_saved"] == 200
        assert "compression_ratio" in output
        assert "per_model" in output

    def test_different_periods(self, temp_metrics_db, capsys):
        """Different periods are handled correctly."""
        store, db_path = temp_metrics_db

        rec = MetricsRecord(
            input_tokens=1000,
            output_tokens=100,
            tokens_saved=200,
            compression_ratio=0.20,
            latency_ms=100.0,
            model="gpt-4o",
        )
        store.record(rec)

        for period in ["24h", "7d", "30d"]:
            args = MagicMock(period=period, verbose=False, json=False)

            with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
                cmd_show(args)

            captured = capsys.readouterr()
            assert "Compression Savings" in captured.out


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_model_name(self, temp_metrics_db):
        """Records with empty model names are handled."""
        store, db_path = temp_metrics_db

        rec = MetricsRecord(
            input_tokens=1000,
            output_tokens=100,
            tokens_saved=200,
            compression_ratio=0.20,
            latency_ms=100.0,
            model="",  # Empty model name
        )
        store.record(rec)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            result = _query_metrics_summary(days=30)

        assert result["request_count"] == 1
        assert "" in result["per_model"]

    def test_zero_input_tokens(self, temp_metrics_db):
        """Zero input tokens don't cause division errors."""
        store, db_path = temp_metrics_db

        rec = MetricsRecord(
            input_tokens=0,
            output_tokens=100,
            tokens_saved=0,
            compression_ratio=0.0,
            latency_ms=100.0,
            model="gpt-4o",
        )
        store.record(rec)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            result = _query_metrics_summary(days=30)

        assert result["compression_ratio"] == 0.0
        assert result["tokens_saved"] == 0

    def test_large_token_counts(self, temp_metrics_db):
        """Large token counts are handled correctly."""
        store, db_path = temp_metrics_db

        rec = MetricsRecord(
            input_tokens=100_000_000,
            output_tokens=50_000_000,
            tokens_saved=25_000_000,
            compression_ratio=0.25,
            latency_ms=5000.0,
            model="gpt-4o",
        )
        store.record(rec)

        with patch("tokenpak.telemetry.anon_metrics.get_store", return_value=store):
            result = _query_metrics_summary(days=30)

        assert result["input_tokens_raw"] == 100_000_000
        assert result["tokens_saved"] == 25_000_000
        assert abs(result["compression_ratio"] - 0.25) < 0.01
