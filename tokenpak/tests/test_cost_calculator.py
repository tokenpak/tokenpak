"""Unit tests for cost calculation functions in tokenpak.telemetry.pricing.

The task references cost_calculator.py, which does not exist as a standalone module.
Cost calculation is implemented in tokenpak/pricing.py — this file tests those functions.
"""

import pytest

from tokenpak.telemetry.pricing import (
    MODEL_RATES,
    DEFAULT_RATE,
    calculate_request_cost,
    calculate_request_cost_baseline,
    get_rates,
    get_price,
    estimate_savings,
)


class TestGetRates:
    """Tests for get_rates — model pricing lookup."""

    def test_known_model_returns_correct_rates(self):
        """Known models should return their specific rates."""
        rates = get_rates("claude-haiku-4-5")
        assert rates["input"] == 0.80
        assert rates["cached"] == 0.08
        assert rates["output"] == 4.0

    def test_unknown_model_returns_default(self):
        """Unknown models should fall back to DEFAULT_RATE."""
        rates = get_rates("unknown-model-xyz")
        assert rates == DEFAULT_RATE

    def test_none_model_returns_default(self):
        """None model should return DEFAULT_RATE."""
        rates = get_rates(None)
        assert rates == DEFAULT_RATE

    def test_no_args_returns_default(self):
        """Calling with no args should return DEFAULT_RATE."""
        rates = get_rates()
        assert rates == DEFAULT_RATE

    def test_rates_have_required_keys(self):
        """All rate dicts should have input, cached, and output keys."""
        for model in MODEL_RATES:
            rates = get_rates(model)
            assert "input" in rates
            assert "cached" in rates
            assert "output" in rates

    def test_openai_model_rates(self):
        """OpenAI models should return correct rates."""
        rates = get_rates("gpt-4o-mini")
        assert rates["input"] == 0.15
        assert rates["output"] == 0.60

    def test_cached_rate_cheaper_than_input(self):
        """Cached rate should always be cheaper than input rate."""
        for model in MODEL_RATES:
            rates = get_rates(model)
            assert rates["cached"] < rates["input"], (
                f"{model}: cached ({rates['cached']}) should be < input ({rates['input']})"
            )


class TestGetPrice:
    """Tests for get_price — per-direction pricing."""

    def test_input_price(self):
        """Input direction should return correct rate."""
        price = get_price("claude-haiku-4-5", "input")
        assert price == 0.80

    def test_output_price(self):
        """Output direction should return correct rate."""
        price = get_price("claude-haiku-4-5", "output")
        assert price == 4.0

    def test_cached_price(self):
        """Cached direction should return correct rate."""
        price = get_price("claude-haiku-4-5", "cached")
        assert price == 0.08

    def test_default_direction_is_input(self):
        """Default direction (no arg) should return input rate."""
        price_default = get_price("claude-haiku-4-5")
        price_input = get_price("claude-haiku-4-5", "input")
        assert price_default == price_input

    def test_unknown_model_returns_default_price(self):
        """Unknown model should return default input rate."""
        price = get_price("nonexistent-model", "input")
        assert price == DEFAULT_RATE["input"]


class TestCalculateRequestCost:
    """Tests for calculate_request_cost — actual cost with TokenPak."""

    def test_zero_tokens_is_zero_cost(self):
        """Zero tokens should produce zero cost."""
        cost = calculate_request_cost("claude-haiku-4-5", 0, 0, 0, 0)
        assert cost == 0.0

    def test_input_tokens_only(self):
        """Input tokens only should calculate correctly."""
        # haiku input = $0.80/M tokens
        # 1_000_000 input tokens = $0.80
        cost = calculate_request_cost("claude-haiku-4-5", input_tokens=1_000_000)
        assert cost == pytest.approx(0.80, rel=1e-4)

    def test_output_tokens_only(self):
        """Output tokens only should calculate correctly."""
        # haiku output = $4.00/M tokens
        # 1_000_000 output tokens = $4.00
        cost = calculate_request_cost("claude-haiku-4-5", input_tokens=0, output_tokens=1_000_000)
        assert cost == pytest.approx(4.00, rel=1e-4)

    def test_cache_read_is_cheaper_than_input(self):
        """Cache read tokens should cost less than equivalent input tokens."""
        cost_input = calculate_request_cost("claude-sonnet-4-6", input_tokens=100_000)
        cost_cache = calculate_request_cost("claude-sonnet-4-6", input_tokens=0, cache_read_tokens=100_000)
        assert cost_cache < cost_input

    def test_combined_token_types(self):
        """Combined input + output tokens should sum correctly."""
        # sonnet: input=$3.0/M, output=$15.0/M
        # 100k input + 10k output
        # = (100_000 / 1_000_000) * 3.0 + (10_000 / 1_000_000) * 15.0
        # = 0.30 + 0.15 = 0.45
        cost = calculate_request_cost(
            "claude-sonnet-4-6",
            input_tokens=100_000,
            output_tokens=10_000,
        )
        assert cost == pytest.approx(0.45, rel=1e-4)

    def test_cache_creation_premium(self):
        """Cache creation tokens should cost 1.25x input rate."""
        # sonnet input = $3.0/M → cache creation = $3.75/M
        # 1_000_000 cache_creation_tokens = $3.75
        cost = calculate_request_cost(
            "claude-sonnet-4-6",
            input_tokens=0,
            cache_creation_tokens=1_000_000,
        )
        assert cost == pytest.approx(3.75, rel=1e-4)

    def test_returns_float(self):
        """Return type should be float."""
        cost = calculate_request_cost("claude-haiku-4-5", 1000)
        assert isinstance(cost, float)

    def test_unknown_model_uses_defaults(self):
        """Unknown model should use DEFAULT_RATE without crashing."""
        cost = calculate_request_cost("unknown-model", input_tokens=1_000_000)
        assert cost == pytest.approx(DEFAULT_RATE["input"], rel=1e-4)


class TestCalculateRequestCostBaseline:
    """Tests for calculate_request_cost_baseline — cost without TokenPak."""

    def test_zero_tokens_is_zero(self):
        """Zero tokens baseline should be zero."""
        cost = calculate_request_cost_baseline("claude-haiku-4-5", 0, 0)
        assert cost == 0.0

    def test_baseline_input_tokens(self):
        """Baseline for 1M input tokens matches model's input rate."""
        cost = calculate_request_cost_baseline("claude-haiku-4-5", 1_000_000, 0)
        assert cost == pytest.approx(0.80, rel=1e-4)

    def test_baseline_output_tokens(self):
        """Baseline includes output token cost."""
        # haiku output = $4.00/M
        cost = calculate_request_cost_baseline("claude-haiku-4-5", 0, 1_000_000)
        assert cost == pytest.approx(4.00, rel=1e-4)

    def test_baseline_higher_than_cached_cost(self):
        """Baseline (no cache) should cost more than cached request."""
        tokens = 500_000
        baseline = calculate_request_cost_baseline("claude-sonnet-4-6", tokens)
        cached_cost = calculate_request_cost("claude-sonnet-4-6", 0, cache_read_tokens=tokens)
        assert baseline > cached_cost

    def test_unknown_model_uses_defaults(self):
        """Unknown model baseline should use DEFAULT_RATE."""
        cost = calculate_request_cost_baseline("unknown-model", 1_000_000)
        assert cost == pytest.approx(DEFAULT_RATE["input"], rel=1e-4)


class TestEstimateSavings:
    """Tests for estimate_savings — proxy stats analysis."""

    def test_no_savings_when_no_compression_or_cache(self):
        """Zero savings with empty stats."""
        result = estimate_savings({}, model="claude-haiku-4-5")
        assert result["total_cost_saved"] == 0.0
        assert result["compression_tokens_saved"] == 0
        assert result["cache_tokens_saved"] == 0

    def test_compression_savings_calculated(self):
        """Compression savings should reflect tokens_saved."""
        stats = {"tokens_raw": 100_000, "tokens_saved": 30_000}
        result = estimate_savings(stats, model="claude-haiku-4-5")
        # haiku input = $0.80/M, 30k tokens = $0.024
        assert result["compression_tokens_saved"] == 30_000
        assert result["compression_cost_saved"] == pytest.approx(0.024, rel=1e-3)

    def test_cache_savings_calculated(self):
        """Cache savings should reflect cache_read_tokens."""
        stats = {"tokens_raw": 100_000, "cache_read_tokens": 50_000}
        result = estimate_savings(stats, model="claude-haiku-4-5")
        assert result["cache_tokens_saved"] == 50_000
        assert result["cache_cost_saved"] > 0

    def test_reduction_percent_in_range(self):
        """Reduction percent should be 0-100."""
        stats = {"tokens_raw": 100_000, "tokens_saved": 30_000}
        result = estimate_savings(stats, model="claude-haiku-4-5")
        assert 0 <= result["reduction_percent"] <= 100

    def test_returns_all_required_keys(self):
        """Result should contain all expected keys."""
        result = estimate_savings({})
        required = [
            "compression_tokens_saved",
            "compression_cost_saved",
            "cache_hit_rate",
            "cache_tokens_saved",
            "cache_cost_saved",
            "total_tokens_saved",
            "total_cost_saved",
            "cost_without_tokenpak",
            "cost_with_tokenpak",
            "reduction_percent",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_model_from_stats(self):
        """Model can be specified inside stats dict."""
        stats = {"tokens_raw": 1_000_000, "tokens_saved": 0, "model": "claude-haiku-4-5"}
        result = estimate_savings(stats)
        # No savings, cost should reflect haiku rate
        assert result["cost_without_tokenpak"] == pytest.approx(0.80, rel=1e-3)
