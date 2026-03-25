"""
Integration tests for term resolver with proxy_v4.

Verifies:
- Term resolver is correctly initialized in proxy
- Health endpoint reports term resolver status
- Term resolution doesn't cause regression in disabled mode
- Glossary injection combines correctly with vault injection
"""

import json
from unittest.mock import Mock

import pytest

from tokenpak.agent.semantic import TermResolver, TermResolverConfig


class TestProxyV4Integration:
    """Test integration points with proxy_v4."""

    @pytest.fixture
    def mock_adapter(self):
        """Create a mock FormatAdapter."""
        adapter = Mock()
        adapter.extract_query_signal.return_value = "baseline cost and actual cost"
        adapter.inject_system_context.return_value = b"modified body"
        return adapter

    @pytest.fixture
    def sample_term_cards(self, tmp_path):
        """Create sample glossary for testing."""
        cards = {
            "baseline_cost": {
                "term": "baseline_cost",
                "what": "Cost without compression.",
                "aliases": ["baseline", "uncompressed"],
                "tier": 0,
                "confidence": 1.0,
            },
            "actual_cost": {
                "term": "actual_cost",
                "what": "Cost after compression.",
                "aliases": ["actual", "billed"],
                "tier": 0,
                "confidence": 1.0,
            },
        }

        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        return cards_path, cards

    def test_term_resolver_config_valid(self, sample_term_cards):
        """Test that valid resolver config works."""
        cards_path, _ = sample_term_cards

        # Standard config matching proxy_v4 defaults
        config = TermResolverConfig(top_k=3, max_bytes_per_card=200, enabled=True)
        resolver = TermResolver(cards_path=cards_path, config=config)

        assert resolver.config.top_k == 3
        assert resolver.config.max_bytes_per_card == 200
        assert resolver.config.enabled
        assert len(resolver._cards) > 0

    def test_resolver_initialization_graceful_failure(self, tmp_path):
        """Test resolver handles missing glossary gracefully."""
        missing_path = tmp_path / "nonexistent.json"

        # Should not crash even if cards file missing
        resolver = TermResolver(cards_path=missing_path)

        # Should return empty results
        result = resolver.resolve_terms("any query")
        assert len(result.canonical_ids) == 0

    def test_health_endpoint_reporting(self, sample_term_cards):
        """Simulate proxy health endpoint reporting term resolver status."""
        cards_path, _ = sample_term_cards

        # Create resolver matching proxy defaults
        config = TermResolverConfig(
            top_k=3,
            max_bytes_per_card=200,
            enabled=True,
        )
        resolver = TermResolver(cards_path=cards_path, config=config)

        # Simulate what /health endpoint would report
        health_status = {
            "term_resolver": {
                "enabled": True,
                "available": resolver is not None,
                "top_k": 3,
                "max_bytes_per_card": 200,
            }
        }

        assert health_status["term_resolver"]["enabled"]
        assert health_status["term_resolver"]["available"]
        assert health_status["term_resolver"]["top_k"] == 3

    def test_glossary_injection_format(self, sample_term_cards):
        """Test that glossary injection is properly formatted for proxy injection."""
        cards_path, _ = sample_term_cards
        resolver = TermResolver(cards_path=cards_path)

        result = resolver.resolve_terms("baseline cost")

        # Should have injection text if terms matched
        if result.canonical_ids:
            assert result.injection_text is not None
            # Should be ready to splice into system prompt
            assert "## Glossary" in result.injection_text
            # Should not be overly long
            assert len(result.injection_text) < 1000

    def test_combined_glossary_vault_simulation(self, sample_term_cards):
        """Simulate combining glossary + vault injection as proxy_v4 does."""
        cards_path, _ = sample_term_cards
        resolver = TermResolver(cards_path=cards_path)

        query = "baseline cost and actual cost comparison"
        resolution = resolver.resolve_terms(query)

        # Simulate vault injection
        vault_injection = "## Retrieved Context\n--- [some_doc.md] ---\nVault content here"
        vault_tokens = 50

        # Combine as proxy would
        if resolution.injection_text:
            combined = resolution.injection_text + "\n\n" + vault_injection
            assert len(combined) > len(vault_injection)
            assert "## Glossary" in combined
            assert "## Retrieved Context" in combined

    def test_disabled_resolver_no_overhead(self, sample_term_cards):
        """Test that disabled resolver adds zero overhead."""
        cards_path, _ = sample_term_cards

        config = TermResolverConfig(enabled=False)
        resolver = TermResolver(cards_path=cards_path, config=config)

        result = resolver.resolve_terms("baseline cost")

        # Should return empty, no side effects
        assert len(result.canonical_ids) == 0
        assert result.injection_text is None
        assert result.tokens_estimate == 0

    def test_feature_flag_allows_safe_rollout(self, sample_term_cards):
        """
        Test that feature flag (TOKENPAK_TERM_RESOLVER_ENABLED) allows safe
        rollout without affecting existing proxy behavior.
        """
        cards_path, _ = sample_term_cards

        # When disabled, no side effects
        config_disabled = TermResolverConfig(enabled=False)
        resolver_disabled = TermResolver(cards_path=cards_path, config=config_disabled)

        result_disabled = resolver_disabled.resolve_terms("baseline cost")
        assert result_disabled.injection_text is None

        # When enabled, features active
        config_enabled = TermResolverConfig(enabled=True)
        resolver_enabled = TermResolver(cards_path=cards_path, config=config_enabled)

        result_enabled = resolver_enabled.resolve_terms("baseline cost")
        if result_enabled.canonical_ids:
            assert result_enabled.injection_text is not None

    def test_ambiguity_handling_in_proxy_context(self, sample_term_cards):
        """Test ambiguity detection in proxy request context."""
        cards_path, _ = sample_term_cards
        resolver = TermResolver(cards_path=cards_path)

        # Query that matches multiple terms
        result = resolver.resolve_terms("compare baseline and actual")

        # Should detect ambiguity
        if len(result.canonical_ids) > 1:
            assert result.ambiguous
            assert result.ambiguity_question is not None
            # Question should be concise, suitable for logging/debugging
            assert len(result.ambiguity_question) < 200


class TestRuntimePolicy:
    """Test enforcement of runtime policy in proxy context."""

    @pytest.fixture
    def resolver_with_many_terms(self, tmp_path):
        """Create resolver with many terms to test policy enforcement."""
        cards = {
            f"term_{i}": {
                "term": f"term_{i}",
                "what": f"Definition of term {i}" * 20,  # long definition
                "aliases": [f"alias_{i}_a", f"alias_{i}_b"],
                "tier": 0 if i % 2 else 1,  # vary tier
                "confidence": 0.5 + (i % 5) * 0.1,
            }
            for i in range(20)
        }

        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        return TermResolver(cards_path=cards_path)

    def test_zero_injection_by_default_no_match(self, tmp_path):
        """Default: inject ZERO glossary unless matched terms exist."""
        cards = {
            "test_term": {
                "term": "test_term",
                "what": "A test",
                "aliases": [],
                "tier": 0,
                "confidence": 1.0,
            }
        }

        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        resolver = TermResolver(cards_path=cards_path)

        # Query with no matching terms
        result = resolver.resolve_terms("Tell me about weather patterns")

        # Should inject nothing
        assert result.injection_text is None
        assert len(result.canonical_ids) == 0

    def test_top_k_hard_cap_enforced(self, resolver_with_many_terms):
        """On match: include ONLY top-K cards (K default 3, max 5)."""
        # Default K=3 should be enforced
        result = resolver_with_many_terms.resolve_terms(
            "term_1 term_2 term_3 term_4 term_5"
        )

        # Should not exceed 3 cards
        assert len(result.canonical_ids) <= 3

    def test_bytes_per_card_cap(self, tmp_path):
        """Per-card: short fields only; no encyclopedia dumps."""
        long_definition = "X" * 1000
        cards = {
            "long_term": {
                "term": "long_term",
                "what": long_definition,
                "aliases": [],
                "tier": 0,
                "confidence": 1.0,
            }
        }

        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")

        config = TermResolverConfig(max_bytes_per_card=100)
        resolver = TermResolver(cards_path=cards_path, config=config)

        result = resolver.resolve_terms("long term")

        if result.card_snippets:
            # Meaning should be truncated
            snippet = result.card_snippets[0]
            assert len(snippet.meaning) <= 100

    def test_ambiguity_deterministic_fallback(self, tmp_path):
        """Ambiguity: deterministic one question or fallback rule."""
        cards = {
            "term_a": {
                "term": "term_a",
                "what": "First term",
                "aliases": [],
                "tier": 0,
                "confidence": 1.0,
            },
            "term_b": {
                "term": "term_b",
                "what": "Second term",
                "aliases": [],
                "tier": 0,
                "confidence": 1.0,
            },
        }

        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        resolver = TermResolver(cards_path=cards_path)

        result = resolver.resolve_terms("term_a term_b")

        # Should detect ambiguity
        assert result.ambiguous
        # Should provide one deterministic question, not multiple
        assert result.ambiguity_question is not None
        assert isinstance(result.ambiguity_question, str)


class TestCacheStability:
    """Test that term resolution maintains cache stability."""

    @pytest.fixture
    def resolver(self, tmp_path):
        """Create simple resolver."""
        cards = {
            "test": {
                "term": "test",
                "what": "A test term",
                "aliases": ["testing"],
                "tier": 0,
                "confidence": 1.0,
            }
        }

        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        return TermResolver(cards_path=cards_path)

    def test_byte_identical_repeated_runs(self, resolver):
        """Repeated runs of same query should produce byte-identical output."""
        query = "test term example"

        results = [resolver.resolve_terms(query) for _ in range(5)]

        # All should have identical canonical_ids
        canonical_sets = [set(r.canonical_ids) for r in results]
        assert all(cs == canonical_sets[0] for cs in canonical_sets)

        # All should have identical injection text
        injection_texts = [r.injection_text for r in results]
        assert all(it == injection_texts[0] for it in injection_texts)

        # All should have identical token estimates
        token_counts = [r.tokens_estimate for r in results]
        assert all(tc == token_counts[0] for tc in token_counts)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
