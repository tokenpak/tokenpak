"""Proxy rate estimates must honor explicit catalog cache prices."""

from dataclasses import replace

import pytest

from tokenpak import models
from tokenpak.proxy.router import estimate_cost


@pytest.mark.parametrize(
    "model,reads,writes,expected",
    [
        ("claude-fable-5-1", 1_000_000, 0, 0.25),
        ("gpt-5.6", 0, 1_000_000, 5.0),  # Registered alias for the catalog entry.
        ("claude-mythos-5-1", 1_000_000, 0, 0.25),
        ("claude-3-haiku-20240307", 1_000_000, 0, 0.03),
        ("claude-3-haiku-20240307", 0, 1_000_000, 0.30),
        # Separate uncached, cached-read, and cached-write partitions.
        ("claude-3-haiku-20240307", 500_000, 250_000, 0.1525),
    ],
)
def test_explicit_catalog_cache_prices(model, reads, writes, expected):
    assert estimate_cost(model, 1_000_000, 0, reads, writes) == pytest.approx(expected)


def test_explicit_zero_cache_prices_are_not_replaced_by_multipliers(monkeypatch):
    pricing = models.get_pricing("claude-sonnet-4-5")
    assert pricing is not None
    free_cache = replace(pricing, cache_read_per_mtok=0.0, cache_write_per_mtok=0.0)
    monkeypatch.setattr(models, "get_pricing", lambda _model: free_cache)

    assert estimate_cost("claude-sonnet-4-5", 1_000_000, 0, 500_000, 500_000) == 0.0


def test_explicit_cache_rates_preserve_uncached_input_and_output_costs():
    # Published catalog: input 0.25, output 1.25, read 0.03, write 0.30 per million.
    assert estimate_cost("claude-3-haiku-20240307", 1_000_000, 1_000_000) == 1.50


@pytest.mark.parametrize("model,expected", [("", 0.30), ("gpt-4o", 0.25)])
def test_absent_explicit_cache_rate_preserves_legacy_estimate(model, expected):
    assert estimate_cost(model, 1_000_000, 0, 1_000_000, 0) == pytest.approx(expected)
