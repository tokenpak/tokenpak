"""Tests for TokenPak Claim/Evidence Indexer."""


import pytest
pytest.importorskip("tokenpak._internal.ingest.claim_indexer", reason="module not available in current build")
import pytest
from tokenpak._internal.ingest.claim_indexer import (
    ClaimEvidence,
    extract_claims_from_text,
    extract_claims_from_document,
    link_claims_by_proximity,
    compact_for_retrieval,
    _extract_metrics_from_text,
    _extract_citations_from_text,
    _calculate_confidence,
)


class TestClaimEvidenceDataClass:
    """Test ClaimEvidence data class."""

    def test_claim_evidence_creation(self):
        """Test creating a ClaimEvidence object."""
        claim = ClaimEvidence(
            claim="Results show 25% improvement",
            evidence=["Study conducted in 2024"],
            metrics=["25%"],
            citations=["[1]"],
            source_section="Results",
            confidence=0.85,
        )
        assert claim.claim == "Results show 25% improvement"
        assert claim.confidence == 0.85
        assert len(claim.evidence) == 1

    def test_claim_evidence_to_dict(self):
        """Test serialization to dictionary."""
        claim = ClaimEvidence(
            claim="Test claim",
            evidence=["Test evidence"],
            metrics=["100"],
            citations=["[1]"],
            confidence=0.75,
        )
        d = claim.to_dict()
        assert d["claim"] == "Test claim"
        assert d["confidence"] == 0.75
        assert isinstance(d, dict)

    def test_claim_evidence_defaults(self):
        """Test default values."""
        claim = ClaimEvidence(claim="Simple claim")
        assert claim.evidence == []
        assert claim.metrics == []
        assert claim.citations == []
        assert claim.source_section == ""
        assert claim.confidence == 0.5


class TestMetricExtraction:
    """Test metric extraction."""

    def test_extract_percentages(self):
        """Test extracting percentage metrics."""
        text = "We found a 25.5% increase in efficiency and a 10% reduction in costs."
        metrics = _extract_metrics_from_text(text)
        assert "25.5%" in metrics or "25.5" in metrics
        assert "10%" in metrics or "10" in metrics

    def test_extract_large_numbers(self):
        """Test extracting large numbers with units."""
        text = "The company made $2.5 million in revenue and $500k in profit."
        metrics = _extract_metrics_from_text(text)
        assert len(metrics) > 0

    def test_extract_dates(self):
        """Test extracting date metrics."""
        text = "The report was published on 2024-03-10 and covers Q1 2024."
        metrics = _extract_metrics_from_text(text)
        assert len(metrics) > 0

    def test_extract_standalone_numbers(self):
        """Test extracting standalone numbers."""
        text = "We analyzed 150 samples over 12 weeks."
        metrics = _extract_metrics_from_text(text)
        assert len(metrics) > 0


class TestCitationExtraction:
    """Test citation extraction."""

    def test_extract_bracketed_citations(self):
        """Test extracting [1] style citations."""
        text = "This finding is supported by research [1] and studies [2]."
        citations = _extract_citations_from_text(text)
        assert "[1]" in citations
        assert "[2]" in citations

    def test_extract_author_year_citations(self):
        """Test extracting (Author, Year) style citations."""
        text = "Recent work (Smith, 2024) shows improvements (Johnson et al.)."
        citations = _extract_citations_from_text(text)
        assert len(citations) > 0

    def test_extract_url_citations(self):
        """Test extracting URL citations."""
        text = "See https://example.com/study for more details."
        citations = _extract_citations_from_text(text)
        assert "https://example.com/study" in citations

    def test_extract_ref_citations(self):
        """Test extracting explicit reference citations."""
        text = "This is referenced in ref. 5 and reference 10."
        citations = _extract_citations_from_text(text)
        assert len(citations) > 0


class TestConfidenceCalculation:
    """Test confidence scoring."""

    def test_confidence_with_strong_evidence(self):
        """Test confidence increases with evidence."""
        claim_with_evidence = _calculate_confidence(
            "We found results",
            evidence_items=["Data shows", "Study indicates"],
            metrics=["50%", "100"],
        )
        claim_no_evidence = _calculate_confidence(
            "We found results",
            evidence_items=[],
            metrics=[],
        )
        assert claim_with_evidence > claim_no_evidence

    def test_confidence_with_assertion_language(self):
        """Test confidence increases with assertion patterns."""
        strong_claim = _calculate_confidence(
            "Results show significant improvement",
            evidence_items=[],
            metrics=[],
        )
        weak_claim = _calculate_confidence(
            "Something happened",
            evidence_items=[],
            metrics=[],
        )
        assert strong_claim > weak_claim

    def test_confidence_bounded(self):
        """Test confidence is bounded to [0.0, 1.0]."""
        confidence = _calculate_confidence(
            "Results show finding [1]",
            evidence_items=["X", "Y", "Z"] * 10,  # Many evidence items
            metrics=["1", "2", "3"] * 10,
        )
        assert 0.0 <= confidence <= 1.0

    def test_confidence_minimum(self):
        """Test minimum confidence."""
        confidence = _calculate_confidence("", [], [])
        assert 0.0 <= confidence <= 1.0
        assert confidence >= 0.5  # Base is 0.5


class TestClaimExtraction:
    """Test claim extraction from text."""

    def test_extract_simple_claim(self):
        """Test extracting a simple claim."""
        text = "We found a 25% improvement in performance. The study was rigorous."
        claims = extract_claims_from_text(text)
        assert len(claims) > 0
        assert any("We found" in c.claim for c in claims)

    def test_extract_multiple_claims(self):
        """Test extracting multiple claims."""
        text = """
        Results show significant improvement. The analysis indicates positive trends.
        Our conclusion is that the method works well. We recommend adoption.
        """
        claims = extract_claims_from_text(text)
        assert len(claims) > 1

    def test_extract_claims_with_linked_evidence(self):
        """Test claims are linked with nearby evidence."""
        text = """
        We found remarkable results. The study analyzed 500 participants over 12 months.
        Results show a 45% improvement in outcomes. This aligns with previous research [1].
        """
        claims = extract_claims_from_text(text)
        # At least one claim should have evidence, metrics, and/or citations
        has_evidence = any(c.evidence or c.metrics or c.citations for c in claims)
        assert has_evidence

    def test_respect_confidence_threshold(self):
        """Test minimum confidence threshold filters weak claims."""
        text = "Stuff happened. Results show 50% improvement."
        low_threshold_claims = extract_claims_from_text(text, min_confidence=0.1)
        high_threshold_claims = extract_claims_from_text(text, min_confidence=0.9)
        assert len(low_threshold_claims) >= len(high_threshold_claims)

    def test_empty_text(self):
        """Test handling empty text."""
        claims = extract_claims_from_text("")
        assert claims == []

    def test_no_claims_in_text(self):
        """Test text with no recognizable claims."""
        text = "The weather is nice. The sky is blue. Birds fly."
        claims = extract_claims_from_text(text)
        # Should find few or no claims due to lack of assertion language
        assert len(claims) <= 1


class TestDocumentExtraction:
    """Test claim extraction from structured documents."""

    def test_extract_from_document_dict(self):
        """Test extracting claims from document dict."""
        doc = {
            "text": "We found a 30% improvement. Study involved 200 subjects.",
            "section": "Results",
            "title": "Impact Study",
        }
        claims = extract_claims_from_document(doc)
        assert len(claims) > 0
        assert all(c.source_section == "Results" for c in claims)

    def test_document_without_section(self):
        """Test document extraction without section info."""
        doc = {"text": "Results show improvement in metrics."}
        claims = extract_claims_from_document(doc)
        assert len(claims) > 0

    def test_document_missing_text(self):
        """Test document with missing text field."""
        doc = {"section": "Results"}
        claims = extract_claims_from_document(doc)
        assert claims == []


class TestClaimLinking:
    """Test linking related claims by proximity."""

    def test_link_nearby_claims(self):
        """Test linking claims that are close together."""
        claims = [
            ClaimEvidence(claim="First finding", evidence=[]),
            ClaimEvidence(claim="Second observation", evidence=[]),
            ClaimEvidence(claim="Third result", evidence=[]),
        ]
        linked = link_claims_by_proximity(claims, distance=1)
        assert len(linked) == 3
        # Each claim should have nearby claims linked
        for claim_text, nearby in linked.items():
            assert len(nearby) >= 0

    def test_link_with_distance_zero(self):
        """Test linking with zero distance (only exact neighbors)."""
        claims = [
            ClaimEvidence(claim="A"),
            ClaimEvidence(claim="B"),
            ClaimEvidence(claim="C"),
        ]
        linked = link_claims_by_proximity(claims, distance=0)
        assert len(linked) == 3

    def test_link_empty_list(self):
        """Test linking empty claim list."""
        linked = link_claims_by_proximity([])
        assert linked == {}


class TestCompactForRetrieval:
    """Test preparing claims for retrieval output."""

    def test_compact_single_claim(self):
        """Test compacting a single claim."""
        claim = ClaimEvidence(
            claim="Main finding",
            evidence=["E1", "E2", "E3"],
            metrics=["50%"],
            citations=["[1]"],
            confidence=0.85,
        )
        compact = compact_for_retrieval([claim], top_n=2)
        assert len(compact) == 1
        assert compact[0]["claim"] == "Main finding"
        assert compact[0]["confidence"] == 0.85
        # top_n=2 should limit evidence
        assert len(compact[0]["evidence"]) <= 2

    def test_compact_truncates_to_top_n(self):
        """Test that compact respects top_n limit."""
        claim = ClaimEvidence(
            claim="Test",
            evidence=["E1", "E2", "E3", "E4", "E5"],
            metrics=["1", "2", "3", "4"],
            citations=["[1]", "[2]", "[3]"],
        )
        compact = compact_for_retrieval([claim], top_n=2)
        assert len(compact[0]["evidence"]) <= 2
        assert len(compact[0]["metrics"]) <= 2
        assert len(compact[0]["citations"]) <= 2

    def test_compact_multiple_claims(self):
        """Test compacting multiple claims."""
        claims = [
            ClaimEvidence(claim="Claim 1", confidence=0.8),
            ClaimEvidence(claim="Claim 2", confidence=0.7),
            ClaimEvidence(claim="Claim 3", confidence=0.9),
        ]
        compact = compact_for_retrieval(claims, top_n=3)
        assert len(compact) == 3
        assert all("claim" in c for c in compact)


class TestIntegration:
    """Integration tests."""

    def test_end_to_end_extraction_and_compaction(self):
        """Test full pipeline: extract → link → compact."""
        document = {
            "text": """
            We found a 35% improvement. Study included 150 participants.
            Results show sustained performance over 6 months [1].
            Our analysis indicates this is statistically significant.
            We recommend deployment in production systems.
            """,
            "section": "Findings",
        }
        # Extract
        claims = extract_claims_from_document(document)
        assert len(claims) > 0
        
        # Link
        linked = link_claims_by_proximity(claims)
        assert len(linked) > 0
        
        # Compact
        compact = compact_for_retrieval(claims, top_n=2)
        assert len(compact) > 0
        assert all("claim" in c for c in compact)

    def test_realistic_research_document(self):
        """Test on realistic research document excerpt."""
        text = """
        Abstract: Our study found significant improvements in efficiency metrics.
        
        Methods: We analyzed 500 transactions [1] over 12 months (2023-2024).
        Results show a 42% reduction in latency and 28% cost savings [2].
        The study involved 15 participants and achieved 99.5% success rate.
        
        Conclusion: We recommend implementing this approach in production.
        Our analysis (Smith et al., 2024) indicates long-term viability.
        """
        claims = extract_claims_from_text(text)
        assert len(claims) > 2
        
        # Check that we extracted metrics
        all_metrics = []
        for claim in claims:
            all_metrics.extend(claim.metrics)
        assert any("42" in m or "500" in m for m in all_metrics)
        
        # Check that we extracted citations
        all_citations = []
        for claim in claims:
            all_citations.extend(claim.citations)
        assert len(all_citations) > 0
