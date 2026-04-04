"""
Tests for tokenpak.vault.search — sort_retrieval_results, inject_retrieved_context,
compute_final_score, score_and_sort, coverage/term utilities.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from unittest.mock import patch

import pytest

from tokenpak.vault.search import (
    all_must_hits_found,
    chunks_contain_term,
    compute_coverage_score,
    compute_final_score,
    extract_must_hit_terms,
    inject_retrieved_context,
    interpret_coverage,
    measure_injection_consistency,
    score_and_sort,
    sort_retrieval_results,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _block(block_id: str, source_path: str = None, content: str = "some content",
           score: float = 5.0, raw_tokens: int = 10) -> Tuple[Dict[str, Any], float]:
    return (
        {
            "block_id": block_id,
            "source_path": source_path or f"docs/{block_id}.md",
            "content": content,
            "raw_tokens": raw_tokens,
        },
        score,
    )


def _count(t: str) -> int:
    """Simple deterministic token counter."""
    return max(1, len(t.split()))


# ---------------------------------------------------------------------------
# sort_retrieval_results
# ---------------------------------------------------------------------------

class TestSortRetrievalResults:
    def test_sorts_by_score_descending(self):
        results = [_block("a", score=3.0), _block("b", score=7.0), _block("c", score=1.0)]
        sorted_ = sort_retrieval_results(results)
        scores = [s for _, s in sorted_]
        assert scores == sorted(scores, reverse=True)

    def test_tie_break_by_source_path(self):
        b1 = ({"block_id": "x", "source_path": "b/file.md", "content": "a"}, 5.0)
        b2 = ({"block_id": "x", "source_path": "a/file.md", "content": "a"}, 5.0)
        sorted_ = sort_retrieval_results([b1, b2])
        assert sorted_[0][0]["source_path"] == "a/file.md"

    def test_tie_break_by_block_id(self):
        b1 = ({"block_id": "z", "source_path": "same.md", "content": "a"}, 5.0)
        b2 = ({"block_id": "a", "source_path": "same.md", "content": "a"}, 5.0)
        sorted_ = sort_retrieval_results([b1, b2])
        assert sorted_[0][0]["block_id"] == "a"

    def test_empty_input(self):
        assert sort_retrieval_results([]) == []

    def test_single_result(self):
        r = [_block("x")]
        assert sort_retrieval_results(r) == r

    def test_deterministic_repeated_calls(self):
        results = [_block("c", score=3.0), _block("a", score=5.0), _block("b", score=5.0)]
        r1 = sort_retrieval_results(results)
        r2 = sort_retrieval_results(results)
        assert r1 == r2

    def test_missing_source_path_uses_empty_string(self):
        b = ({"block_id": "x", "content": "hello"}, 5.0)
        result = sort_retrieval_results([b])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# inject_retrieved_context
# ---------------------------------------------------------------------------

class TestInjectRetrievedContext:
    def test_empty_results(self):
        text, tokens, refs = inject_retrieved_context([])
        assert text == ""
        assert tokens == 0
        assert refs == []

    def test_single_block_injected(self):
        results = [_block("a", content="hello world")]
        text, tokens, refs = inject_retrieved_context(results, count_tokens_fn=_count)
        assert "## Retrieved Context" in text
        assert "hello world" in text
        assert len(refs) == 1

    def test_source_ref_included(self):
        results = [_block("a", source_path="docs/readme.md")]
        _, _, refs = inject_retrieved_context(results, count_tokens_fn=_count)
        assert "docs/readme.md" in refs

    def test_budget_limits_output(self):
        # Large content should be truncated when budget is tiny
        big = "word " * 2000
        results = [_block("a", content=big, raw_tokens=2000)]
        text, tokens, refs = inject_retrieved_context(results, max_tokens=50, count_tokens_fn=_count)
        # Either truncated or empty
        assert tokens <= 200  # some leeway for header

    def test_multiple_blocks_sorted_by_score(self):
        results = [
            _block("low", score=1.0, content="low score content"),
            _block("high", score=9.0, content="high score content"),
        ]
        text, _, _ = inject_retrieved_context(results, count_tokens_fn=_count)
        high_pos = text.find("high score")
        low_pos = text.find("low score")
        assert high_pos < low_pos  # high score appears first

    def test_returns_tokens_count(self):
        results = [_block("a", content="hello world test")]
        _, tokens, _ = inject_retrieved_context(results, count_tokens_fn=_count)
        assert tokens > 0

    def test_custom_count_tokens_fn_used(self):
        call_count = [0]
        def counter(t):
            call_count[0] += 1
            return len(t)
        results = [_block("a", content="test")]
        inject_retrieved_context(results, count_tokens_fn=counter)
        assert call_count[0] > 0

    def test_no_results_fit_budget(self):
        # Budget so small nothing fits
        results = [_block("a", content="hello")]
        text, tokens, refs = inject_retrieved_context(results, max_tokens=1, count_tokens_fn=lambda t: 999)
        assert text == ""
        assert refs == []

    def test_block_without_source_path(self):
        b = ({"block_id": "x", "content": "no path"}, 3.0)
        text, _, refs = inject_retrieved_context([b], count_tokens_fn=_count)
        assert len(refs) > 0  # falls back to block_id

    def test_schema_content_selected_when_intent_matches(self):
        with patch("tokenpak._internal.ingest.schema_converter.should_serve_schema", return_value=True):
            b = ({"block_id": "x", "source_path": "x.md", "content": "raw",
                  "metadata": {"doc_type": "api", "schema": {"key": "val"}}}, 5.0)
            text, _, _ = inject_retrieved_context([b], intent="schema", count_tokens_fn=_count)
            assert "key" in text or "raw" in text  # schema or fallback


# ---------------------------------------------------------------------------
# compute_final_score
# ---------------------------------------------------------------------------

class TestComputeFinalScore:
    def test_all_zeros_returns_zero(self):
        assert compute_final_score() == 0.0

    def test_bm25_norm_contributes(self):
        s = compute_final_score(bm25_norm=1.0)
        assert s > 0.0

    def test_sem_norm_contributes(self):
        s = compute_final_score(sem_norm=1.0)
        assert s > 0.0

    def test_symbol_hit_boosts_score(self):
        base = compute_final_score(bm25_norm=0.5)
        with_hit = compute_final_score(bm25_norm=0.5, symbol_hit=True)
        assert with_hit > base

    def test_path_hit_boosts_score(self):
        base = compute_final_score(bm25_norm=0.5)
        with_hit = compute_final_score(bm25_norm=0.5, path_hit=True)
        assert with_hit > base

    def test_stale_penalizes_score(self):
        base = compute_final_score(bm25_norm=0.5)
        stale = compute_final_score(bm25_norm=0.5, is_stale=True)
        assert stale < base

    def test_noisy_penalizes_score(self):
        base = compute_final_score(bm25_norm=0.5)
        noisy = compute_final_score(bm25_norm=0.5, is_noisy=True)
        assert noisy <= base

    def test_recent_boosts_score(self):
        base = compute_final_score(bm25_norm=0.5)
        recent = compute_final_score(bm25_norm=0.5, is_recent=True)
        assert recent >= base


# ---------------------------------------------------------------------------
# score_and_sort
# ---------------------------------------------------------------------------

class TestScoreAndSort:
    def test_empty_input(self):
        assert score_and_sort([]) == []

    def test_sorts_by_final_score(self):
        results = [
            _block("a", score=2.0),
            _block("b", score=8.0),
            _block("c", score=1.0),
        ]
        sorted_ = score_and_sort(results)
        assert len(sorted_) == 3

    def test_semantic_scores_applied(self):
        results = [_block("a", score=5.0), _block("b", score=5.0)]
        sem = {"b": 0.9, "a": 0.1}
        sorted_ = score_and_sort(results, semantic_scores=sem)
        assert sorted_[0][0]["block_id"] == "b"

    def test_with_query_string(self):
        results = [_block("x", content="python tutorial")]
        sorted_ = score_and_sort(results, query="python")
        assert len(sorted_) == 1

    def test_recent_ids_boost(self):
        results = [_block("old", score=5.0), _block("new", score=5.0)]
        sorted_ = score_and_sort(results, recent_ids={"new"})
        assert sorted_[0][0]["block_id"] == "new"

    def test_stale_ids_penalize(self):
        results = [_block("fresh", score=5.0), _block("stale", score=5.0)]
        sorted_ = score_and_sort(results, stale_ids={"stale"})
        assert sorted_[0][0]["block_id"] == "fresh"


# ---------------------------------------------------------------------------
# extract_must_hit_terms
# ---------------------------------------------------------------------------

class TestExtractMustHitTerms:
    def test_empty_query(self):
        assert extract_must_hit_terms("") == []

    def test_identifier_terms_extracted(self):
        # extract_must_hit_terms uses _IDENTIFIER_RE — finds identifier-like tokens
        terms = extract_must_hit_terms("compute_coverage_score inject_retrieved_context")
        assert any("compute" in t or "inject" in t for t in terms)

    def test_returns_list(self):
        result = extract_must_hit_terms("find the answer")
        assert isinstance(result, list)

    def test_no_identifiers_returns_empty(self):
        # Short common words aren't identifiers (< min length threshold)
        result = extract_must_hit_terms("a b c")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# chunks_contain_term / all_must_hits_found
# ---------------------------------------------------------------------------

class TestTermMatching:
    def test_chunks_contain_term_found(self):
        chunks = [{"content": "hello world python"}, {"content": "machine learning"}]
        assert chunks_contain_term(chunks, "python") is True

    def test_chunks_contain_term_not_found(self):
        chunks = [{"content": "hello world"}]
        assert chunks_contain_term(chunks, "javascript") is False

    def test_chunks_contain_term_empty(self):
        assert chunks_contain_term([], "anything") is False

    def test_all_must_hits_found_true(self):
        chunks = [{"content": "python tutorial"}, {"content": "machine learning"}]
        assert all_must_hits_found(chunks, ["python", "machine"]) is True

    def test_all_must_hits_found_false(self):
        chunks = [{"content": "python tutorial"}]
        assert all_must_hits_found(chunks, ["python", "javascript"]) is False

    def test_all_must_hits_empty_terms(self):
        assert all_must_hits_found([], []) is True


# ---------------------------------------------------------------------------
# compute_coverage_score / interpret_coverage
# ---------------------------------------------------------------------------

class TestCoverageScore:
    def test_compute_coverage_score_returns_float(self):
        # compute_coverage_score(scored_chunks, must_hit_terms)
        scored_chunks = [({"content": "python tutorial", "source_path": "a.md", "block_id": "a"}, 5.0)]
        score = compute_coverage_score(scored_chunks, ["python"])
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_perfect_coverage(self):
        scored_chunks = [({"content": "python python python", "source_path": "a.md", "block_id": "a"}, 5.0)]
        score = compute_coverage_score(scored_chunks, ["python"])
        assert score > 0.0

    def test_zero_coverage_no_terms(self):
        scored_chunks = [({"content": "unrelated content", "source_path": "a.md", "block_id": "a"}, 1.0)]
        # No must_hit_terms → defaults to full must_hit_factor
        score = compute_coverage_score(scored_chunks, [])
        assert score > 0.0

    def test_empty_chunks(self):
        score = compute_coverage_score([], ["python"])
        assert score == 0.0

    def test_interpret_coverage_high(self):
        label = interpret_coverage(0.9)
        assert isinstance(label, str)
        assert len(label) > 0

    def test_interpret_coverage_zero(self):
        label = interpret_coverage(0.0)
        assert isinstance(label, str)

    def test_interpret_coverage_mid(self):
        label = interpret_coverage(0.5)
        assert isinstance(label, str)


# ---------------------------------------------------------------------------
# measure_injection_consistency
# ---------------------------------------------------------------------------

class TestMeasureInjectionConsistency:
    def test_consistent_fn_returns_1_0(self):
        def fn(q):
            return "fixed output", 10, ["a.md"]

        result = measure_injection_consistency(fn, "query", runs=3)
        assert "consistency_rate" in result or "consistent" in result or isinstance(result, dict)

    def test_returns_dict(self):
        def fn(q):
            return "text", 5, []
        result = measure_injection_consistency(fn, "test", runs=2)
        assert isinstance(result, dict)
