"""Tests for pricing module."""

import pytest
from tokenpak.pricing import (
    get_price,
    get_rates,
    calculate_request_cost,
    calculate_request_cost_baseline,
    estimate_savings,
)


class TestGetPrice:
    """Test get_price function."""

    def test_get_price_valid_model_input(self):
        """Test getting input price for a known model."""
        price = get_price("claude-opus-4-6", direction="input")
        assert price == 15.0

    def test_get_price_valid_model_output(self):
        """Test getting output price for a known model."""
        price = get_price("claude-opus-4-6", direction="output")
        assert price == 75.0

    def test_get_price_unknown_model_uses_default(self):
        """Test getting price for unknown model returns default."""
        price = get_price("unknown-model-xyz")
        assert price == 3.0  # DEFAULT_RATE input

    def test_get_price_haiku(self):
        """Test Haiku model pricing."""
        input_price = get_price("claude-haiku-4-5", direction="input")
        output_price = get_price("claude-haiku-4-5", direction="output")
        assert input_price == 0.80
        assert output_price == 4.0


class TestGetRates:
    """Test get_rates function."""

    def test_get_rates_opus(self):
        """Test getting full rates for Opus."""
        rates = get_rates("claude-opus-4-6")
        assert rates["input"] == 15.0
        assert rates["cached"] == 1.50
        assert rates["output"] == 75.0

    def test_get_rates_sonnet(self):
        """Test getting full rates for Sonnet."""
        rates = get_rates("claude-sonnet-4-6")
        assert rates["input"] == 3.0
        assert rates["cached"] == 0.30
        assert rates["output"] == 15.0

    def test_get_rates_unknown_returns_default(self):
        """Test unknown model returns default rates."""
        rates = get_rates("unknown-model")
        assert rates == {"input": 3.0, "cached": 0.30, "output": 15.0}

    def test_get_rates_none_returns_default(self):
        """Test None returns default rates."""
        rates = get_rates(None)
        assert rates == {"input": 3.0, "cached": 0.30, "output": 15.0}


class TestCalculateRequestCost:
    """Test calculate_request_cost function."""

    def test_cost_input_only(self):
        """Test cost calculation for input-only request."""
        cost = calculate_request_cost("claude-opus-4-6", input_tokens=1000000)
        # 1M tokens * $15/M = $15
        assert abs(cost - 15.0) < 0.01

    def test_cost_with_cache_read(self):
        """Test cost with cache read tokens."""
        # 1M input @ 15 per M, 1M cache read @ 0.1 per M
        cost = calculate_request_cost("claude-opus-4-6", input_tokens=1000000, cache_read_tokens=1000000)
        # input: 1M * 15 = 15
        # cache: 1M * (15 * 0.1) = 1.5
        # total: 16.5
        expected = 15.0 + 1.5
        assert abs(cost - expected) < 0.01

    def test_cost_with_output(self):
        """Test cost with output tokens."""
        cost = calculate_request_cost("claude-opus-4-6", input_tokens=1000000, output_tokens=100000)
        # input: 1M * 15 = 15
        # output: 0.1M * 75 = 7.5
        # total: 22.5
        expected = 15.0 + 7.5
        assert abs(cost - expected) < 0.01

    def test_cost_haiku_cheap(self):
        """Test Haiku is cheaper than Opus."""
        opus_cost = calculate_request_cost("claude-opus-4-6", input_tokens=1000000)
        haiku_cost = calculate_request_cost("claude-haiku-4-5", input_tokens=1000000)
        assert haiku_cost < opus_cost
        assert haiku_cost < 1.0  # Haiku input is $0.80 per M

    def test_cost_zero_tokens(self):
        """Test cost with zero tokens is zero."""
        cost = calculate_request_cost("claude-opus-4-6", input_tokens=0)
        assert cost == 0.0


class TestCalculateRequestCostBaseline:
    """Test calculate_request_cost_baseline function."""

    def test_baseline_input_only(self):
        """Test baseline cost is full input rate."""
        cost = calculate_request_cost_baseline("claude-opus-4-6", total_input_tokens=1000000)
        assert abs(cost - 15.0) < 0.01

    def test_baseline_with_output(self):
        """Test baseline cost includes output."""
        cost = calculate_request_cost_baseline("claude-opus-4-6", total_input_tokens=1000000, output_tokens=100000)
        # input: 1M * 15 = 15
        # output: 0.1M * 75 = 7.5
        expected = 15.0 + 7.5
        assert abs(cost - expected) < 0.01

    def test_baseline_vs_cached_saves_money(self):
        """Test that caching saves money."""
        baseline = calculate_request_cost_baseline("claude-opus-4-6", total_input_tokens=1000000)
        # With 500K cached at 1.5/M instead of 15/M
        cached = calculate_request_cost("claude-opus-4-6", input_tokens=500000, cache_read_tokens=500000)
        assert cached < baseline

    def test_baseline_unknown_model(self):
        """Test baseline with unknown model uses default."""
        cost = calculate_request_cost_baseline("unknown-model", total_input_tokens=1000000)
        # Default is $3 per M
        assert abs(cost - 3.0) < 0.01


class TestEstimateSavings:
    """Test estimate_savings function."""

    def test_savings_compression_only(self):
        """Test savings from compression alone."""
        stats = {
            "tokens_raw": 1000000,
            "tokens_saved": 100000,  # 10% saved
            "model": "claude-opus-4-6",
        }
        savings = estimate_savings(stats)
        # Compression saves 100K * 15/M = 1.50
        assert savings["compression_cost_saved"] == 1.5
        assert savings["cache_cost_saved"] == 0.0
        assert savings["total_cost_saved"] == 1.5

    def test_savings_cache_only(self):
        """Test savings from cache alone."""
        stats = {
            "tokens_raw": 1000000,
            "cache_read_tokens": 500000,  # 50% from cache
            "model": "claude-opus-4-6",
        }
        savings = estimate_savings(stats)
        # Cache saves (input_rate - cache_rate) * cache_tokens
        # (15 - 1.5) * 0.5M = 13.5 * 0.5 = 6.75
        assert savings["cache_cost_saved"] == 6.75
        assert savings["compression_cost_saved"] == 0.0

    def test_savings_compression_and_cache(self):
        """Test savings from both compression and cache."""
        stats = {
            "tokens_raw": 1000000,
            "tokens_saved": 200000,  # 20% compression
            "cache_read_tokens": 400000,  # 40% of 800K post-compression from cache
            "model": "claude-opus-4-6",
        }
        savings = estimate_savings(stats)
        # Compression saves 200K * 15/M = 3.00
        # Cache saves (15 - 1.5) * 0.4M = 13.5 * 0.4 = 5.40
        assert savings["compression_cost_saved"] == 3.0
        assert abs(savings["cache_cost_saved"] - 5.4) < 0.01

    def test_savings_reduction_percent(self):
        """Test reduction percentage calculation."""
        stats = {
            "tokens_raw": 1000000,
            "tokens_saved": 300000,  # 30% compression
            "cache_read_tokens": 700000 * 0.5,  # 50% of post-compression from cache
            "model": "claude-opus-4-6",
        }
        savings = estimate_savings(stats)
        # Without TokenPak: 1M * 15 = 15
        # With TokenPak: 700K * 15 + 350K * 1.5 = 10.5 + 0.525 = 11.025
        # Savings: 15 - 11.025 = 3.975
        # Reduction: 3.975 / 15 = 26.5%
        assert savings["reduction_percent"] > 0
        assert savings["cost_without_tokenpak"] == 15.0

    def test_savings_unknown_model_uses_default(self):
        """Test savings with unknown model uses default rates."""
        stats = {
            "tokens_raw": 1000000,
            "tokens_saved": 100000,
            "model": "unknown-model",
        }
        savings = estimate_savings(stats)
        # Default is $3/M, so 100K savings = $0.30
        assert savings["compression_cost_saved"] == 0.3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
