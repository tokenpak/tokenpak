"""Tests for multi-signal retrieval scoring + coverage score."""

from __future__ import annotations

import pytest

from tokenpak.agent.vault.retrieval import (
    COVERAGE_OK,
    COVERAGE_STRONG,
    _BOOST_PATH,
    _BOOST_RECENCY,
    _BOOST_SYMBOL,
    _PENALTY_NOISE,
    _PENALTY_STALE,
    _W_BM25,
    _W_META,
    _W_SEM,
    all_must_hits_found,
    chunks_contain_term,
    compute_coverage_score,
    compute_final_score,
    extract_must_hit_terms,
    interpret_coverage,
    score_and_sort,
    sort_retrieval_results,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _block(content: str, source: str = "file.py", block_id: str = "b0") -> dict:
    return {"content": content, "source_path": source, "block_id": block_id}


def _pair(content: str, score: float = 0.5, source: str = "file.py", block_id: str = "b0"):
    return (_block(content, source, block_id), score)


# ---------------------------------------------------------------------------
# 1. Scoring formula: correct values
# ---------------------------------------------------------------------------

class TestScoringFormula:
    def test_baseline_weights_sum(self):
        """Weights should sum to 1.0."""
        assert abs(_W_SEM + _W_BM25 + _W_META - 1.0) < 1e-9

    def test_no_boosts_no_penalties(self):
        score = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.5)
        expected = 0.45 * 0.5 + 0.45 * 0.5 + 0.10 * 0.5
        assert abs(score - expected) < 1e-9

    def test_perfect_signals_no_boosts(self):
        score = compute_final_score(sem_norm=1.0, bm25_norm=1.0, meta_norm=1.0)
        assert abs(score - 1.0) < 1e-9

    def test_zero_signals(self):
        score = compute_final_score(sem_norm=0.0, bm25_norm=0.0, meta_norm=0.0)
        assert score == 0.0

    def test_symbol_boost_applied(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0)
        boosted = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0, symbol_hit=True)
        assert abs(boosted - base - _BOOST_SYMBOL) < 1e-9

    def test_path_boost_applied(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0)
        boosted = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0, path_hit=True)
        assert abs(boosted - base - _BOOST_PATH) < 1e-9

    def test_recency_boost_applied(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0)
        boosted = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0, is_recent=True)
        assert abs(boosted - base - _BOOST_RECENCY) < 1e-9

    def test_stale_penalty_applied(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0)
        penalised = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0, is_stale=True)
        assert abs(base - penalised - _PENALTY_STALE) < 1e-9

    def test_noise_penalty_applied(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0)
        penalised = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.0, is_noisy=True)
        assert abs(base - penalised - _PENALTY_NOISE) < 1e-9

    def test_all_boosts(self):
        base = compute_final_score(sem_norm=0.6, bm25_norm=0.6, meta_norm=0.6)
        all_boosts = compute_final_score(
            sem_norm=0.6, bm25_norm=0.6, meta_norm=0.6,
            symbol_hit=True, path_hit=True, is_recent=True,
        )
        delta = _BOOST_SYMBOL + _BOOST_PATH + _BOOST_RECENCY
        assert abs(all_boosts - base - delta) < 1e-9

    def test_all_penalties(self):
        base = compute_final_score(sem_norm=0.6, bm25_norm=0.6, meta_norm=0.6)
        penalised = compute_final_score(
            sem_norm=0.6, bm25_norm=0.6, meta_norm=0.6,
            is_stale=True, is_noisy=True,
        )
        delta = _PENALTY_STALE + _PENALTY_NOISE
        assert abs(base - penalised - delta) < 1e-9


# ---------------------------------------------------------------------------
# 2. Coverage score ranges (strong / ok / weak)
# ---------------------------------------------------------------------------

class TestCoverageScoreRanges:
    def _make_strong_result(self) -> list:
        """All must-hits found, single file, high scores."""
        chunk = _block("def my_func(): pass\n# foo bar", source="core.py", block_id="b0")
        return [(chunk, 0.95)]

    def _make_weak_result(self) -> list:
        """No must-hit terms, many files, low scores."""
        return [
            (_block("some content", source=f"file_{i}.py", block_id=f"b{i}"), 0.1)
            for i in range(8)
        ]

    def test_strong_coverage(self):
        results = self._make_strong_result()
        cov = compute_coverage_score(results, must_hit_terms=["my_func"])
        assert cov >= COVERAGE_STRONG, f"Expected strong, got {cov:.3f}"

    def test_weak_coverage(self):
        results = self._make_weak_result()
        cov = compute_coverage_score(results, must_hit_terms=["nonexistent_identifier"])
        assert cov < COVERAGE_OK, f"Expected weak, got {cov:.3f}"

    def test_ok_coverage_in_range(self):
        # 2 files, moderate scores, partial must-hits
        chunks = [
            (_block("def fetch(): pass", source="a.py", block_id="a"), 0.6),
            (_block("class Loader: pass", source="b.py", block_id="b"), 0.5),
        ]
        cov = compute_coverage_score(chunks, must_hit_terms=["fetch"])
        # Should be in ok range (0.55-0.75) or strong
        assert COVERAGE_OK <= cov <= 1.0, f"Expected ok+, got {cov:.3f}"

    def test_coverage_in_0_1_range(self):
        for n in [0, 1, 5, 10]:
            chunks = [
                (_block("content", source=f"f{i}.py", block_id=f"b{i}"), 0.5)
                for i in range(n)
            ]
            cov = compute_coverage_score(chunks, must_hit_terms=["content"])
            assert 0.0 <= cov <= 1.0, f"Coverage out of range: {cov}"

    def test_empty_chunks_zero_coverage(self):
        cov = compute_coverage_score([], must_hit_terms=["func"])
        assert cov == 0.0

    def test_interpret_strong(self):
        assert interpret_coverage(0.80) == "strong"

    def test_interpret_ok(self):
        assert interpret_coverage(0.65) == "ok"

    def test_interpret_weak(self):
        assert interpret_coverage(0.40) == "weak"

    def test_interpret_boundary_strong(self):
        assert interpret_coverage(COVERAGE_STRONG) == "strong"

    def test_interpret_boundary_ok(self):
        assert interpret_coverage(COVERAGE_OK) == "ok"


# ---------------------------------------------------------------------------
# 3. Must-hit term extraction
# ---------------------------------------------------------------------------

class TestMustHitExtraction:
    def test_extracts_function_name(self):
        terms = extract_must_hit_terms("How does fetch_data work?")
        assert "fetch_data" in terms

    def test_extracts_class_name(self):
        terms = extract_must_hit_terms("Explain the DataLoader class")
        assert "DataLoader" in terms

    def test_extracts_error_code(self):
        terms = extract_must_hit_terms("Getting TypeError: invalid argument")
        assert "TypeError" in terms

    def test_no_stop_words(self):
        terms = extract_must_hit_terms("What is the value of this variable?")
        low = [t.lower() for t in terms]
        for sw in ("what", "is", "the", "of", "this"):
            assert sw not in low

    def test_unique_terms(self):
        terms = extract_must_hit_terms("fetch_data fetch_data fetch_data")
        assert len(terms) == len(set(terms))

    def test_empty_query(self):
        terms = extract_must_hit_terms("")
        assert terms == []

    def test_chunks_contain_term(self):
        chunks = [_block("def compute_score(x): pass")]
        assert chunks_contain_term(chunks, "compute_score")
        assert not chunks_contain_term(chunks, "unknown_func")

    def test_all_must_hits_found_true(self):
        chunks = [
            _block("def fetch_data(): pass"),
            _block("class DataLoader: pass"),
        ]
        assert all_must_hits_found(chunks, ["fetch_data", "DataLoader"])

    def test_all_must_hits_found_false(self):
        chunks = [_block("def unrelated(): pass")]
        assert not all_must_hits_found(chunks, ["fetch_data"])

    def test_all_must_hits_empty_terms(self):
        """Empty must-hit list → all found (vacuously true)."""
        chunks = [_block("anything")]
        assert all_must_hits_found(chunks, [])


# ---------------------------------------------------------------------------
# 4. Symbol boost applied correctly
# ---------------------------------------------------------------------------

class TestSymbolBoost:
    def test_symbol_boost_increases_score(self):
        query = "show me the render function"
        content_hit = "def render(template, context):\n    return template.format(**context)"
        content_miss = "class DataStore:\n    pass"

        results_hit = [(_block(content_hit, block_id="b0"), 0.5)]
        results_miss = [(_block(content_miss, block_id="b1"), 0.5)]

        scored_hit = score_and_sort(results_hit, query=query)
        scored_miss = score_and_sort(results_miss, query=query)

        score_hit = scored_hit[0][1]
        score_miss = scored_miss[0][1]
        assert score_hit > score_miss

    def test_path_boost_applied_via_score_and_sort(self):
        query = "in core.py what does setup do"
        results = [
            (_block("def setup(): pass", source="core.py", block_id="b0"), 0.5),
            (_block("def setup(): pass", source="other.py", block_id="b1"), 0.5),
        ]
        scored = score_and_sort(results, query=query)
        # core.py block should have higher score due to path boost
        block_ids = [b.get("block_id") for b, _ in scored]
        assert block_ids[0] == "b0"


# ---------------------------------------------------------------------------
# 5. Stale penalty applied correctly
# ---------------------------------------------------------------------------

class TestStalePenalty:
    def test_stale_chunk_scores_lower(self):
        results = [
            (_block("def process(): pass", block_id="fresh"), 0.8),
            (_block("def process(): pass", block_id="stale"), 0.8),
        ]
        scored = score_and_sort(results, stale_ids={"stale"})
        scores = {b.get("block_id"): s for b, s in scored}
        assert scores["fresh"] > scores["stale"]

    def test_recent_chunk_scores_higher(self):
        results = [
            (_block("content", block_id="old"), 0.7),
            (_block("content", block_id="new"), 0.7),
        ]
        scored = score_and_sort(results, recent_ids={"new"})
        scores = {b.get("block_id"): s for b, s in scored}
        assert scores["new"] > scores["old"]


# ---------------------------------------------------------------------------
# 6. Integration with sort_retrieval_results
# ---------------------------------------------------------------------------

class TestIntegrationWithSort:
    def test_score_and_sort_returns_sorted_desc(self):
        results = [
            (_block("low content", block_id="lo"), 0.2),
            (_block("high content", block_id="hi"), 0.9),
            (_block("mid content", block_id="mi"), 0.5),
        ]
        sorted_results = score_and_sort(results)
        scores = [s for _, s in sorted_results]
        assert scores == sorted(scores, reverse=True)

    def test_score_and_sort_empty_returns_empty(self):
        assert score_and_sort([]) == []

    def test_sort_retrieval_results_deterministic(self):
        """Existing sort_retrieval_results still works correctly."""
        results = [
            (_block("c", source="z.py", block_id="z"), 0.5),
            (_block("a", source="a.py", block_id="a"), 0.5),
            (_block("b", source="m.py", block_id="m"), 0.5),
        ]
        sorted_r = sort_retrieval_results(results)
        paths = [b.get("source_path") for b, _ in sorted_r]
        assert paths == ["a.py", "m.py", "z.py"]

    def test_score_and_sort_with_coverage(self):
        """End-to-end: score, sort, then compute coverage."""
        query = "fetch_data function"
        results = [
            (_block("def fetch_data(): pass", source="utils.py", block_id="u"), 0.8),
            (_block("class Config: pass", source="config.py", block_id="c"), 0.3),
        ]
        scored = score_and_sort(results, query=query)
        must_hits = extract_must_hit_terms(query)
        cov = compute_coverage_score(scored, must_hit_terms=must_hits)
        assert cov >= COVERAGE_OK


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
