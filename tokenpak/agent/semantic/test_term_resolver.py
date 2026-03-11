"""
Tests for term resolver — deterministic term-card resolution.

Covers:
- Basic term extraction (canonical + aliases)
- Deterministic ordering (equivalent variants -> same result)
- Ambiguity detection
- Hard caps enforcement (top-K, bytes per card)
- Zero injection by default
- Cache stability (byte-identical repeated runs)
"""

import pytest
import json
import tempfile
from pathlib import Path
from tokenpak.agent.semantic import (
    TermResolver,
    TermResolverConfig,
    TermResolution,
    resolve_terms,
)


class TestTermCardSnippet:
    """Test TermCardSnippet formatting."""
    
    def test_snippet_formatting(self):
        from tokenpak.agent.semantic.term_resolver import TermCardSnippet
        
        snippet = TermCardSnippet(
            canonical_id="baseline_cost",
            meaning="Cost without compression",
            aliases=["baseline", "uncompressed cost"],
            confidence=1.0,
        )
        formatted = snippet.to_injection_format()
        assert "baseline_cost" in formatted
        assert "also: baseline, uncompressed cost" in formatted
        assert "Cost without compression" in formatted


class TestTermResolverBasics:
    """Test basic resolver functionality."""
    
    @pytest.fixture
    def sample_cards(self, tmp_path):
        """Create sample term_cards.json for testing."""
        cards = {
            "baseline_cost": {
                "term": "baseline_cost",
                "what": "Cost without compression — full uncompressed cost.",
                "who": "FinOps, budget owners",
                "where": ["finops dashboard", "cost cards"],
                "why": "Reference point for compression value",
                "how": "raw × rate",
                "not_this": "Not actual spend",
                "aliases": ["baseline", "uncompressed cost", "full cost"],
                "tier": 0,
                "confidence": 1.0,
                "source_refs": ["finops.html"],
            },
            "actual_cost": {
                "term": "actual_cost",
                "what": "Cost after compression — tokens sent to provider × price.",
                "who": "FinOps",
                "where": ["finops dashboard"],
                "why": "Real cost figure",
                "how": "final × rate",
                "not_this": "Not baseline",
                "aliases": ["actual", "real cost", "billed cost"],
                "tier": 0,
                "confidence": 1.0,
                "source_refs": ["finops.html"],
            },
            "compression_ratio": {
                "term": "compression_ratio",
                "what": "How much smaller compressed context is vs original.",
                "who": "Engineering",
                "where": ["engineering dashboard"],
                "why": "Direct measure of efficiency",
                "how": "raw ÷ final",
                "not_this": "Not savings %",
                "aliases": ["ratio", "compression factor"],
                "tier": 0,
                "confidence": 1.0,
                "source_refs": ["engineering.html"],
            },
        }
        
        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards, indent=2), encoding="utf-8")
        return cards_path, cards
    
    def test_resolver_loads_cards(self, sample_cards):
        cards_path, expected = sample_cards
        resolver = TermResolver(cards_path=cards_path)
        
        # Verify cards loaded
        assert len(resolver._cards) == 3
        assert "baseline_cost" in resolver._cards
        assert "actual_cost" in resolver._cards
    
    def test_resolve_single_term(self, sample_cards):
        cards_path, _ = sample_cards
        resolver = TermResolver(cards_path=cards_path)
        
        result = resolver.resolve_terms("What is the baseline cost?")
        
        assert "baseline_cost" in result.canonical_ids
        assert len(result.card_snippets) > 0
        assert not result.ambiguous
        assert result.injection_text is not None
        assert "baseline_cost" in result.injection_text
    
    def test_resolve_multiple_terms(self, sample_cards):
        cards_path, _ = sample_cards
        resolver = TermResolver(cards_path=cards_path)
        
        result = resolver.resolve_terms("Compare baseline cost and actual cost")
        
        # Both should be matched
        assert "baseline_cost" in result.canonical_ids
        assert "actual_cost" in result.canonical_ids
        assert result.ambiguous
        assert result.ambiguity_question is not None
    
    def test_resolve_alias(self, sample_cards):
        """Test matching via aliases."""
        cards_path, _ = sample_cards
        resolver = TermResolver(cards_path=cards_path)
        
        # "uncompressed cost" is an alias for baseline_cost
        result = resolver.resolve_terms("the uncompressed cost is high")
        
        assert "baseline_cost" in result.canonical_ids
        assert not result.ambiguous
    
    def test_no_matches_returns_empty(self, sample_cards):
        cards_path, _ = sample_cards
        resolver = TermResolver(cards_path=cards_path)
        
        result = resolver.resolve_terms("Tell me about pizza")
        
        assert len(result.canonical_ids) == 0
        assert len(result.card_snippets) == 0
        assert result.injection_text is None
        assert result.tokens_estimate == 0
    
    def test_disabled_resolver_returns_empty(self, sample_cards):
        cards_path, _ = sample_cards
        config = TermResolverConfig(enabled=False)
        resolver = TermResolver(cards_path=cards_path, config=config)
        
        result = resolver.resolve_terms("baseline cost and actual cost")
        
        assert len(result.canonical_ids) == 0
        assert result.injection_text is None


class TestTermResolverDeterminism:
    """Test deterministic behavior for cache stability."""
    
    @pytest.fixture
    def resolver_with_cards(self, tmp_path):
        """Create resolver with sample cards."""
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
        return TermResolver(cards_path=cards_path)
    
    def test_repeated_queries_identical(self, resolver_with_cards):
        """Same query should produce identical results."""
        query = "baseline and actual cost"
        
        result1 = resolver_with_cards.resolve_terms(query)
        result2 = resolver_with_cards.resolve_terms(query)
        result3 = resolver_with_cards.resolve_terms(query)
        
        # All results should be identical
        assert result1.canonical_ids == result2.canonical_ids == result3.canonical_ids
        assert result1.injection_text == result2.injection_text == result3.injection_text
        assert result1.tokens_estimate == result2.tokens_estimate == result3.tokens_estimate
    
    def test_equivalent_text_variants_resolve_same(self, resolver_with_cards):
        """Different phrasings of same concept should resolve to same targets."""
        queries = [
            "baseline cost",
            "baseline_cost",
            "uncompressed cost",  # alias
        ]
        
        results = [resolver_with_cards.resolve_terms(q) for q in queries]
        
        # All should resolve to baseline_cost
        canonical_ids_sets = [set(r.canonical_ids) for r in results]
        assert all(cids >= {"baseline_cost"} for cids in canonical_ids_sets)


class TestTermResolverHardCaps:
    """Test enforcement of runtime policy hard caps."""
    
    @pytest.fixture
    def large_resolver(self, tmp_path):
        """Create resolver with many terms."""
        cards = {f"term_{i}": {
            "term": f"term_{i}",
            "what": f"This is term {i}",
            "aliases": [f"alias_{i}"],
            "tier": 0,
            "confidence": 1.0,
        } for i in range(10)}
        
        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        return TermResolver(cards_path=cards_path)
    
    def test_top_k_enforcement(self, large_resolver):
        """Config top_k should be enforced (max 5)."""
        config = TermResolverConfig(top_k=10)
        assert config.top_k == 5  # capped to 5
        
        config2 = TermResolverConfig(top_k=3)
        assert config2.top_k == 3  # unchanged if < 5
    
    def test_snippet_limit_applied(self, tmp_path):
        """Snippets should respect max_bytes_per_card."""
        long_text = "X" * 500  # Very long meaning
        cards = {
            "long_term": {
                "term": "long_term",
                "what": long_text,
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
        
        # Snippet should be truncated
        snippet = result.card_snippets[0]
        assert len(snippet.meaning) <= 100


class TestAmbiguityHandling:
    """Test deterministic ambiguity detection and questions."""
    
    @pytest.fixture
    def ambiguous_resolver(self, tmp_path):
        """Resolver with terms that could be ambiguous."""
        cards = {
            "cost": {
                "term": "cost",
                "what": "General cost concept.",
                "aliases": ["price"],
                "tier": 1,
                "confidence": 1.0,
            },
            "baseline_cost": {
                "term": "baseline_cost",
                "what": "Cost before compression.",
                "aliases": ["baseline"],
                "tier": 0,
                "confidence": 1.0,
            },
            "actual_cost": {
                "term": "actual_cost",
                "what": "Cost after compression.",
                "aliases": ["actual"],
                "tier": 0,
                "confidence": 1.0,
            },
        }
        
        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        return TermResolver(cards_path=cards_path)
    
    def test_ambiguity_question_format(self, ambiguous_resolver):
        """Ambiguity questions should be deterministic."""
        result = ambiguous_resolver.resolve_terms("baseline vs actual")
        
        assert result.ambiguous
        assert result.ambiguity_question is not None
        # Question should mention both terms
        assert "baseline_cost" in result.ambiguity_question or "baseline" in result.ambiguity_question
        assert "actual_cost" in result.ambiguity_question or "actual" in result.ambiguity_question
    
    def test_same_ambiguity_question_repeated(self, ambiguous_resolver):
        """Same ambiguous query should produce identical question."""
        query = "baseline vs actual"
        
        result1 = ambiguous_resolver.resolve_terms(query)
        result2 = ambiguous_resolver.resolve_terms(query)
        
        assert result1.ambiguity_question == result2.ambiguity_question


class TestModuleLevelFunction:
    """Test module-level resolve_terms convenience function."""
    
    def test_resolve_terms_function(self, tmp_path):
        """Test global resolve_terms() function."""
        cards = {
            "test_term": {
                "term": "test_term",
                "what": "A test term.",
                "aliases": ["test"],
                "tier": 0,
                "confidence": 1.0,
            }
        }
        
        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        
        config = TermResolverConfig()
        # This would use the config to create a new resolver
        result = resolve_terms("test term here", config=config)
        
        # Should return TermResolution
        assert isinstance(result, TermResolution)


class TestIntegrationWithProxy:
    """Test integration patterns for proxy_v4."""
    
    @pytest.fixture
    def proxy_ready_resolver(self, tmp_path):
        """Create resolver matching typical proxy usage."""
        cards = {
            "compression_ratio": {
                "term": "compression_ratio",
                "what": "Ratio of original to compressed tokens.",
                "aliases": ["ratio", "compression factor"],
                "tier": 0,
                "confidence": 1.0,
            },
            "baseline_cost": {
                "term": "baseline_cost",
                "what": "Uncompressed cost baseline.",
                "aliases": ["baseline", "uncompressed"],
                "tier": 0,
                "confidence": 1.0,
            },
        }
        
        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        return TermResolver(cards_path=cards_path)
    
    def test_zero_injection_by_default_on_no_match(self, proxy_ready_resolver):
        """When no terms match, should inject nothing."""
        result = proxy_ready_resolver.resolve_terms("what is the weather today")
        
        assert len(result.canonical_ids) == 0
        assert result.injection_text is None
        # Should NOT pollute the request
    
    def test_injection_format_ready_for_system_prompt(self, proxy_ready_resolver):
        """Injection text should be ready to splice into system prompt."""
        result = proxy_ready_resolver.resolve_terms("compression ratio and baseline")
        
        if result.injection_text:
            # Should have proper section header
            assert "## Glossary" in result.injection_text
            # Should be concise (not encyclopedia dump)
            assert len(result.injection_text) < 500
    
    def test_token_estimate_reasonable(self, proxy_ready_resolver):
        """Token estimates should be reasonable rough approximations."""
        result = proxy_ready_resolver.resolve_terms("compression ratio")
        
        if result.injection_text:
            # Rough check: 1 token ≈ 4 chars
            expected_tokens = len(result.injection_text) // 4
            assert abs(result.tokens_estimate - expected_tokens) < 10


class TestNoRegressionWhenDisabled:
    """Verify no regression when feature disabled."""
    
    def test_disabled_feature_zero_overhead(self, tmp_path):
        """When disabled, resolver should have zero overhead."""
        cards = {"test": {"term": "test", "what": "test", "tier": 0, "confidence": 1.0}}
        cards_path = tmp_path / "term_cards.json"
        cards_path.write_text(json.dumps(cards), encoding="utf-8")
        
        config = TermResolverConfig(enabled=False)
        resolver = TermResolver(cards_path=cards_path, config=config)
        
        # Even matching query should return empty
        result = resolver.resolve_terms("test term")
        
        assert len(result.canonical_ids) == 0
        assert result.injection_text is None
        assert result.tokens_estimate == 0
        # Query passed through
        assert result.query == "test term"
    
    def test_empty_cards_no_crash(self):
        """Resolver with no cards should not crash."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cards_path = Path(tmpdir) / "term_cards.json"
            cards_path.write_text("{}", encoding="utf-8")
            
            resolver = TermResolver(cards_path=cards_path)
            result = resolver.resolve_terms("any query")
            
            assert len(result.canonical_ids) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
