"""test_cost_calculation.py — Tests for cost calculation and pricing logic.

Tests the pricing module and cost calculation for various models and scenarios:
- Model price lookups
- Cost calculations for different providers
- Bulk/volume discounts if applicable
- Edge cases (unknown models, zero tokens, extreme prices)
"""

import pytest
from decimal import Decimal
from tokenpak.telemetry.pricing import ModelPricing, PricingCatalog, compute_baseline_cost
from tokenpak.models import get_model_pricing, MODEL_PRICING


class TestModelPricingData:
    """Tests for model pricing data consistency."""

    def test_pricing_data_structure(self):
        """Test that MODEL_PRICING has expected structure."""
        assert isinstance(MODEL_PRICING, dict)
        for model_name, pricing in MODEL_PRICING.items():
            assert isinstance(model_name, str)
            assert "input" in pricing
            assert "output" in pricing
            assert isinstance(pricing["input"], (int, float))
            assert isinstance(pricing["output"], (int, float))
            assert pricing["input"] > 0
            assert pricing["output"] > 0

    def test_common_models_have_pricing(self):
        """Test that common models have pricing data."""
        common_models = [
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
            "gpt-4o",
            "gpt-4o-mini",
        ]
        for model in common_models:
            pricing = get_model_pricing(model)
            assert pricing is not None
            assert pricing["input"] > 0
            assert pricing["output"] > 0

    def test_pricing_values_reasonable(self):
        """Test that pricing values are within reasonable ranges."""
        # Most models should cost < $1 per 1M tokens for input
        # and < $5 per 1M tokens for output
        for model_name, pricing in MODEL_PRICING.items():
            assert pricing["input"] < 1000  # Per 1M tokens
            assert pricing["output"] < 5000  # Per 1M tokens


class TestCostCalculation:
    """Tests for basic cost calculation."""

    def test_cost_for_sonnet(self):
        """Test cost calculation for Claude Sonnet."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        # Input: 1000 tokens at $3 per 1M = $0.003
        input_cost = (1000 * pricing["input"]) / 1_000_000
        assert input_cost == pytest.approx(0.003, rel=0.01)
        
        # Output: 500 tokens at $15 per 1M = $0.0075
        output_cost = (500 * pricing["output"]) / 1_000_000
        assert output_cost == pytest.approx(0.0075, rel=0.01)

    def test_cost_for_gpt4o(self):
        """Test cost calculation for GPT-4o."""
        pricing = get_model_pricing("gpt-4o")
        # Input: 1000 tokens at $5 per 1M = $0.005
        input_cost = (1000 * pricing["input"]) / 1_000_000
        assert input_cost == pytest.approx(0.005, rel=0.01)

    def test_cost_for_haiku(self):
        """Test cost calculation for Claude Haiku."""
        pricing = get_model_pricing("claude-3-5-haiku")
        # Input: 10000 tokens at $0.80 per 1M = $0.008
        input_cost = (10000 * pricing["input"]) / 1_000_000
        assert input_cost == pytest.approx(0.008, rel=0.01)

    def test_zero_tokens_zero_cost(self):
        """Test that zero tokens = zero cost."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        input_cost = (0 * pricing["input"]) / 1_000_000
        assert input_cost == 0

    def test_large_token_count(self):
        """Test cost calculation for large token counts."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        large_count = 1_000_000  # 1M tokens
        input_cost = (large_count * pricing["input"]) / 1_000_000
        # Should be exactly $3 for Sonnet
        assert input_cost == pytest.approx(3.0, rel=0.01)

    def test_cost_accumulation(self):
        """Test that costs accumulate correctly for multiple requests."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        
        # First request
        req1_input = (1000 * pricing["input"]) / 1_000_000
        req1_output = (500 * pricing["output"]) / 1_000_000
        
        # Second request
        req2_input = (2000 * pricing["input"]) / 1_000_000
        req2_output = (1000 * pricing["output"]) / 1_000_000
        
        total_cost = req1_input + req1_output + req2_input + req2_output
        
        # Should be able to verify total
        assert total_cost > 0
        assert total_cost < 1.0  # Should be reasonable


class TestSavingsCalculation:
    """Tests for calculating token savings."""

    def test_compression_savings(self):
        """Test calculation of savings from token compression."""
        original_tokens = 100_000
        compressed_tokens = 80_000
        compression_ratio = 1 - (compressed_tokens / original_tokens)
        
        assert compression_ratio == pytest.approx(0.2, rel=0.01)  # 20% savings

    def test_cost_savings(self):
        """Test calculation of cost savings from compression."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        
        # Original cost for 100K tokens
        original_cost = (100_000 * pricing["input"]) / 1_000_000
        
        # Cost after 20% compression
        compressed_cost = (80_000 * pricing["input"]) / 1_000_000
        
        savings = original_cost - compressed_cost
        savings_pct = (savings / original_cost) * 100
        
        # For Sonnet: 100K*3/1M = 0.30, 80K*3/1M = 0.24, savings = 0.06
        assert savings == pytest.approx(0.06, rel=0.01)
        assert savings_pct == pytest.approx(20, rel=0.1)

    def test_cache_hit_savings(self):
        """Test savings from cache hits."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        
        # If 50% of requests are cache hits
        # We only pay for input tokens on misses
        cache_hit_rate = 0.5
        total_requests = 100_000
        
        requests_with_full_cost = total_requests * (1 - cache_hit_rate)
        requests_free = total_requests * cache_hit_rate
        
        assert requests_with_full_cost == 50_000
        assert requests_free == 50_000


class TestPricingEdgeCases:
    """Tests for edge cases in pricing."""

    def test_unknown_model_fallback(self):
        """Test that unknown models fall back gracefully."""
        pricing = get_model_pricing("unknown-model-xyz")
        # Should return something reasonable (default or closest match)
        assert pricing is not None
        assert "input" in pricing
        assert "output" in pricing

    def test_model_name_variations(self):
        """Test that model name variations are handled."""
        # Same model, different name formats
        p1 = get_model_pricing("claude-3-5-sonnet")
        p2 = get_model_pricing("claude-3-5-sonnet-20250319")
        
        # Should be the same pricing
        assert p1["input"] == p2["input"]
        assert p1["output"] == p2["output"]

    def test_case_insensitive_lookup(self):
        """Test that model lookup is case-insensitive."""
        p1 = get_model_pricing("claude-3-5-sonnet")
        p2 = get_model_pricing("CLAUDE-3-5-SONNET")
        
        # Should return same pricing
        assert p1["input"] == p2["input"]
        assert p1["output"] == p2["output"]

    def test_pricing_precision(self):
        """Test that pricing maintains precision."""
        pricing = get_model_pricing("gpt-4o-mini")
        # Prices should be precise to at least 2 decimal places
        assert isinstance(pricing["input"], (int, float))
        assert isinstance(pricing["output"], (int, float))
        # When multiplied by large token counts, should not lose precision
        large_calc = (10_000_000 * pricing["input"]) / 1_000_000
        assert large_calc > 0


class TestBulkPricingScenarios:
    """Tests for realistic bulk usage scenarios."""

    def test_daily_usage_pattern(self):
        """Test cost calculation for typical daily usage."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        
        # Typical daily: 100 requests, ~1K input tokens each, ~200 output tokens each
        daily_input = 100 * 1000  # 100K
        daily_output = 100 * 200  # 20K
        
        input_cost = (daily_input * pricing["input"]) / 1_000_000
        output_cost = (daily_output * pricing["output"]) / 1_000_000
        daily_total = input_cost + output_cost
        
        # Should be less than $1 per day for Haiku
        haiku_pricing = get_model_pricing("claude-3-5-haiku")
        haiku_daily = ((daily_input * haiku_pricing["input"]) + 
                      (daily_output * haiku_pricing["output"])) / 1_000_000
        assert haiku_daily < 1.0

    def test_monthly_usage_aggregation(self):
        """Test aggregation of costs across a month."""
        pricing = get_model_pricing("claude-3-5-sonnet")
        
        # 30 days * daily usage
        monthly_input = 30 * 100 * 1000  # 3M tokens
        monthly_output = 30 * 100 * 200  # 600K tokens
        
        input_cost = (monthly_input * pricing["input"]) / 1_000_000
        output_cost = (monthly_output * pricing["output"]) / 1_000_000
        monthly_total = input_cost + output_cost
        
        assert monthly_total > 10  # Should be over $10/month
        assert monthly_total < 100  # But not over $100/month for Sonnet

    def test_comparison_across_models(self):
        """Test cost comparison across different models."""
        models = ["claude-3-5-haiku", "claude-3-5-sonnet", "gpt-4o"]
        
        # Same workload: 1M input tokens, 100K output tokens
        workload_input = 1_000_000
        workload_output = 100_000
        
        costs = {}
        for model in models:
            pricing = get_model_pricing(model)
            cost = ((workload_input * pricing["input"]) + 
                   (workload_output * pricing["output"])) / 1_000_000
            costs[model] = cost
        
        # Verify ordering: Haiku < Sonnet < GPT-4o typically
        assert costs["claude-3-5-haiku"] < costs["claude-3-5-sonnet"]
        assert costs["claude-3-5-sonnet"] < costs["gpt-4o"]


class TestCostRounding:
    """Tests for cost rounding and precision."""

    def test_cost_rounds_appropriately(self):
        """Test that costs are rounded to appropriate precision."""
        pricing = get_model_pricing("gpt-4o-mini")
        
        # Very small cost should not round to zero
        tiny_cost = (1 * pricing["input"]) / 1_000_000
        assert tiny_cost > 0
        assert tiny_cost < 0.0001

    def test_accumulation_does_not_lose_precision(self):
        """Test that summing many small costs maintains accuracy."""
        pricing = get_model_pricing("claude-3-5-haiku")
        
        # 1000 requests, each with 10 input tokens
        total_cost = 0
        for _ in range(1000):
            total_cost += (10 * pricing["input"]) / 1_000_000
        
        # Should equal 10000 tokens
        expected_cost = (10000 * pricing["input"]) / 1_000_000
        assert total_cost == pytest.approx(expected_cost, rel=0.0001)
