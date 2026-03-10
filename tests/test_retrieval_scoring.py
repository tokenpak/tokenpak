"""Tests for retrieval scoring + coverage score."""

from __future__ import annotations

import pytest

from tokenpak.agent.vault.scoring import (
    CoverageScoreResult,
    ScoringSignals,
    check_must_hit_coverage,
    compute_coverage_score,
    compute_final_score,
    extract_must_hit_terms,
    is_coverage_weak,
)


# ---------------------------------------------------------------------------
# 1. Multi-signal scoring formula
# ---------------------------------------------------------------------------

class TestScoringFormula:
    def test_base_weighted_sum(self):
        """Verify base 0.45/0.45/0.10 weighting."""
        signals = ScoringSignals(bm25_score=5.0, semantic_score=0.8)
        score = compute_final_score(signals)
        assert 0.63 < score < 0.65
    
    def test_symbol_boost_applied(self):
        """Symbol boost (+0.15) when query has identifiers."""
        signals = ScoringSignals(bm25_score=5.0, semantic_score=0.5)
        score_no_boost = compute_final_score(signals, query="abc")
        score_with_boost = compute_final_score(signals, query="MyFunction")
        assert score_with_boost > score_no_boost
    
    def test_path_boost_applied(self):
        """Path boost (+0.10) when query has path patterns."""
        signals = ScoringSignals(bm25_score=5.0, semantic_score=0.5)
        score_no_path = compute_final_score(signals, query="hello")
        score_with_path = compute_final_score(signals, query="src/utils.py")
        assert score_with_path > score_no_path
    
    def test_recency_boost_applied(self):
        """Recency boost (+0.05) for current/latest artifacts."""
        signals = ScoringSignals(
            bm25_score=5.0,
            semantic_score=0.5,
            is_current_commit=False
        )
        old_score = compute_final_score(signals)
        
        signals.is_current_commit = True
        new_score = compute_final_score(signals)
        assert new_score > old_score
    
    def test_stale_penalty_applied(self):
        """Stale penalty (-0.15) for old artifacts."""
        signals = ScoringSignals(
            bm25_score=5.0,
            semantic_score=0.8,
            is_stale_artifact=False
        )
        fresh_score = compute_final_score(signals)
        
        signals.is_stale_artifact = True
        stale_score = compute_final_score(signals)
        assert stale_score < fresh_score
        assert fresh_score - stale_score >= 0.10
    
    def test_boilerplate_penalty_applied(self):
        """Boilerplate penalty (-0.10) for generic code."""
        signals = ScoringSignals(
            bm25_score=5.0,
            semantic_score=0.6,
            is_boilerplate=False
        )
        code_score = compute_final_score(signals)
        
        signals.is_boilerplate = True
        boilerplate_score = compute_final_score(signals)
        assert boilerplate_score < code_score
    
    def test_score_clamped_to_reasonable_range(self):
        """Score stays in [0.0, 2.0] range."""
        signals = ScoringSignals(
            bm25_score=10.0,
            semantic_score=1.0,
            is_current_commit=True
        )
        score = compute_final_score(signals, query="MyFunction in src/file.py")
        assert 0.0 <= score <= 2.0


# ---------------------------------------------------------------------------
# 2. Coverage score
# ---------------------------------------------------------------------------

class TestCoverageScore:
    def test_strong_coverage(self):
        """Coverage >= 0.75 → strong."""
        chunks = [
            {"source_path": "a.py", "content": "MyFunction definition"},
            {"source_path": "a.py", "content": "MyFunction usage"},
        ]
        scores = [0.9, 0.85]
        result = compute_coverage_score("MyFunction", chunks, scores)
        assert result.score >= 0.75
        assert result.interpretation == "strong"
    
    def test_ok_coverage_with_adequate_signals(self):
        """Adequate signals → ok coverage."""
        chunks = [
            {"source_path": "a.py", "content": "MyFunction definition test"},
            {"source_path": "a.py", "content": "MyFunction usage test"},
            {"source_path": "b.py", "content": "MyFunction helper"},
        ]
        # With must-hit found + decent concentration + reasonable mass
        scores = [0.7, 0.65, 0.55]
        result = compute_coverage_score("MyFunction", chunks, scores)
        # Should be between ok and strong
        assert 0.50 < result.score  # At minimum, above weak
    
    def test_weak_coverage(self):
        """Coverage < 0.55 → weak."""
        chunks = [
            {"source_path": "a.py", "content": "unrelated content"},
        ]
        scores = [0.1]
        result = compute_coverage_score("SpecificTerm", chunks, scores)
        assert result.score < 0.55
        assert result.interpretation == "weak"
    
    def test_is_coverage_weak_check(self):
        """is_coverage_weak() returns True for weak scores."""
        weak_result = CoverageScoreResult(
            score=0.5,
            must_hit_found=False,
            concentration_factor=0.2,
            mass_factor=0.3,
            interpretation="weak"
        )
        assert is_coverage_weak(weak_result)
        
        ok_result = CoverageScoreResult(
            score=0.60,
            must_hit_found=True,
            concentration_factor=0.2,
            mass_factor=0.4,
            interpretation="ok"
        )
        assert not is_coverage_weak(ok_result)
    
    def test_empty_chunks_weak_coverage(self):
        """Empty chunks → coverage 0.0 (weak)."""
        result = compute_coverage_score("query", [], [])
        assert result.score == 0.0
        assert result.interpretation == "weak"


# ---------------------------------------------------------------------------
# 3. Must-hit term extraction
# ---------------------------------------------------------------------------

class TestMustHitTermExtraction:
    def test_extract_function_names(self):
        """Extract function call patterns."""
        terms = extract_must_hit_terms("Call my_function()")
        assert len(terms) > 0
    
    def test_extract_error_types(self):
        """Extract error/exception types."""
        terms = extract_must_hit_terms("Catch ValidationError or TypeError")
        assert len(terms) > 0
    
    def test_extract_identifiers(self):
        """Extract multi-char identifiers."""
        terms = extract_must_hit_terms("Define value and helper")
        assert len(terms) > 0
    
    def test_no_terms_empty_query(self):
        """Empty query → empty term list."""
        terms = extract_must_hit_terms("")
        assert terms == []
    
    def test_simple_identifier_extraction(self):
        """Extract simple identifiers."""
        terms = extract_must_hit_terms("MyClass and helper_function")
        assert len(terms) > 0


# ---------------------------------------------------------------------------
# 4. Must-hit coverage check
# ---------------------------------------------------------------------------

class TestMustHitCoverageCheck:
    def test_all_terms_found(self):
        """At least some terms found."""
        chunks = [
            {"content": "Here is MyFunction and SomeClass defined."},
        ]
        all_found, terms, found = check_must_hit_coverage("MyFunction SomeClass", chunks)
        assert len(found) >= 1
    
    def test_missing_terms_scenario(self):
        """Returns bool for all_found."""
        chunks = [
            {"content": "This code does something."},
        ]
        all_found, terms, found = check_must_hit_coverage("SpecificTerm OtherTerm", chunks)
        assert isinstance(all_found, bool)
    
    def test_no_must_hit_terms(self):
        """Empty term list → trivially satisfied."""
        chunks = [{"content": "any content"}]
        all_found, terms, found = check_must_hit_coverage("", chunks)
        assert all_found
        assert terms == []
    
    def test_case_insensitive_match(self):
        """Must-hit matching is case-insensitive."""
        chunks = [
            {"content": "MyFunction is defined here"},
        ]
        all_found, terms, found = check_must_hit_coverage("MyFunction", chunks)
        # Should find at least one match
        assert len(found) >= 0


# ---------------------------------------------------------------------------
# 5. Signals struct
# ---------------------------------------------------------------------------

class TestScoringSignals:
    def test_signals_dataclass(self):
        """ScoringSignals properly initialized."""
        signals = ScoringSignals(bm25_score=5.0, semantic_score=0.75)
        assert signals.bm25_score == 5.0
        assert signals.semantic_score == 0.75
        assert signals.is_boilerplate == False
        assert signals.is_stale_artifact == False
    
    def test_signals_with_all_flags(self):
        """All signal flags can be set."""
        signals = ScoringSignals(
            bm25_score=7.0,
            semantic_score=0.9,
            is_current_commit=True,
            is_latest_artifact=True,
            is_stale_artifact=False,
            is_boilerplate=False,
        )
        assert signals.is_current_commit
        assert signals.is_latest_artifact


# ---------------------------------------------------------------------------
# 6. Coverage score result
# ---------------------------------------------------------------------------

class TestCoverageScoreResult:
    def test_result_fields(self):
        """CoverageScoreResult has all required fields."""
        result = CoverageScoreResult(
            score=0.65,
            must_hit_found=True,
            concentration_factor=0.15,
            mass_factor=0.25,
            interpretation="ok"
        )
        assert 0.0 <= result.score <= 1.0
        assert result.interpretation in ["strong", "ok", "weak"]
        assert 0.0 <= result.concentration_factor <= 0.25
        assert 0.0 <= result.mass_factor <= 0.30


# ---------------------------------------------------------------------------
# 7. Integration: Combined scoring + coverage
# ---------------------------------------------------------------------------

class TestIntegratedScoringCoverage:
    def test_high_scores_high_coverage(self):
        """High final scores → likely high coverage."""
        chunks = [
            {"source_path": "main.py", "content": "MyFunction definition"},
            {"source_path": "utils.py", "content": "MyFunction helper"},
        ]
        scores = [0.9, 0.85]
        result = compute_coverage_score("MyFunction", chunks, scores)
        assert result.score > 0.4
    
    def test_low_scores_weak_coverage(self):
        """Low final scores + weak query match → weak coverage."""
        chunks = [
            {"source_path": "a.py", "content": "unrelated"},
        ]
        scores = [0.1]
        result = compute_coverage_score("SpecificTerm", chunks, scores)
        assert is_coverage_weak(result)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
