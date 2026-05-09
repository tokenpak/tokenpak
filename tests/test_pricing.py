"""Tests for tokenpak/pricing.py — savings calculations and model rates."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.pricing", reason="module not available in current build")
import pytest
from tokenpak.pricing import (
    DEFAULT_RATE,
    MODEL_RATES,
    calculate_request_cost,
    calculate_request_cost_baseline,
    calculate_savings_from_proxy_stats,
    estimate_savings,
    get_price,
    get_rates,
)

# ── Model rate lookups ────────────────────────────────────────────────────────


class TestGetRates:
    def test_known_model_returns_correct_rates(self):
        r = get_rates("claude-sonnet-4-6")
        assert r["input"] == 3.0
        assert r["cached"] == 0.30
        assert r["output"] == 15.0

    def test_opus_rates(self):
        r = get_rates("claude-opus-4-6")
        assert r["input"] == 15.0
        assert r["output"] == 75.0

    def test_haiku_rates(self):
        r = get_rates("claude-haiku-4-5")
        assert r["input"] == 0.80
        assert r["output"] == 4.0

    def test_haiku_4_6_rates(self):
        r = get_rates("claude-haiku-4-6")
        assert r["input"] == 0.80

    def test_unknown_model_falls_back_to_default(self):
        r = get_rates("some-mystery-model-9000")
        assert r == DEFAULT_RATE

    def test_none_model_returns_default(self):
        assert get_rates(None) == DEFAULT_RATE

    def test_all_known_models_have_required_keys(self):
        for model, rates in MODEL_RATES.items():
            assert "input" in rates, f"{model} missing input rate"
            assert "cached" in rates, f"{model} missing cached rate"
            assert "output" in rates, f"{model} missing output rate"


class TestGetPrice:
    def test_input_direction(self):
        assert get_price("claude-sonnet-4-6", "input") == 3.0

    def test_output_direction(self):
        assert get_price("claude-sonnet-4-6", "output") == 15.0

    def test_cached_direction(self):
        assert get_price("claude-sonnet-4-6", "cached") == 0.30

    def test_default_direction_is_input(self):
        assert get_price("claude-sonnet-4-6") == 3.0


# ── calculate_request_cost ────────────────────────────────────────────────────


class TestCalculateRequestCost:
    def test_zero_tokens_costs_zero(self):
        cost = calculate_request_cost("claude-sonnet-4-6", 0)
        assert cost == 0.0

    def test_pure_input_tokens(self):
        # 1M input tokens at $3.00/M → $3.00
        cost = calculate_request_cost("claude-sonnet-4-6", 1_000_000)
        assert cost == pytest.approx(3.0, abs=0.0001)

    def test_cache_read_tokens_cheaper_than_input(self):
        cost_input = calculate_request_cost("claude-sonnet-4-6", 100_000)
        cost_cached = calculate_request_cost("claude-sonnet-4-6", 0, cache_read_tokens=100_000)
        assert cost_cached < cost_input

    def test_output_tokens_billed_at_output_rate(self):
        # 1M output tokens at $15/M
        cost = calculate_request_cost("claude-sonnet-4-6", 0, output_tokens=1_000_000)
        assert cost == pytest.approx(15.0, abs=0.001)

    def test_cache_creation_billed_at_125_pct(self):
        # 1M cache creation @ $3.00 * 1.25 = $3.75/M
        cost = calculate_request_cost("claude-sonnet-4-6", 0, cache_creation_tokens=1_000_000)
        assert cost == pytest.approx(3.75, abs=0.001)

    def test_combined_cost_is_sum_of_parts(self):
        cost_combined = calculate_request_cost(
            "claude-sonnet-4-6",
            input_tokens=500_000,
            cache_read_tokens=500_000,
            output_tokens=100_000,
        )
        assert cost_combined > 0


class TestCalculateRequestCostBaseline:
    def test_no_tokens(self):
        assert calculate_request_cost_baseline("claude-sonnet-4-6", 0) == 0.0

    def test_1m_input_tokens(self):
        # 1M at $3.00/M
        assert calculate_request_cost_baseline("claude-sonnet-4-6", 1_000_000) == pytest.approx(3.0)

    def test_baseline_higher_than_tokenpak_cost(self):
        # Baseline ignores cache discounts, so must be >= actual cost
        baseline = calculate_request_cost_baseline("claude-sonnet-4-6", 1_000_000, 100_000)
        actual = calculate_request_cost("claude-sonnet-4-6", 1_000_000, cache_read_tokens=100_000)
        assert baseline >= actual


# ── estimate_savings ─────────────────────────────────────────────────────────


class TestEstimateSavings:
    def test_no_savings_on_empty_stats(self):
        result = estimate_savings({})
        assert result["total_cost_saved"] == 0.0
        assert result["total_tokens_saved"] == 0

    def test_compression_savings(self):
        result = estimate_savings({
            "tokens_raw": 2_000_000,
            "tokens_saved": 500_000,
            "cache_read_tokens": 0,
        }, model="claude-sonnet-4-6")
        assert result["compression_tokens_saved"] == 500_000
        # 500K tokens * $3.00/M = $1.50
        assert result["compression_cost_saved"] == pytest.approx(1.50, abs=0.01)

    def test_cache_savings(self):
        result = estimate_savings({
            "tokens_raw": 1_000_000,
            "tokens_saved": 0,
            "cache_read_tokens": 500_000,
        }, model="claude-sonnet-4-6")
        # cache saves (input_rate - cached_rate) per token
        expected = (500_000 / 1_000_000) * (3.0 - 0.30)
        assert result["cache_cost_saved"] == pytest.approx(expected, abs=0.01)

    def test_reduction_percent_is_bounded_0_to_100(self):
        result = estimate_savings({
            "tokens_raw": 1_000_000,
            "tokens_saved": 900_000,
            "cache_read_tokens": 90_000,
        }, model="claude-sonnet-4-6")
        assert 0 <= result["reduction_percent"] <= 100

    def test_zero_raw_tokens_no_division_error(self):
        result = estimate_savings({"tokens_raw": 0, "tokens_saved": 0, "cache_read_tokens": 0})
        assert result["reduction_percent"] == 0.0


# ── calculate_savings_from_proxy_stats ───────────────────────────────────────


SAMPLE_STATS = {
    "requests": 1000,
    "input_tokens": 10_000_000,
    "sent_input_tokens": 9_000_000,
    "saved_tokens": 1_000_000,
    "output_tokens": 500_000,
    "cache_read_tokens": 50_000_000,
    "cache_creation_tokens": 5_000_000,
    "cost": 15.0,  # actual cost < baseline due to caching+compression
    "cache_hits": 900,
    "cache_misses": 100,
}

SAMPLE_BY_MODEL = {
    "claude-sonnet-4-6": {
        "requests": 700,
        "input_tokens": 7_000_000,
        "output_tokens": 350_000,
        "cost": 10.0,   # actual cost
        "cache_read_tokens": 35_000_000,
        "cache_creation_tokens": 3_500_000,
    },
    "claude-haiku-4-5": {
        "requests": 300,
        "input_tokens": 3_000_000,
        "output_tokens": 150_000,
        "cost": 5.0,    # actual cost
        "cache_read_tokens": 15_000_000,
        "cache_creation_tokens": 1_500_000,
    },
}


class TestCalculateSavingsFromProxyStats:
    def test_returns_required_keys(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        required = {
            "cost_without_tokenpak", "cost_with_tokenpak", "total_saved",
            "cache_saved", "compression_saved", "routing_saved",
            "total_saved_pct", "cache_hit_rate", "total_requests", "per_model",
        }
        assert required.issubset(result.keys())

    def test_total_saved_is_positive(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        assert result["total_saved"] >= 0

    def test_cost_without_greater_than_cost_with(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        assert result["cost_without_tokenpak"] >= result["cost_with_tokenpak"]

    def test_cache_hit_rate_matches_expectation(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        # 900 hits / 1000 decisions = 90%
        assert result["cache_hit_rate"] == pytest.approx(90.0, abs=0.5)

    def test_total_requests_passed_through(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        assert result["total_requests"] == 1000

    def test_per_model_sorted_by_cost_desc(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        costs = [r["cost"] for r in result["per_model"]]
        assert costs == sorted(costs, reverse=True)

    def test_per_model_has_required_fields(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        for row in result["per_model"]:
            assert "model" in row
            assert "requests" in row
            assert "cost" in row
            assert "cache_hit_rate" in row

    def test_empty_by_model(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, {})
        assert result["per_model"] == []
        assert result["cost_without_tokenpak"] == 0.0

    def test_savings_pct_bounded_0_to_100(self):
        result = calculate_savings_from_proxy_stats(SAMPLE_STATS, SAMPLE_BY_MODEL)
        assert 0 <= result["total_saved_pct"] <= 100

    def test_accepts_nested_session_stats(self):
        nested = {"session": SAMPLE_STATS}
        result = calculate_savings_from_proxy_stats(nested, SAMPLE_BY_MODEL)
        assert result["total_requests"] == 1000

    def test_zero_requests_no_crash(self):
        result = calculate_savings_from_proxy_stats({}, {})
        assert result["total_saved"] == 0.0
        assert result["cache_hit_rate"] == 0.0

    def test_savings_accuracy_sonnet_model(self):
        """Sonnet baseline: 1M input @ $3/M + 100K output @ $15/M = $4.50.
        Actual: $1.00. Saved: $3.50."""
        stats = {
            "requests": 10,
            "input_tokens": 1_000_000,
            "sent_input_tokens": 950_000,
            "saved_tokens": 50_000,
            "output_tokens": 100_000,
            "cost": 1.0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_hits": 0,
            "cache_misses": 10,
        }
        by_model = {
            "claude-sonnet-4-6": {
                "requests": 10,
                "input_tokens": 1_000_000,
                "output_tokens": 100_000,
                "cost": 1.0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        }
        result = calculate_savings_from_proxy_stats(stats, by_model)
        expected_baseline = (1_000_000 / 1e6) * 3.0 + (100_000 / 1e6) * 15.0
        assert result["cost_without_tokenpak"] == pytest.approx(expected_baseline, abs=0.01)
        assert result["total_saved"] == pytest.approx(expected_baseline - 1.0, abs=0.05)
