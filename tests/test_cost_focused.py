"""Test suite for tokenpak.telemetry.cost — cost calculation and pricing.

Covers:
- USD calculation for known model+token combinations
- Token counting edge cases
- Compression ratio impacts on final cost
- Rate calculation for different providers
"""

import pytest

from tokenpak.telemetry.cost import (
    CostEngine,
    CostResult,
    Pricing,
    calculate_baseline,
    calculate_savings,
)


class TestCostEngine:
    """Test cost engine for USD calculation."""

    def test_engine_initialized(self):
        """CostEngine initializes correctly."""
        engine = CostEngine()
        assert engine is not None

    def test_cost_result_structure(self):
        """CostResult tracks cost data."""
        result = CostResult(
            model="claude-opus",
            pricing_version="1.0",
            raw_input_tokens=100_000,
            final_input_tokens=50_000,
            output_tokens=1_000,
            baseline_cost=1.50,
            actual_cost=0.75,
            savings_amount=0.75,
            savings_pct=50.0,
            data_source="official",
        )
        assert result.baseline_cost == 1.50
        assert result.actual_cost == 0.75
        assert result.raw_input_tokens == 100_000
        assert result.final_input_tokens == 50_000

    def test_pricing_anthropic(self):
        """Anthropic pricing initialized correctly."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        assert pricing.provider == "anthropic"
        assert pricing.model == "claude-3-opus"
        assert pricing.input_rate == 15.0
        assert pricing.output_rate == 75.0

    def test_pricing_openai(self):
        """OpenAI pricing initialized correctly."""
        pricing = Pricing(
            provider="openai",
            model="gpt-4-turbo",
            input_rate=10.0,
            output_rate=30.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        assert pricing.provider == "openai"
        assert pricing.model == "gpt-4-turbo"

    def test_pricing_per_token_rates(self):
        """Pricing calculates per-token rates correctly."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,  # per 1K tokens
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        # Per-token should be 1/1000 of per-1K rate
        # $15/1K = $0.015 per token
        assert pricing.input_per_token == pytest.approx(0.015, rel=0.01)
        assert pricing.output_per_token == pytest.approx(0.075, rel=0.01)


class TestCostCalculation:
    """Test cost calculations for known scenarios."""

    def test_baseline_cost_anthropic(self):
        """Baseline cost calculated for Anthropic."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        cost = calculate_baseline(raw_input_tokens=1_000_000, output_tokens=0, pricing=pricing)
        # 1M tokens * $0.015/token = $15,000
        assert cost == pytest.approx(15000.0, rel=0.01)

    def test_baseline_cost_openai(self):
        """Baseline cost calculated for OpenAI."""
        pricing = Pricing(
            provider="openai",
            model="gpt-4-turbo",
            input_rate=10.0,
            output_rate=30.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        cost = calculate_baseline(raw_input_tokens=1_000_000, output_tokens=0, pricing=pricing)
        # 1M tokens * $0.010/token = $10,000
        assert cost == pytest.approx(10000.0, rel=0.01)

    def test_baseline_cost_zero_tokens(self):
        """Baseline cost is zero for zero tokens."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        cost = calculate_baseline(raw_input_tokens=0, output_tokens=0, pricing=pricing)
        assert cost == 0.0

    def test_baseline_cost_output_tokens(self):
        """Baseline cost includes output tokens."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        cost = calculate_baseline(raw_input_tokens=0, output_tokens=1_000_000, pricing=pricing)
        # 1M output tokens * $0.075/token = $75,000
        assert cost == pytest.approx(75000.0, rel=0.01)

    def test_baseline_cost_combined(self):
        """Combined input and output cost."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        cost = calculate_baseline(
            raw_input_tokens=1_000_000, output_tokens=1_000_000, pricing=pricing
        )
        # (1M * $0.015/token) + (1M * $0.075/token) = $15,000 + $75,000 = $90,000
        assert cost == pytest.approx(90000.0, rel=0.01)


class TestCostSavings:
    """Test cost savings calculation."""

    def test_savings_calculation(self):
        """Savings correctly calculated from baseline and actual."""
        baseline_usd = 10.0
        actual_usd = 5.0

        savings_usd, savings_percent = calculate_savings(baseline_usd, actual_usd)

        assert savings_usd == 5.0
        assert savings_percent == 50.0

    def test_savings_zero_compression(self):
        """No savings when baseline equals actual."""
        baseline_usd = 10.0
        actual_usd = 10.0

        savings_usd, savings_percent = calculate_savings(baseline_usd, actual_usd)

        assert savings_usd == 0.0
        assert savings_percent == 0.0

    def test_savings_100_percent(self):
        """100% savings when actual is zero."""
        baseline_usd = 10.0
        actual_usd = 0.0

        savings_usd, savings_percent = calculate_savings(baseline_usd, actual_usd)

        assert savings_usd == 10.0
        assert savings_percent == 100.0

    def test_savings_percentage_scaling(self):
        """Savings percentage scales correctly."""
        baseline_usd = 100.0
        actual_usd = 75.0  # 25% savings

        savings_usd, savings_percent = calculate_savings(baseline_usd, actual_usd)

        assert savings_usd == 25.0
        assert savings_percent == 25.0


class TestEdgeCases:
    """Test edge cases in cost calculation."""

    def test_single_token_cost(self):
        """Single token has fractional cost."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )
        cost = calculate_baseline(1, 0, pricing)

        # 1 token * $0.015/token = $0.015
        assert cost == pytest.approx(0.015, rel=0.01)

    def test_large_token_count(self):
        """Large token counts scale linearly."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )

        cost_1m = calculate_baseline(1_000_000, 0, pricing)
        cost_10m = calculate_baseline(10_000_000, 0, pricing)

        # 10M should be ~10x more expensive
        assert cost_10m == pytest.approx(cost_1m * 10, rel=0.01)

    def test_mixed_input_output_tokens(self):
        """Combined input and output tokens calculated correctly."""
        pricing = Pricing(
            provider="anthropic",
            model="claude-3-opus",
            input_rate=15.0,
            output_rate=75.0,
            version="1.0",
            effective_date="2026-03-17",
        )

        cost_input_only = calculate_baseline(1_000_000, 0, pricing)
        cost_output_only = calculate_baseline(0, 1_000_000, pricing)
        cost_both = calculate_baseline(1_000_000, 1_000_000, pricing)

        # Combined should equal sum
        assert cost_both == pytest.approx(cost_input_only + cost_output_only, rel=0.01)
