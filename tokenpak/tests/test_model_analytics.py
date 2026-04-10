"""test_model_analytics.py — Tests for tokenpak.models (ModelStats, ModelAnalyzer, get_model_pricing).

Uses sys.modules patching to bypass FastAPI/Starlette incompatibility in the import chain.
"""

import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch the broken ingest + proxy import chain
# ---------------------------------------------------------------------------
_fake_ingest = types.ModuleType("tokenpak._internal.ingest")
_fake_sc = types.ModuleType("tokenpak._internal.ingest.schema_converter")
_fake_sc.should_serve_schema = lambda intent: False
_fake_sc.convert_document = MagicMock(return_value={})
_fake_ingest.schema_converter = _fake_sc
sys.modules.setdefault("tokenpak._internal.ingest", _fake_ingest)
sys.modules.setdefault("tokenpak._internal.ingest.schema_converter", _fake_sc)
sys.modules.setdefault("tokenpak._internal.ingest.api", MagicMock())

from tokenpak.models import (  # noqa: E402
    ModelAnalyzer,
    ModelStats,
    get_model_pricing,
)


# ---------------------------------------------------------------------------
# get_model_pricing
# ---------------------------------------------------------------------------

class TestGetModelPricing:
    def test_exact_match(self):
        pricing = get_model_pricing("gpt-4o")
        assert "input" in pricing
        assert "output" in pricing
        assert pricing["input"] > 0
        assert pricing["output"] > 0

    def test_fuzzy_match_claude_sonnet(self):
        pricing = get_model_pricing("claude-3-5-sonnet-20250319")
        assert pricing == get_model_pricing("claude-3-5-sonnet")

    def test_unknown_model_fallback(self):
        pricing = get_model_pricing("totally-unknown-xyz")
        assert pricing["input"] > 0
        assert pricing["output"] > 0

    def test_gpt4_fallback(self):
        pricing = get_model_pricing("gpt-4-unknown-variant")
        assert pricing["input"] > 0

    def test_claude_opus_fallback(self):
        pricing = get_model_pricing("claude-opus-vX")
        assert pricing["input"] == 15.0
        assert pricing["output"] == 75.0

    def test_claude_haiku_fallback(self):
        pricing = get_model_pricing("claude-haiku-vX")
        assert pricing["input"] == 0.25

    def test_gemini_fallback(self):
        pricing = get_model_pricing("gemini-ultra-v99")
        assert pricing["input"] == 0.50

    def test_llama_fallback(self):
        pricing = get_model_pricing("llama-3-70b-instruct")
        assert pricing["input"] == 0.75


# ---------------------------------------------------------------------------
# ModelStats
# ---------------------------------------------------------------------------

class TestModelStats:
    def _make_stats(self, **kwargs):
        defaults = dict(
            model_name="test-model",
            requests=10,
            input_tokens=100_000,
            output_tokens=20_000,
            cache_hits=3,
            cache_read_tokens=10_000,
            errors=1,
            total_latency_ms=5000,
        )
        defaults.update(kwargs)
        return ModelStats(**defaults)

    def test_avg_latency_computed(self):
        stats = self._make_stats(requests=5, total_latency_ms=10000)
        result = stats.to_dict()
        assert result["avg_latency_ms"] == 2000

    def test_avg_latency_zero_requests(self):
        stats = ModelStats(model_name="x", requests=0)
        assert stats.to_dict()["avg_latency_ms"] == 0

    def test_cache_hit_rate(self):
        stats = self._make_stats(requests=10, cache_hits=5)
        result = stats.to_dict()
        assert result["cache_hit_rate"] == 50.0

    def test_cache_hit_rate_zero_requests(self):
        stats = ModelStats(model_name="x", requests=0)
        assert stats.to_dict()["cache_hit_rate"] == 0.0

    def test_compression_efficiency(self):
        stats = self._make_stats(input_tokens=90_000, cache_read_tokens=10_000)
        result = stats.to_dict()
        assert result["compression_efficiency"] > 0

    def test_compression_efficiency_zero_input(self):
        stats = ModelStats(model_name="x")
        assert stats.to_dict()["compression_efficiency"] == 0.0

    def test_cost_metrics_keys(self):
        stats = self._make_stats()
        costs = stats.to_dict()["cost_metrics"]
        assert "sent" in costs
        assert "saved" in costs
        assert "net" in costs

    def test_cost_metrics_cache_saves_money(self):
        stats = self._make_stats(
            model_name="gpt-4o",
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=500_000,
        )
        costs = stats.to_dict()["cost_metrics"]
        assert costs["saved"] > 0
        assert costs["net"] < costs["sent"]

    def test_to_dict_contains_all_keys(self):
        stats = self._make_stats()
        d = stats.to_dict()
        expected_keys = {
            "model", "requests", "input_tokens", "output_tokens",
            "cache_hits", "cache_read_tokens", "errors",
            "avg_latency_ms", "cache_hit_rate", "compression_efficiency", "cost_metrics",
        }
        assert expected_keys.issubset(d.keys())

    def test_zero_stats(self):
        stats = ModelStats(model_name="empty-model")
        d = stats.to_dict()
        assert d["requests"] == 0
        assert d["cache_hit_rate"] == 0.0
        assert d["compression_efficiency"] == 0.0


# ---------------------------------------------------------------------------
# ModelAnalyzer
# ---------------------------------------------------------------------------

class TestModelAnalyzer:
    def _write_events(self, tmp_path: Path, events: list) -> Path:
        log_file = tmp_path / "stats.jsonl"
        with open(log_file, "w") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")
        return log_file

    def test_load_empty_file_returns_empty(self, tmp_path):
        log_file = self._write_events(tmp_path, [])
        analyzer = ModelAnalyzer(log_path=str(log_file))
        result = analyzer.load_from_file()
        assert result == {}

    def test_load_single_event(self, tmp_path):
        events = [{
            "model": "claude-sonnet-4-5",
            "status": "ok",
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_hits": 0,
            "cache_read_tokens": 0,
            "latency_ms": 500,
        }]
        log_file = self._write_events(tmp_path, events)
        analyzer = ModelAnalyzer(log_path=str(log_file))
        result = analyzer.load_from_file()
        assert "claude-sonnet-4-5" in result
        stats = result["claude-sonnet-4-5"]
        assert stats.requests == 1
        assert stats.input_tokens == 1000

    def test_load_multiple_models(self, tmp_path):
        events = [
            {"model": "gpt-4o", "status": "ok", "input_tokens": 500, "output_tokens": 100,
             "cache_hits": 0, "cache_read_tokens": 0, "latency_ms": 200},
            {"model": "claude-haiku-4-5", "status": "ok", "input_tokens": 300, "output_tokens": 50,
             "cache_hits": 1, "cache_read_tokens": 100, "latency_ms": 100},
        ]
        log_file = self._write_events(tmp_path, events)
        analyzer = ModelAnalyzer(log_path=str(log_file))
        result = analyzer.load_from_file()
        assert len(result) == 2
        assert "gpt-4o" in result
        assert "claude-haiku-4-5" in result

    def test_error_events_counted_separately(self, tmp_path):
        events = [
            {"model": "gpt-4o", "status": "ok", "input_tokens": 500, "output_tokens": 100,
             "cache_hits": 0, "cache_read_tokens": 0, "latency_ms": 200},
            {"model": "gpt-4o", "status": "error"},
        ]
        log_file = self._write_events(tmp_path, events)
        analyzer = ModelAnalyzer(log_path=str(log_file))
        result = analyzer.load_from_file()
        stats = result["gpt-4o"]
        assert stats.requests == 1
        assert stats.errors == 1

    def test_get_summary_empty(self, tmp_path):
        log_file = self._write_events(tmp_path, [])
        analyzer = ModelAnalyzer(log_path=str(log_file))
        analyzer.load_from_file()
        summary = analyzer.get_summary()
        assert summary["total_requests"] == 0
        assert summary["total_models"] == 0
        assert summary["total_cost_sent"] == 0.0

    def test_get_summary_with_data(self, tmp_path):
        events = [
            {"model": "claude-sonnet-4-5", "status": "ok", "input_tokens": 1_000_000,
             "output_tokens": 100_000, "cache_hits": 2, "cache_read_tokens": 50_000,
             "latency_ms": 800},
        ]
        log_file = self._write_events(tmp_path, events)
        analyzer = ModelAnalyzer(log_path=str(log_file))
        analyzer.load_from_file()
        summary = analyzer.get_summary()
        assert summary["total_requests"] == 1
        assert summary["total_models"] == 1
        assert summary["total_cost_sent"] > 0
        assert summary["overall_cache_hit_rate"] == 200.0  # 2 hits / 1 request * 100

    def test_get_summary_cost_saved(self, tmp_path):
        events = [
            {"model": "gpt-4o", "status": "ok", "input_tokens": 1_000_000,
             "output_tokens": 0, "cache_hits": 0, "cache_read_tokens": 500_000,
             "latency_ms": 300},
        ]
        log_file = self._write_events(tmp_path, events)
        analyzer = ModelAnalyzer(log_path=str(log_file))
        analyzer.load_from_file()
        summary = analyzer.get_summary()
        assert summary["total_cost_saved"] > 0
        assert summary["total_cost_net"] < summary["total_cost_sent"]

    def test_nonexistent_file_handled(self):
        analyzer = ModelAnalyzer(log_path="/tmp/nonexistent_stats.jsonl")
        result = analyzer.load_from_file()
        # Should return empty dict or raise — both acceptable
        assert isinstance(result, dict)
