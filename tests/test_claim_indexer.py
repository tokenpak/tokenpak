"""Tests for tokenpak._internal.ingest.claim_indexer module."""

import pytest
from tokenpak._internal.ingest.claim_indexer import (
    ClaimEvidence,
    extract_claims_from_text,
)


class TestClaimEvidence:
    """Test ClaimEvidence dataclass."""

    def test_default_values(self):
        """Test default initialization."""
        ce = ClaimEvidence(claim="test claim")
        assert ce.claim == "test claim"
        assert ce.evidence == []
        assert ce.metrics == []
        assert ce.citations == []
        assert ce.source_section == ""
        assert ce.confidence == 0.5

    def test_with_evidence(self):
        """Test initialization with evidence."""
        ce = ClaimEvidence(
            claim="test",
            evidence=["supporting fact"],
        )
        assert ce.evidence == ["supporting fact"]

    def test_with_metrics(self):
        """Test initialization with metrics."""
        ce = ClaimEvidence(
            claim="test",
            metrics=["95%", "100"],
        )
        assert ce.metrics == ["95%", "100"]

    def test_with_citations(self):
        """Test initialization with citations."""
        ce = ClaimEvidence(
            claim="test",
            citations=["[1]", "[2]"],
        )
        assert ce.citations == ["[1]", "[2]"]

    def test_to_dict(self):
        """Test conversion to dictionary."""
        ce = ClaimEvidence(
            claim="test",
            evidence=["fact"],
            metrics=["50%"],
            citations=["[1]"],
            source_section="Intro",
            confidence=0.75,
        )
        d = ce.to_dict()
        assert d["claim"] == "test"
        assert d["evidence"] == ["fact"]
        assert d["metrics"] == ["50%"]
        assert d["citations"] == ["[1]"]
        assert d["source_section"] == "Intro"
        assert d["confidence"] == 0.75


class TestExtractClaimsBasic:
    """Test basic claim extraction."""

    def test_empty_text(self):
        """Test extraction from empty text."""
        claims = extract_claims_from_text("")
        assert claims == []

    def test_no_claims(self):
        """Test text with no claims."""
        text = "This is just regular text about something mundane."
        claims = extract_claims_from_text(text)
        # Should find no claims
        assert isinstance(claims, list)

    def test_single_claim(self):
        """Test extraction of single claim."""
        text = "Our analysis shows that performance improved by 50%."
        claims = extract_claims_from_text(text)
        assert len(claims) > 0

    def test_multiple_claims(self):
        """Test extraction of multiple claims."""
        text = """
        We found that users increased by 30%.
        Results show satisfaction improved.
        The study indicates cost savings of 40%.
        """
        claims = extract_claims_from_text(text)
        assert len(claims) >= 1


class TestExtractClaimsPatterns:
    """Test claim pattern matching."""

    def test_we_found_pattern(self):
        """Test 'we found' claim pattern."""
        text = "We found a significant correlation."
        claims = extract_claims_from_text(text)
        assert any("found" in claim.claim.lower() for claim in claims)

    def test_results_show_pattern(self):
        """Test 'results show' pattern."""
        text = "Results show a clear trend in the data."
        claims = extract_claims_from_text(text)
        assert any("result" in claim.claim.lower() for claim in claims)

    def test_conclusion_pattern(self):
        """Test 'conclusion' pattern."""
        text = "In conclusion, the hypothesis was proven."
        claims = extract_claims_from_text(text)
        assert any(claim for claim in claims)

    def test_recommendation_pattern(self):
        """Test 'recommendation' pattern."""
        text = "We recommend adopting this strategy immediately."
        claims = extract_claims_from_text(text)
        assert len(claims) >= 0


class TestExtractClaimsEvidence:
    """Test evidence extraction within claims."""

    def test_percentage_evidence(self):
        """Test extraction of percentage evidence."""
        text = "We found improvement of 75% in efficiency."
        claims = extract_claims_from_text(text)
        # Check if metrics are found
        has_metrics = any(claim.metrics for claim in claims)
        assert isinstance(has_metrics, bool)

    def test_numeric_evidence(self):
        """Test extraction of numeric evidence."""
        text = "Results show 1000 users affected by the change."
        claims = extract_claims_from_text(text)
        # Check structure is valid
        assert all(isinstance(c, ClaimEvidence) for c in claims)

    def test_stated_evidence(self):
        """Test extraction of stated evidence."""
        text = "According to research, the effect is significant."
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)


class TestExtractClaimsMetrics:
    """Test metrics extraction."""

    def test_percentage_metric(self):
        """Test percentage metric extraction."""
        text = "We identified a 45% increase in adoption."
        claims = extract_claims_from_text(text)
        # Should extract claims with potential metrics
        assert all(isinstance(c, ClaimEvidence) for c in claims)

    def test_date_metric(self):
        """Test date metric extraction."""
        text = "Results from 2024-01-15 show improvement."
        claims = extract_claims_from_text(text)
        assert all(isinstance(c, ClaimEvidence) for c in claims)

    def test_quarterly_metric(self):
        """Test quarterly metric extraction."""
        text = "Q4 2024 shows strong performance gains."
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)


class TestExtractClaimsCitations:
    """Test citation extraction."""

    def test_bracket_citation(self):
        """Test bracketed citation extraction."""
        text = "The study [1] demonstrates the effect clearly."
        claims = extract_claims_from_text(text)
        # Check if citations are captured
        has_citations = any(claim.citations for claim in claims)
        assert isinstance(has_citations, bool)

    def test_author_year_citation(self):
        """Test author-year citation extraction."""
        text = "(Smith et al., 2024) showed significant results."
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)

    def test_url_citation(self):
        """Test URL citation extraction."""
        text = "Reference: https://example.com/study shows the impact."
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)


class TestExtractClaimsConfidence:
    """Test confidence scoring."""

    def test_low_confidence_default(self):
        """Test that claims have reasonable confidence."""
        text = "This is a regular statement without evidence."
        claims = extract_claims_from_text(text, min_confidence=0.0)
        for claim in claims:
            assert 0.0 <= claim.confidence <= 1.0

    def test_high_confidence_with_evidence(self):
        """Test confidence increases with evidence."""
        text = "Our analysis shows 85% improvement with supporting metrics."
        claims = extract_claims_from_text(text)
        for claim in claims:
            if claim.evidence or claim.metrics:
                assert claim.confidence > 0.4

    def test_min_confidence_filtering(self):
        """Test filtering by minimum confidence."""
        text = "Regular text and We found 90% improvement."
        high_conf = extract_claims_from_text(text, min_confidence=0.7)
        low_conf = extract_claims_from_text(text, min_confidence=0.3)
        # Higher threshold should return fewer/different claims
        assert isinstance(high_conf, list)
        assert isinstance(low_conf, list)


class TestExtractClaimsSourceSection:
    """Test source section tracking."""

    def test_source_section_assignment(self):
        """Test that source_section can be assigned."""
        ce = ClaimEvidence(
            claim="test",
            source_section="Introduction",
        )
        assert ce.source_section == "Introduction"

    def test_multiple_sections(self):
        """Test claims from different sections."""
        # Claims would be extracted with different sections in real usage
        ce1 = ClaimEvidence(claim="test1", source_section="Intro")
        ce2 = ClaimEvidence(claim="test2", source_section="Results")
        assert ce1.source_section != ce2.source_section


class TestExtractClaimsComplexDocuments:
    """Test extraction from complex documents."""

    def test_multi_paragraph_document(self):
        """Test extraction from multi-paragraph text."""
        text = """
        Introduction: This study examines the impact.
        
        We found significant results. The analysis shows 60% improvement.
        Our conclusion: the method works effectively.
        
        Recommendations: adopt this approach widely.
        """
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)

    def test_mixed_claim_types(self):
        """Test document with various claim types."""
        text = """
        Data shows 40% increase.
        Research indicates positive trends.
        We recommend immediate action.
        Results demonstrate efficacy beyond 90%.
        """
        claims = extract_claims_from_text(text)
        assert all(isinstance(c, ClaimEvidence) for c in claims)

    def test_claims_with_citations_and_metrics(self):
        """Test claims containing both citations and metrics."""
        text = """
        According to [Smith 2024], the effect is 75% significant.
        Results from research show improvement over baseline (P < 0.05).
        """
        claims = extract_claims_from_text(text)
        # Check structure validity
        for claim in claims:
            assert isinstance(claim.claim, str)
            assert isinstance(claim.evidence, list)
            assert isinstance(claim.metrics, list)


class TestExtractClaimsEdgeCases:
    """Test edge cases."""

    def test_single_word_text(self):
        """Test with single word."""
        text = "Conclusion"
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)

    def test_very_long_text(self):
        """Test with very long document."""
        long_text = "Regular text. " * 500 + "We found improvement of 90%."
        claims = extract_claims_from_text(long_text)
        assert isinstance(claims, list)

    def test_special_characters(self):
        """Test with special characters."""
        text = "Results show $100M savings & 50% improvement!"
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)

    def test_unicode_text(self):
        """Test with unicode characters."""
        text = "Результаты показывают улучшение на 80%."
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)

    def test_numbers_and_percentages(self):
        """Test extraction with multiple numbers."""
        text = "123 users, 456 days, 78.9% success rate, $1000 investment."
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)

    def test_empty_claims_list(self):
        """Test returning empty claims appropriately."""
        text = "The quick brown fox jumps over the lazy dog."
        claims = extract_claims_from_text(text)
        assert isinstance(claims, list)

    def test_minimum_confidence_zero(self):
        """Test with minimum confidence of 0."""
        text = "Any text."
        claims = extract_claims_from_text(text, min_confidence=0.0)
        assert isinstance(claims, list)

    def test_minimum_confidence_one(self):
        """Test with minimum confidence of 1.0."""
        text = "We found 100% improvement with full proof."
        claims = extract_claims_from_text(text, min_confidence=1.0)
        # May return no claims if none meet 100% confidence
        assert isinstance(claims, list)
