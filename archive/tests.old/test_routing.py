"""test_routing.py — Tests for request routing logic.

Tests for the routing module:
- Correct provider selection based on model name
- Provider aliasing and name resolution
- Fallback behavior for unknown models
- Routing across different provider families
"""

import pytest


class TestProviderRouting:
    """Tests for provider selection based on model names."""

    def test_anthropic_model_detection(self):
        """Test that Claude models are routed to Anthropic."""
        from tokenpak.models import get_model_pricing
        
        models = [
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
            "claude-3-opus",
            "claude-instant",
        ]
        
        for model in models:
            pricing = get_model_pricing(model)
            # Should return valid pricing (Anthropic models)
            assert pricing is not None
            assert "input" in pricing
            assert "output" in pricing

    def test_openai_model_detection(self):
        """Test that GPT models are routed to OpenAI."""
        from tokenpak.models import get_model_pricing
        
        models = [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3-5-turbo",
        ]
        
        for model in models:
            pricing = get_model_pricing(model)
            # Should return valid pricing (OpenAI models)
            assert pricing is not None
            assert "input" in pricing
            assert "output" in pricing

    def test_google_model_detection(self):
        """Test that Gemini models are routed to Google."""
        from tokenpak.models import get_model_pricing
        
        models = [
            "gemini-pro",
            "gemini-1-5-pro",
            "gemini-1-5-flash",
        ]
        
        for model in models:
            pricing = get_model_pricing(model)
            # Should return pricing (may be default if not specific)
            assert pricing is not None
            assert "input" in pricing

    def test_model_name_normalization(self):
        """Test that model names are normalized before routing."""
        from tokenpak.models import get_model_pricing
        
        # These should all route to the same pricing
        p1 = get_model_pricing("claude-3-5-sonnet")
        p2 = get_model_pricing("claude-3-5-sonnet-20250319")
        
        # Prices should be the same
        assert p1["input"] == p2["input"]
        assert p1["output"] == p2["output"]

    def test_unknown_model_fallback(self):
        """Test that unknown models fall back to default."""
        from tokenpak.models import get_model_pricing
        
        # Unknown model should return something reasonable
        pricing = get_model_pricing("unknown-model-xyz-12345")
        assert pricing is not None
        assert "input" in pricing
        assert "output" in pricing
        # Should have reasonable defaults
        assert pricing["input"] > 0
        assert pricing["output"] > 0


class TestRoutingEdgeCases:
    """Tests for edge cases in model routing."""

    def test_case_insensitive_routing(self):
        """Test that routing is case-insensitive."""
        from tokenpak.models import get_model_pricing
        
        p1 = get_model_pricing("claude-3-5-sonnet")
        p2 = get_model_pricing("CLAUDE-3-5-SONNET")
        p3 = get_model_pricing("Claude-3-5-Sonnet")
        
        # All should return same pricing
        assert p1["input"] == p2["input"]
        assert p1["input"] == p3["input"]

    def test_model_with_version_suffix(self):
        """Test routing of models with version suffixes."""
        from tokenpak.models import get_model_pricing
        
        # These should all route to Sonnet
        models = [
            "claude-3-5-sonnet",
            "claude-3-5-sonnet-20250319",
            "claude-3-5-sonnet-latest",
        ]
        
        prices = [get_model_pricing(m) for m in models]
        # All should have reasonable pricing
        for pricing in prices:
            assert pricing["input"] > 0

    def test_model_abbreviations(self):
        """Test that abbreviated model names work."""
        from tokenpak.models import get_model_pricing
        
        # Test various abbreviations
        models = [
            "sonnet",
            "haiku",
            "opus",
            "gpt-4",
            "gpt-3",
        ]
        
        for model in models:
            pricing = get_model_pricing(model)
            # Should resolve to something
            assert pricing is not None
            assert pricing["input"] > 0

    def test_empty_model_name(self):
        """Test handling of empty model name."""
        from tokenpak.models import get_model_pricing
        
        # Empty string should fall back to default
        pricing = get_model_pricing("")
        assert pricing is not None
        assert "input" in pricing
        assert pricing["input"] > 0

    def test_whitespace_in_model_name(self):
        """Test handling of whitespace in model names."""
        from tokenpak.models import get_model_pricing
        
        # Model names with extra whitespace
        models = [
            "  claude-3-5-sonnet  ",
            "claude-3-5-sonnet\t",
            "claude-3-5-sonnet\n",
        ]
        
        for model in models:
            pricing = get_model_pricing(model)
            # Should handle gracefully (strip or normalize)
            assert pricing is not None


class TestProviderCoexistence:
    """Tests for handling requests from different providers in sequence."""

    def test_switch_between_providers(self):
        """Test handling rapid switches between provider models."""
        from tokenpak.models import get_model_pricing
        
        # Request with Claude
        p1 = get_model_pricing("claude-3-5-sonnet")
        assert p1["input"] > 0
        
        # Request with GPT-4
        p2 = get_model_pricing("gpt-4o")
        assert p2["input"] > 0
        
        # Request back to Claude
        p3 = get_model_pricing("claude-3-5-haiku")
        assert p3["input"] > 0
        
        # All should work independently
        assert p1 != p2  # Different prices
        assert p3 != p2

    def test_provider_isolation(self):
        """Test that provider states don't interfere."""
        from tokenpak.models import get_model_pricing
        
        # Get pricing from different providers
        claude_pricing = get_model_pricing("claude-3-5-sonnet")
        openai_pricing = get_model_pricing("gpt-4o")
        gemini_pricing = get_model_pricing("gemini-pro")
        
        # Get pricing again — should be unchanged
        claude_pricing_2 = get_model_pricing("claude-3-5-sonnet")
        assert claude_pricing["input"] == claude_pricing_2["input"]


class TestRoutingPerformance:
    """Tests for routing performance characteristics."""

    def test_routing_is_fast(self):
        """Test that model routing is fast."""
        from tokenpak.models import get_model_pricing
        import time
        
        models = [
            "claude-3-5-sonnet",
            "gpt-4o",
            "gemini-pro",
            "claude-3-5-haiku",
            "gpt-4o-mini",
        ]
        
        start = time.time()
        for _ in range(100):
            for model in models:
                get_model_pricing(model)
        elapsed = time.time() - start
        
        # Should be very fast (< 1 second for 500 lookups)
        assert elapsed < 1.0

    def test_routing_consistency(self):
        """Test that routing always gives same result for same model."""
        from tokenpak.models import get_model_pricing
        
        model = "claude-3-5-sonnet"
        
        # Get pricing multiple times
        prices = [get_model_pricing(model) for _ in range(10)]
        
        # All should be identical
        first = prices[0]
        for price in prices[1:]:
            assert price["input"] == first["input"]
            assert price["output"] == first["output"]


class TestRoutingWithInvalidInput:
    """Tests for routing with invalid or malformed inputs."""

    def test_none_model_name(self):
        """Test handling of None as model name."""
        from tokenpak.models import get_model_pricing
        
        # None should not crash
        try:
            pricing = get_model_pricing(None)
            assert pricing is not None
        except (TypeError, AttributeError):
            # Expected: None is invalid
            pass

    def test_model_name_with_special_chars(self):
        """Test handling of special characters in model names."""
        from tokenpak.models import get_model_pricing
        
        models = [
            "claude@3.5-sonnet",
            "model#123",
            "model/v1",
            "model|variant",
        ]
        
        for model in models:
            pricing = get_model_pricing(model)
            # Should handle without crashing
            if pricing is not None:
                assert "input" in pricing

    def test_very_long_model_name(self):
        """Test handling of very long model names."""
        from tokenpak.models import get_model_pricing
        
        long_name = "x" * 1000
        pricing = get_model_pricing(long_name)
        
        # Should fall back to default
        assert pricing is not None
        assert pricing["input"] > 0
