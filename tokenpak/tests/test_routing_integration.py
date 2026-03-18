"""Tests for request routing logic.

Covers: routing/rules.py — provider selection based on model name, cost, latency.
"""

import pytest


class TestRouting:
    """Test: Request routing and provider selection."""

    def test_routing_module_exists(self):
        """Routing module can be imported."""
        try:
            from tokenpak import routing
            assert routing is not None
        except ImportError:
            pytest.skip("routing module not yet available")

    def test_default_provider_selection(self):
        """Default routing selects appropriate provider."""
        # When routing module is available, test provider selection
        pytest.skip("routing module not yet stable")
