"""test_pricing.py — Unit tests for tokenpak/pricing.py public API.

Covers: get_rates, estimate_savings, calculate_request_cost,
        calculate_request_cost_baseline, get_price, MODEL_RATES, DEFAULT_RATE.

NOTE: Comprehensive coverage already lives in test_pricing_module.py.
This file provides the canonical test_pricing.py entry point as required
by the task spec, with focused tests on key public API contracts.
"""
import pytest
from tokenpak.telemetry.pricing import (
    MODEL_RATES,
    DEFAULT_RATE,
    get_rates,
    estimate_savings,
    calculate_request_cost,
    calculate_request_cost_baseline,
    get_price,
)


# ---------------------------------------------------------------------------
# get_rates — fallback & known-model lookups
# ---------------------------------------------------------------------------

def test_get_rates_known_model():
    rates = get_rates("claude-sonnet-4-5")
    assert rates["input"] == 3.0
    assert rates["cached"] == 0.30
    assert rates["output"] == 15.0


def test_get_rates_unknown_falls_back_to_default():
    assert get_rates("nonexistent-model-xyz") == DEFAULT_RATE


def test_get_rates_none_falls_back_to_default():
    assert get_rates(None) == DEFAULT_RATE


def test_get_rates_empty_string_falls_back():
    assert get_rates("") == DEFAULT_RATE


def test_get_rates_all_known_models_have_three_keys():
    for model, rates in MODEL_RATES.items():
        assert {"input", "cached", "output"} <= rates.keys(), f"{model} missing keys"


def test_get_rates_cached_cheaper_than_input():
    for model, rates in MODEL_RATES.items():
        assert rates["cached"] < rates["input"], f"{model}: cached >= input"


# ---------------------------------------------------------------------------
# estimate_savings — core savings math
# ---------------------------------------------------------------------------

def test_estimate_savings_returns_all_required_keys():
    result = estimate_savings({"tokens_raw": 0})
    required = {
        "compression_tokens_saved", "compression_cost_saved",
        "cache_hit_rate", "cache_tokens_saved", "cache_cost_saved",
        "total_tokens_saved", "total_cost_saved",
        "cost_without_tokenpak", "cost_with_tokenpak", "reduction_percent",
    }
    assert required <= result.keys()


def test_estimate_savings_empty_stats_no_error():
    result = estimate_savings({})
    assert result["total_cost_saved"] == 0.0
    assert result["cost_without_tokenpak"] == 0.0


def test_estimate_savings_compression_math():
    # 1M raw, 500k compressed away, sonnet input=$3/M → $1.50 saved
    result = estimate_savings(
        {"tokens_raw": 1_000_000, "tokens_saved": 500_000},
        model="claude-sonnet-4-5",
    )
    assert result["compression_tokens_saved"] == 500_000
    assert abs(result["compression_cost_saved"] - 1.50) < 0.001


def test_estimate_savings_cache_math():
    # 1M cache_read at sonnet: savings = (3.0 - 0.30)/M * 1M = $2.70
    result = estimate_savings(
        {"tokens_raw": 2_000_000, "cache_read_tokens": 1_000_000},
        model="claude-sonnet-4-5",
    )
    assert abs(result["cache_cost_saved"] - 2.70) < 0.001


def test_estimate_savings_total_tokens_is_sum():
    result = estimate_savings(
        {"tokens_raw": 1_000_000, "tokens_saved": 100_000, "cache_read_tokens": 200_000},
        model="claude-sonnet-4-5",
    )
    assert result["total_tokens_saved"] == 300_000


def test_estimate_savings_reduction_percent_positive_when_savings_exist():
    result = estimate_savings(
        {"tokens_raw": 1_000_000, "tokens_saved": 500_000},
        model="claude-sonnet-4-5",
    )
    assert result["reduction_percent"] > 0


def test_estimate_savings_model_override():
    # Passing model= should override stats["model"]
    result = estimate_savings(
        {"tokens_raw": 1_000_000, "tokens_saved": 100_000, "model": "claude-haiku-4-5"},
        model="claude-opus-4-5",
    )
    haiku_savings = estimate_savings(
        {"tokens_raw": 1_000_000, "tokens_saved": 100_000},
        model="claude-haiku-4-5",
    )
    # opus is more expensive → higher savings
    assert result["compression_cost_saved"] > haiku_savings["compression_cost_saved"]


# ---------------------------------------------------------------------------
# calculate_request_cost — per-request billing
# ---------------------------------------------------------------------------

def test_calculate_request_cost_input_only():
    # 1M input at sonnet $3/M = $3.00
    cost = calculate_request_cost("claude-sonnet-4-5", input_tokens=1_000_000)
    assert abs(cost - 3.0) < 0.001


def test_calculate_request_cost_output_only():
    cost = calculate_request_cost("claude-sonnet-4-5", input_tokens=0, output_tokens=1_000_000)
    assert abs(cost - 15.0) < 0.001


def test_calculate_request_cost_cache_read_cheaper_than_input():
    fresh = calculate_request_cost("claude-sonnet-4-5", input_tokens=1_000_000)
    cached = calculate_request_cost("claude-sonnet-4-5", input_tokens=0, cache_read_tokens=1_000_000)
    assert cached < fresh


def test_calculate_request_cost_zero_tokens():
    assert calculate_request_cost("claude-sonnet-4-5", input_tokens=0) == 0.0


def test_calculate_request_cost_unknown_model_positive():
    cost = calculate_request_cost("unknown-model", input_tokens=1_000_000)
    assert cost > 0


def test_calculate_request_cost_returns_float():
    assert isinstance(calculate_request_cost("gpt-4o", input_tokens=1000), float)


# ---------------------------------------------------------------------------
# calculate_request_cost_baseline — no-cache baseline
# ---------------------------------------------------------------------------

def test_calculate_request_cost_baseline_basic():
    cost = calculate_request_cost_baseline("claude-sonnet-4-5", total_input_tokens=1_000_000)
    assert abs(cost - 3.0) < 0.001


def test_calculate_request_cost_baseline_with_output():
    cost = calculate_request_cost_baseline(
        "claude-sonnet-4-5", total_input_tokens=1_000_000, output_tokens=1_000_000
    )
    assert abs(cost - 18.0) < 0.001  # 3.0 + 15.0


def test_calculate_request_cost_baseline_zero():
    assert calculate_request_cost_baseline("claude-haiku-4-5", total_input_tokens=0) == 0.0


def test_calculate_request_cost_baseline_higher_than_cached():
    baseline = calculate_request_cost_baseline("claude-sonnet-4-5", total_input_tokens=1_000_000)
    with_cache = calculate_request_cost("claude-sonnet-4-5", input_tokens=0, cache_read_tokens=1_000_000)
    assert baseline > with_cache


# ---------------------------------------------------------------------------
# get_price — per-direction price lookup
# ---------------------------------------------------------------------------

def test_get_price_input():
    assert get_price("claude-sonnet-4-5", "input") == 3.0


def test_get_price_output():
    assert get_price("claude-sonnet-4-5", "output") == 15.0


def test_get_price_cached():
    assert get_price("claude-sonnet-4-5", "cached") == 0.30


def test_get_price_default_direction_is_input():
    assert get_price("claude-sonnet-4-5") == get_price("claude-sonnet-4-5", "input")


def test_get_price_unknown_model_uses_defaults():
    assert get_price("totally-fake-model", "input") == DEFAULT_RATE["input"]
    assert get_price("totally-fake-model", "output") == DEFAULT_RATE["output"]
    assert get_price("totally-fake-model", "cached") == DEFAULT_RATE["cached"]
