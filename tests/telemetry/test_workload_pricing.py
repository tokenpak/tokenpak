"""Workload pricing remains coherent across persistence, updates and thresholds."""

import json
import sqlite3
from dataclasses import replace

import pytest

from tokenpak.models import PricingContext, RateBand
from tokenpak.telemetry.cost import (
    UNIT_BASIS_USD_PER_1K,
    CostEngine,
    Pricing,
    StalePricingRefreshPlanError,
    calculate_actual,
)


@pytest.fixture
def bands():
    short = RateBand(
        "short",
        4,
        20,
        "fixture",
        "https://example.com/pricing",
        cache_read_per_mtok=0.4,
        cache_write_per_mtok=5,
        max_input_tokens=272001,
        service_tiers=("standard",),
    )
    return (
        short,
        replace(
            short,
            band_id="long",
            input_per_mtok=8,
            output_per_mtok=30,
            cache_read_per_mtok=0.8,
            cache_write_per_mtok=10,
            min_input_tokens=272001,
            max_input_tokens=None,
        ),
    )


@pytest.fixture
def engine(tmp_path, bands):
    engine = CostEngine(str(tmp_path / "pricing.db"), strict_unknown_units=True)
    engine.add_pricing(
        "example",
        "context-model",
        0.012,
        0.040,
        version="fixture",
        effective_date="2026-01-01",
        rate_bands=bands,
    )
    return engine


def test_roundtrip_bands_and_scalar_compatibility(engine, bands):
    restarted = CostEngine(engine.db_path, strict_unknown_units=True)
    scalar = restarted.get_pricing("context-model")
    assert scalar.rate_bands == bands
    assert scalar.input_rate == 0.012
    assert scalar.unit_basis == UNIT_BASIS_USD_PER_1K
    assert scalar.selected_band is None
    for tokens, expected, rate in [(272000, "short", 0.004), (272001, "long", 0.008)]:
        contextual = restarted.get_pricing("context-model", context=PricingContext(tokens))
        assert contextual.selected_band == expected
        assert contextual.input_rate == rate
        assert contextual.source_url == "https://example.com/pricing"


def test_compression_crosses_threshold_and_prices_output_at_each_band(engine):
    result = engine.calculate(
        "context-model", 300000, 250000, 10000, cache_read_tokens=100000, cache_write_tokens=20000
    )
    assert result.baseline_cost == pytest.approx(300000 * 8e-6 + 10000 * 30e-6)
    assert result.actual_cost == pytest.approx(
        130000 * 4e-6 + 100000 * 0.4e-6 + 20000 * 5e-6 + 10000 * 20e-6
    )
    assert result.baseline_rate_band == "long"
    assert result.actual_rate_band == "short"
    assert result.to_dict()["actual_rate_band"] == "short"


def test_context_count_cannot_select_a_cheaper_band(engine):
    with pytest.raises(ValueError, match="differs"):
        engine.calculate(
            "context-model", 300000, 300000, 0, context=PricingContext(input_tokens=200000)
        )


def test_resolved_discount_cannot_survive_an_unmatched_workload(engine):
    price = engine.get_pricing("context-model", context=PricingContext(1000))
    with pytest.raises(ValueError, match="does not match"):
        price.for_context(PricingContext(1000, service_tier="batch"))


def test_equal_overlap_refuses_cost_instead_of_using_scalar(engine, bands):
    engine.add_pricing(
        "example",
        "ambiguous",
        0.001,
        0.002,
        rate_bands=(bands[0], replace(bands[0], band_id="overlap")),
    )
    with pytest.raises(ValueError, match="ambiguous"):
        engine.calculate("ambiguous", 1000, 1000, 100)


def test_actual_cache_components_are_charged_in_per_thousand_units(engine):
    engine.add_pricing("example", "scalar-cache", 3, 15, cache_read_rate=0.3, cache_write_rate=3.75)
    result = engine.calculate(
        "scalar-cache", 1000, 1000, 100, cache_read_tokens=400, cache_write_tokens=200
    )
    assert result.actual_cost == pytest.approx(1.2 + 0.12 + 0.75 + 1.5)
    assert result.baseline_cost == pytest.approx(4.5)


def test_unknown_cache_discount_is_not_free(engine):
    engine.add_pricing("example", "no-cache-rate", 3, 15)
    result = engine.calculate("no-cache-rate", 1000, 1000, 100, cache_read_tokens=400)
    assert result.actual_cost == pytest.approx(result.baseline_cost)
    assert result.savings_amount == 0


def test_fresh_seed_uses_current_bands_and_cache_rates(engine):
    price = engine.get_pricing(
        "gpt-5.6-sol", context=PricingContext(300000, cache_ttl_seconds=1800)
    )
    assert price.input_rate == 0.008
    assert price.output_rate == 0.030
    assert price.cache_read_rate == 0.0008
    result = engine.calculate("claude-sonnet-4-6", 1000, 1000, 100, cache_read_tokens=400)
    assert result.actual_cost == pytest.approx(600 * 3e-6 + 400 * 0.3e-6 + 100 * 15e-6)


def test_explicit_refresh_inserts_bands_without_rewriting_legacy_row(tmp_path):
    db = tmp_path / "existing.db"
    engine = CostEngine(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tp_pricing SET rate_bands_json=NULL, cache_read_rate=NULL, cache_write_rate=NULL"
        )
        old = conn.execute("SELECT * FROM tp_pricing ORDER BY id").fetchall()
    restarted = CostEngine(str(db))
    assert restarted.get_pricing("gpt-5.6-sol").rate_bands == ()
    key = "openai/gpt-5.6-sol"
    plan = restarted.plan_seed_refresh(
        selected_models=[key], overrides={key: True}, version="with-bands"
    )
    assert restarted.apply_seed_refresh(plan)["inserted"] == 1
    with sqlite3.connect(db) as conn:
        current = conn.execute("SELECT * FROM tp_pricing ORDER BY id").fetchall()
    assert current[:-1] == old
    assert restarted.get_pricing("gpt-5.6-sol").rate_bands


@pytest.mark.parametrize(
    "read,write,expected", [(2000, 2000, 0.3), (-20, -50, 3.0), (0, 2000, 3.75)]
)
def test_cache_subsets_never_double_count_or_exceed_final_input(read, write, expected):
    price = Pricing(
        "example",
        "model",
        3,
        15,
        "fixture",
        "2026-01-01",
        cache_read_rate=0.3,
        cache_write_rate=3.75,
    )
    assert calculate_actual(1000, 0, price, read, cache_write_tokens=write) == pytest.approx(
        expected
    )


def test_other_process_band_update_is_visible_without_restart(engine, bands):
    assert engine.get_pricing("context-model", context=PricingContext(1000)).input_rate == 0.004
    changed = [replace(bands[0], input_per_mtok=7), bands[1]]
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute(
            "UPDATE tp_pricing SET rate_bands_json=? WHERE model=?",
            (json.dumps([b.to_mapping() for b in changed]), "context-model"),
        )
    assert engine.get_pricing("context-model", context=PricingContext(1000)).input_rate == 0.007


@pytest.mark.parametrize("payload", ["{}", "null", '[{"id":"broken"}]'])
def test_corrupt_persisted_bands_never_fall_back_to_cheap_scalar(engine, payload):
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute(
            "UPDATE tp_pricing SET rate_bands_json=? WHERE model=?", (payload, "context-model")
        )
    with pytest.raises(ValueError):
        engine.calculate("context-model", 1000, 1000, 10)


@pytest.mark.parametrize("applied", [False, True])
def test_band_change_invalidates_refresh_plan_and_applied_receipt(engine, bands, applied):
    key = "anthropic/claude-sonnet-4-6"
    plan = engine.plan_seed_refresh(
        selected_models=[key], overrides={key: True}, version="test-refresh"
    )
    if applied:
        engine.apply_seed_refresh(plan)
        assert engine.apply_seed_refresh(plan)["status"] == "already_applied"
    changed = [replace(bands[0], input_per_mtok=7), bands[1]]
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute(
            "UPDATE tp_pricing SET rate_bands_json=? WHERE model=?",
            (json.dumps([b.to_mapping() for b in changed]), "context-model"),
        )
    with pytest.raises(StalePricingRefreshPlanError):
        engine.apply_seed_refresh(plan)


def test_additive_upgrade_preserves_scalar_history_and_does_not_seed(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE tp_pricing (
            id INTEGER PRIMARY KEY, version TEXT, effective_date TEXT, provider TEXT,
            model TEXT, input_rate REAL, output_rate REAL, source TEXT, currency TEXT)""")
        conn.execute(
            "INSERT INTO tp_pricing VALUES (1,'old','2025-01-01','example','old',3,15,'custom','USD')"
        )
    engine = CostEngine(str(db))
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT * FROM tp_pricing").fetchall()
        assert len(row) == 1
        assert row[0][:9] == (1, "old", "2025-01-01", "example", "old", 3, 15, "custom", "USD")
    assert engine.get_pricing("old").rate_bands == ()


@pytest.mark.parametrize("value", [True, -1, float("inf"), float("nan"), 10**400])
def test_custom_cache_rates_must_be_finite_non_negative(engine, value):
    with pytest.raises(ValueError):
        engine.add_pricing("example", "bad-rate", 1, 2, cache_read_rate=value)
