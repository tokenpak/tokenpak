"""Unit tests for tokenpak/pricing.py"""

import pytest

from tokenpak.telemetry.pricing import (
    DEFAULT_RATE,
    MODEL_RATES,
    calculate_request_cost,
    calculate_request_cost_baseline,
    estimate_savings,
    get_price,
    get_rates,
)

# ---------------------------------------------------------------------------
# MODEL_RATES / DEFAULT_RATE constants
# ---------------------------------------------------------------------------


class TestModelRates:
    def test_known_models_present(self):
        for model in ("claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5", "gpt-4o"):
            assert model in MODEL_RATES

    def test_each_rate_has_three_keys(self):
        for model, rates in MODEL_RATES.items():
            assert "input" in rates, f"{model} missing 'input'"
            assert "cached" in rates, f"{model} missing 'cached'"
            assert "output" in rates, f"{model} missing 'output'"

    def test_cached_cheaper_than_input(self):
        for model, rates in MODEL_RATES.items():
            assert rates["cached"] < rates["input"], f"{model}: cached should be < input"

    def test_default_rate_has_all_keys(self):
        assert "input" in DEFAULT_RATE
        assert "cached" in DEFAULT_RATE
        assert "output" in DEFAULT_RATE

    def test_opus_more_expensive_than_haiku(self):
        assert MODEL_RATES["claude-opus-4-5"]["input"] > MODEL_RATES["claude-haiku-4-5"]["input"]


# ---------------------------------------------------------------------------
# get_rates
# ---------------------------------------------------------------------------


class TestGetRates:
    def test_known_model_returns_correct_rates(self):
        rates = get_rates("claude-sonnet-4-5")
        assert rates["input"] == 3.0
        assert rates["output"] == 15.0

    def test_unknown_model_returns_default(self):
        rates = get_rates("unknown-model-xyz")
        assert rates == DEFAULT_RATE

    def test_none_model_returns_default(self):
        rates = get_rates(None)
        assert rates == DEFAULT_RATE

    def test_empty_string_returns_default(self):
        rates = get_rates("")
        assert rates == DEFAULT_RATE

    def test_returns_dict(self):
        assert isinstance(get_rates("gpt-4o"), dict)

    def test_opus_4_6_same_as_4_5(self):
        assert get_rates("claude-opus-4-6") == get_rates("claude-opus-4-5")

    def test_sonnet_4_6_same_as_4_5(self):
        assert get_rates("claude-sonnet-4-6") == get_rates("claude-sonnet-4-5")


# ---------------------------------------------------------------------------
# estimate_savings
# ---------------------------------------------------------------------------


class TestEstimateSavings:
    def test_returns_required_keys(self):
        result = estimate_savings({"tokens_raw": 1000})
        for key in (
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
        ):
            assert key in result, f"Missing key: {key}"

    def test_zero_stats_returns_zeros(self):
        result = estimate_savings({})
        assert result["compression_tokens_saved"] == 0
        assert result["total_cost_saved"] == 0.0
        assert result["cost_without_tokenpak"] == 0.0

    def test_compression_savings_calculated(self):
        # 1M tokens raw, 500k saved → 500k saved at sonnet input rate $3/M = $1.50
        result = estimate_savings(
            {"tokens_raw": 1_000_000, "tokens_saved": 500_000},
            model="claude-sonnet-4-5",
        )
        assert result["compression_tokens_saved"] == 500_000
        assert abs(result["compression_cost_saved"] - 1.50) < 0.01

    def test_cache_savings_calculated(self):
        # Cache read tokens: 1M at sonnet (input=3.0, cached=0.30) → diff=2.70/M → $2.70
        result = estimate_savings(
            {"tokens_raw": 2_000_000, "cache_read_tokens": 1_000_000},
            model="claude-sonnet-4-5",
        )
        assert result["cache_tokens_saved"] == 1_000_000
        assert abs(result["cache_cost_saved"] - 2.70) < 0.01

    def test_total_savings_is_sum(self):
        result = estimate_savings(
            {"tokens_raw": 1_000_000, "tokens_saved": 100_000, "cache_read_tokens": 200_000},
            model="claude-sonnet-4-5",
        )
        assert result["total_tokens_saved"] == 300_000

    def test_reduction_percent_positive(self):
        result = estimate_savings(
            {"tokens_raw": 1_000_000, "tokens_saved": 500_000},
            model="claude-sonnet-4-5",
        )
        assert result["reduction_percent"] > 0

    def test_model_from_stats_dict(self):
        result = estimate_savings(
            {"tokens_raw": 1_000_000, "tokens_saved": 100_000, "model": "claude-haiku-4-5"}
        )
        haiku_input = MODEL_RATES["claude-haiku-4-5"]["input"]
        expected = (100_000 / 1_000_000) * haiku_input
        assert abs(result["compression_cost_saved"] - expected) < 0.0001

    def test_model_arg_overrides_stats_model(self):
        result_opus = estimate_savings(
            {"tokens_raw": 1_000_000, "tokens_saved": 100_000, "model": "claude-haiku-4-5"},
            model="claude-opus-4-5",
        )
        result_haiku = estimate_savings(
            {"tokens_raw": 1_000_000, "tokens_saved": 100_000},
            model="claude-haiku-4-5",
        )
        assert result_opus["compression_cost_saved"] > result_haiku["compression_cost_saved"]

    def test_cache_hit_rate_calculation(self):
        # 1M raw, 500k after compression, 250k from cache → 50% hit rate
        result = estimate_savings(
            {"tokens_raw": 1_000_000, "tokens_saved": 500_000, "cache_read_tokens": 250_000},
            model="claude-sonnet-4-5",
        )
        assert result["cache_hit_rate"] == 50.0

    def test_no_negative_cost(self):
        result = estimate_savings({"tokens_raw": 0, "tokens_saved": 0})
        assert result["cost_without_tokenpak"] >= 0
        assert result["cost_with_tokenpak"] >= 0

    def test_input_tokens_alias(self):
        # stats["input_tokens"] should work as alias for tokens_raw
        result = estimate_savings({"input_tokens": 1_000_000}, model="claude-sonnet-4-5")
        assert result["cost_without_tokenpak"] == pytest.approx(3.0, abs=0.001)


# ---------------------------------------------------------------------------
# calculate_request_cost
# ---------------------------------------------------------------------------


class TestCalculateRequestCost:
    def test_pure_input_tokens(self):
        # 1M input tokens at sonnet $3/M = $3.00
        cost = calculate_request_cost("claude-sonnet-4-5", input_tokens=1_000_000)
        assert abs(cost - 3.0) < 0.001

    def test_output_tokens(self):
        # 1M output tokens at sonnet $15/M = $15.00
        cost = calculate_request_cost("claude-sonnet-4-5", input_tokens=0, output_tokens=1_000_000)
        assert abs(cost - 15.0) < 0.001

    def test_cache_read_tokens_cheaper(self):
        cost_fresh = calculate_request_cost("claude-sonnet-4-5", input_tokens=1_000_000)
        cost_cached = calculate_request_cost(
            "claude-sonnet-4-5", input_tokens=0, cache_read_tokens=1_000_000
        )
        assert cost_cached < cost_fresh

    def test_cache_creation_tokens_cost(self):
        # cache_creation_tokens cost input_rate * 1.25
        cost = calculate_request_cost(
            "claude-sonnet-4-5", input_tokens=0, cache_creation_tokens=1_000_000
        )
        expected = 3.0 * 1.25
        assert abs(cost - expected) < 0.01

    def test_unknown_model_uses_defaults(self):
        cost = calculate_request_cost("unknown-model", input_tokens=1_000_000)
        assert isinstance(cost, float)
        assert cost > 0

    def test_zero_tokens_zero_cost(self):
        cost = calculate_request_cost("claude-sonnet-4-5", input_tokens=0)
        assert cost == 0.0

    def test_returns_float(self):
        assert isinstance(calculate_request_cost("gpt-4o", input_tokens=100), float)

    def test_combined_tokens(self):
        cost = calculate_request_cost(
            "claude-sonnet-4-5",
            input_tokens=100_000,
            cache_read_tokens=50_000,
            output_tokens=10_000,
        )
        rates = MODEL_RATES["claude-sonnet-4-5"]
        expected = (
            (100_000 / 1_000_000) * rates["input"]
            + (50_000 / 1_000_000) * rates["cached"]
            + (10_000 / 1_000_000) * rates["output"]
        )
        assert abs(cost - expected) < 0.0001


# ---------------------------------------------------------------------------
# calculate_request_cost_baseline
# ---------------------------------------------------------------------------


class TestCalculateRequestCostBaseline:
    def test_basic_cost(self):
        cost = calculate_request_cost_baseline("claude-sonnet-4-5", total_input_tokens=1_000_000)
        assert abs(cost - 3.0) < 0.001

    def test_with_output_tokens(self):
        cost = calculate_request_cost_baseline(
            "claude-sonnet-4-5", total_input_tokens=1_000_000, output_tokens=1_000_000
        )
        assert abs(cost - 18.0) < 0.001  # 3.0 + 15.0

    def test_zero_input_zero_cost(self):
        assert calculate_request_cost_baseline("claude-haiku-4-5", total_input_tokens=0) == 0.0

    def test_returns_float(self):
        assert isinstance(calculate_request_cost_baseline("gpt-4o", total_input_tokens=1000), float)

    def test_baseline_higher_than_cached_cost(self):
        baseline = calculate_request_cost_baseline(
            "claude-sonnet-4-5", total_input_tokens=1_000_000
        )
        with_cache = calculate_request_cost(
            "claude-sonnet-4-5", input_tokens=0, cache_read_tokens=1_000_000
        )
        assert baseline > with_cache


# ---------------------------------------------------------------------------
# get_price
# ---------------------------------------------------------------------------


class TestGetPrice:
    def test_input_price(self):
        assert get_price("claude-sonnet-4-5", "input") == 3.0

    def test_output_price(self):
        assert get_price("claude-sonnet-4-5", "output") == 15.0

    def test_cached_price(self):
        assert get_price("claude-sonnet-4-5", "cached") == 0.30

    def test_default_direction_is_input(self):
        assert get_price("claude-sonnet-4-5") == get_price("claude-sonnet-4-5", "input")

    def test_unknown_model_returns_default_input(self):
        price = get_price("unknown-xyz", "input")
        assert price == DEFAULT_RATE["input"]

    def test_unknown_model_returns_default_output(self):
        price = get_price("unknown-xyz", "output")
        assert price == DEFAULT_RATE["output"]

    def test_haiku_input_price(self):
        assert get_price("claude-haiku-4-5", "input") == 0.80

    def test_opus_input_price(self):
        assert get_price("claude-opus-4-5", "input") == 15.0
