"""
Unit tests for tokenpak/telemetry/cost.py

Directly tests CostEngine, Pricing, CostResult, and module-level helpers.
Avoids duplicating test_cost_integration.py coverage; focuses on:
  - Pricing lookup (exact match, fuzzy match, fallback)
  - add_pricing / list_pricing catalog management
  - cache_read_tokens reducing actual cost
  - CostResult.to_dict serialisation
  - Module-level calculate_baseline / calculate_actual / calculate_savings
  - _parse_date edge cases
  - Negative / clamped token inputs
"""

import tempfile
from pathlib import Path

import pytest

from tokenpak.telemetry.cost import (
    CURRENT_PRICING_VERSION,
    SEED_PRICING,
    CostEngine,
    CostResult,
    Pricing,
    calculate_actual,
    calculate_baseline,
    calculate_savings,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    """CostEngine backed by a temp DB."""
    db = tmp_path / "cost_test.db"
    return CostEngine(db_path=str(db))


# ---------------------------------------------------------------------------
# Pricing class
# ---------------------------------------------------------------------------


class TestPricingDataclass:
    """Unit-test the Pricing dataclass helpers."""

    def _make_pricing(self, input_rate=3.0, output_rate=15.0):
        return Pricing(
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_rate=input_rate,
            output_rate=output_rate,
            version=CURRENT_PRICING_VERSION,
            effective_date="2026-02-01",
        )

    def test_input_per_token(self):
        p = self._make_pricing(input_rate=3.0)
        assert p.input_per_token == pytest.approx(0.003)

    def test_output_per_token(self):
        p = self._make_pricing(output_rate=15.0)
        assert p.output_per_token == pytest.approx(0.015)

    def test_haiku_rates(self):
        p = self._make_pricing(input_rate=0.80, output_rate=4.0)
        assert p.input_per_token == pytest.approx(0.0008)
        assert p.output_per_token == pytest.approx(0.004)


# ---------------------------------------------------------------------------
# get_pricing — exact, fuzzy, fallback
# ---------------------------------------------------------------------------


class TestGetPricing:
    """CostEngine.get_pricing resolution paths."""

    def test_exact_match_known_model(self, engine):
        p = engine.get_pricing("claude-sonnet-4-6")
        assert p.model == "claude-sonnet-4-6"
        assert p.input_rate == pytest.approx(3.0)
        assert p.output_rate == pytest.approx(15.0)

    def test_exact_match_haiku(self, engine):
        p = engine.get_pricing("claude-haiku-4-5")
        assert p.input_rate == pytest.approx(0.80)
        assert p.output_rate == pytest.approx(4.0)

    def test_exact_match_opus(self, engine):
        p = engine.get_pricing("claude-opus-4-6")
        assert p.input_rate == pytest.approx(15.0)
        assert p.output_rate == pytest.approx(75.0)

    def test_fallback_unknown_model(self, engine):
        p = engine.get_pricing("totally-unknown-model-xyz")
        # Must return something (fallback pricing)
        assert p.input_rate > 0
        assert p.output_rate > 0
        assert p.source in ("estimated", "official")

    def test_pricing_cache_hit(self, engine):
        """Second call for same key uses cache (no second DB hit)."""
        p1 = engine.get_pricing("claude-sonnet-4-6")
        p2 = engine.get_pricing("claude-sonnet-4-6")
        assert p1.input_rate == p2.input_rate
        assert p1.model == p2.model

    def test_get_pricing_with_event_ts(self, engine):
        """event_ts accepted; pricing resolved."""
        p = engine.get_pricing("gpt-4o", event_ts="2026-03-01T10:00:00Z")
        assert p.input_rate > 0

    def test_source_official_for_seeded_model(self, engine):
        p = engine.get_pricing("claude-sonnet-4-6")
        assert p.source == "official"


# ---------------------------------------------------------------------------
# add_pricing / list_pricing
# ---------------------------------------------------------------------------


class TestPricingCatalog:
    """Catalog management: add_pricing and list_pricing."""

    def test_add_pricing_returns_row_id(self, engine):
        row_id = engine.add_pricing(
            provider="custom",
            model="my-model-v1",
            input_rate=1.0,
            output_rate=5.0,
        )
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_add_pricing_queryable(self, engine):
        engine.add_pricing(
            provider="custom",
            model="queryable-model",
            input_rate=2.5,
            output_rate=10.0,
        )
        p = engine.get_pricing("queryable-model")
        assert p.input_rate == pytest.approx(2.5)
        assert p.output_rate == pytest.approx(10.0)

    def test_list_pricing_all(self, engine):
        rows = engine.list_pricing()
        assert len(rows) >= len(SEED_PRICING)

    def test_list_pricing_by_version(self, engine):
        rows = engine.list_pricing(version=CURRENT_PRICING_VERSION)
        assert len(rows) >= len(SEED_PRICING)
        for r in rows:
            assert r["version"] == CURRENT_PRICING_VERSION

    def test_add_pricing_invalidates_cache(self, engine):
        """Adding new pricing clears the internal cache."""
        # Prime cache
        engine.get_pricing("cache-bust-model")
        engine.add_pricing(
            provider="test",
            model="cache-bust-model",
            input_rate=9.9,
            output_rate=9.9,
        )
        # Cache should be cleared; next get_pricing hits DB
        assert len(engine._pricing_cache) == 0


# ---------------------------------------------------------------------------
# calculate — cache_read_tokens, clamping
# ---------------------------------------------------------------------------


class TestCalculateEdgeCases:
    """calculate() edge cases not in test_cost_integration."""

    def test_cache_read_tokens_reduce_actual(self, engine):
        """cache_read_tokens lowers actual cost below baseline."""
        result = engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=5000,
            final_input_tokens=5000,
            output_tokens=500,
            cache_read_tokens=2000,
        )
        assert result.actual_cost < result.baseline_cost
        assert result.savings_amount > 0

    def test_cache_read_tokens_zero_has_no_effect(self, engine):
        """cache_read_tokens=0 is neutral."""
        r1 = engine.calculate("claude-sonnet-4-6", 1000, 1000, 100, cache_read_tokens=0)
        r2 = engine.calculate("claude-sonnet-4-6", 1000, 1000, 100)
        assert r1.actual_cost == pytest.approx(r2.actual_cost)

    def test_negative_raw_input_clamped_to_zero(self, engine):
        result = engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=-500,
            final_input_tokens=0,
            output_tokens=0,
        )
        assert result.baseline_cost == pytest.approx(0.0)
        assert result.raw_input_tokens == 0

    def test_negative_output_clamped_to_zero(self, engine):
        result = engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=1000,
            output_tokens=-100,
        )
        assert result.output_tokens == 0

    def test_savings_never_negative(self, engine):
        """Even if final > raw (shouldn't happen but guard exists), savings >= 0."""
        result = engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=100,
            final_input_tokens=900,  # larger than raw
            output_tokens=0,
        )
        assert result.savings_amount >= 0.0
        assert result.savings_pct >= 0.0

    def test_result_has_correct_pricing_version(self, engine):
        result = engine.calculate("claude-haiku-4-6", 1000, 800, 200)
        assert result.pricing_version == CURRENT_PRICING_VERSION


# ---------------------------------------------------------------------------
# CostResult.to_dict
# ---------------------------------------------------------------------------


class TestCostResultToDict:
    """to_dict() produces expected keys and rounds values."""

    @pytest.fixture
    def result(self, engine):
        return engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=10000,
            final_input_tokens=6000,
            output_tokens=500,
        )

    def test_to_dict_has_required_keys(self, result):
        d = result.to_dict()
        expected_keys = {
            "model", "pricing_version", "raw_input_tokens",
            "final_input_tokens", "output_tokens",
            "baseline_cost", "actual_cost",
            "savings_amount", "savings_pct", "data_source",
        }
        assert expected_keys.issubset(d.keys())

    def test_to_dict_model_name(self, result):
        assert result.to_dict()["model"] == "claude-sonnet-4-6"

    def test_to_dict_costs_rounded_6dp(self, result):
        d = result.to_dict()
        # Values should have at most 6 decimal places (rounded by to_dict)
        for key in ("baseline_cost", "actual_cost", "savings_amount"):
            val = d[key]
            assert isinstance(val, float)
            assert round(val, 6) == val

    def test_to_dict_data_source(self, result):
        assert result.to_dict()["data_source"] == "official"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleLevelHelpers:
    """calculate_baseline, calculate_actual, calculate_savings."""

    @pytest.fixture
    def sonnet_pricing(self):
        return Pricing(
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_rate=3.0,
            output_rate=15.0,
            version=CURRENT_PRICING_VERSION,
            effective_date="2026-02-01",
        )

    def test_calculate_baseline_math(self, sonnet_pricing):
        # 1000 input * 0.003 + 100 output * 0.015 = 3.0 + 1.5 = 4.5
        result = calculate_baseline(1000, 100, sonnet_pricing)
        assert result == pytest.approx(4.5)

    def test_calculate_baseline_zero(self, sonnet_pricing):
        assert calculate_baseline(0, 0, sonnet_pricing) == pytest.approx(0.0)

    def test_calculate_actual_with_cache_reads(self, sonnet_pricing):
        # final=1000, cache_read=400 → effective=600 * 0.003 + 100 * 0.015 = 1.8 + 1.5 = 3.3
        result = calculate_actual(1000, 100, sonnet_pricing, cache_read_tokens=400)
        assert result == pytest.approx(3.3)

    def test_calculate_actual_no_cache_reads(self, sonnet_pricing):
        # 1000 * 0.003 + 100 * 0.015 = 3.0 + 1.5 = 4.5
        result = calculate_actual(1000, 100, sonnet_pricing)
        assert result == pytest.approx(4.5)

    def test_calculate_savings_normal(self):
        amount, pct = calculate_savings(10.0, 6.0)
        assert amount == pytest.approx(4.0)
        assert pct == pytest.approx(40.0)

    def test_calculate_savings_zero_baseline(self):
        amount, pct = calculate_savings(0.0, 0.0)
        assert amount == pytest.approx(0.0)
        assert pct == pytest.approx(0.0)

    def test_calculate_savings_never_negative(self):
        """If actual > baseline (edge case), savings clamped to 0."""
        amount, pct = calculate_savings(5.0, 8.0)
        assert amount == pytest.approx(0.0)
        assert pct == pytest.approx(0.0)

    def test_calculate_savings_100_pct(self):
        amount, pct = calculate_savings(10.0, 0.0)
        assert amount == pytest.approx(10.0)
        assert pct == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


class TestParseDate:
    """CostEngine._parse_date static method."""

    def test_none_returns_today(self):
        from datetime import datetime, timezone
        d = CostEngine._parse_date(None)
        today = datetime.now(timezone.utc).date().isoformat()
        assert d == today

    def test_iso_z_suffix(self):
        d = CostEngine._parse_date("2026-03-15T12:00:00Z")
        assert d == "2026-03-15"

    def test_iso_with_offset(self):
        d = CostEngine._parse_date("2026-01-01T00:00:00+05:30")
        # Date portion (converted to UTC may shift, but should be parseable)
        assert d.startswith("202")

    def test_date_only_string(self):
        d = CostEngine._parse_date("2026-06-01")
        assert d == "2026-06-01"

    def test_invalid_ts_returns_today(self):
        from datetime import datetime, timezone
        d = CostEngine._parse_date("not-a-date")
        today = datetime.now(timezone.utc).date().isoformat()
        assert d == today
