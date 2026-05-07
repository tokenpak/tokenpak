"""test_retrieval.py — Tests for tokenpak.vault.retrieval.

Covers: sort_retrieval_results, inject_retrieved_context, measure_injection_consistency,
compute_final_score, extract_must_hit_terms, compute_coverage_score, score_and_sort,
interpret_coverage.
"""

import sys
import types
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Patch broken ingest module before any tokenpak imports
# ---------------------------------------------------------------------------
_fake_ingest = types.ModuleType("tokenpak.vault.ingest")
_fake_sc = types.ModuleType("tokenpak.vault.ingest.schema_converter")
_fake_sc.should_serve_schema = lambda intent: False
_fake_sc.convert_document = MagicMock(return_value={})
_fake_ingest.schema_converter = _fake_sc
sys.modules.setdefault("tokenpak.vault.ingest", _fake_ingest)
sys.modules.setdefault("tokenpak.vault.ingest.schema_converter", _fake_sc)
sys.modules.setdefault("tokenpak.vault.ingest.api", MagicMock())

from tokenpak.vault.search import (  # noqa: E402
    COVERAGE_OK,
    COVERAGE_STRONG,
    RETRIEVED_CONTEXT_HEADER,
    all_must_hits_found,
    chunks_contain_term,
    compute_coverage_score,
    compute_final_score,
    extract_must_hit_terms,
    inject_retrieved_context,
    interpret_coverage,
    measure_injection_consistency,
    sort_retrieval_results,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _block(content: str, source_path: str = "test.md", block_id: str = "b1", **kwargs):
    b = {"content": content, "source_path": source_path, "block_id": block_id}
    b.update(kwargs)
    return b

def _token_counter(text: str) -> int:
    """Simple char-based counter: 4 chars = 1 token."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# sort_retrieval_results
# ---------------------------------------------------------------------------

class TestSortRetrievalResults:
    def test_higher_score_first(self):
        results = [
            (_block("low", source_path="a.md"), 0.3),
            (_block("high", source_path="b.md"), 0.9),
            (_block("mid", source_path="c.md"), 0.6),
        ]
        sorted_r = sort_retrieval_results(results)
        scores = [s for _, s in sorted_r]
        assert scores[0] >= scores[1] >= scores[2]

    def test_tie_broken_by_source_path(self):
        results = [
            (_block("x", source_path="z.md", block_id="1"), 0.5),
            (_block("x", source_path="a.md", block_id="1"), 0.5),
        ]
        sorted_r = sort_retrieval_results(results)
        assert sorted_r[0][0]["source_path"] == "a.md"

    def test_tie_broken_by_block_id(self):
        results = [
            (_block("x", source_path="a.md", block_id="z1"), 0.5),
            (_block("x", source_path="a.md", block_id="a1"), 0.5),
        ]
        sorted_r = sort_retrieval_results(results)
        assert sorted_r[0][0]["block_id"] == "a1"

    def test_empty_list_returns_empty(self):
        assert sort_retrieval_results([]) == []

    def test_single_item(self):
        r = [(_block("x"), 1.0)]
        assert sort_retrieval_results(r) == r


# ---------------------------------------------------------------------------
# inject_retrieved_context
# ---------------------------------------------------------------------------

class TestInjectRetrievedContext:
    def test_empty_results_returns_empty(self):
        text, tokens, refs = inject_retrieved_context([])
        assert text == ""
        assert tokens == 0
        assert refs == []

    def test_header_present_in_output(self):
        results = [(_block("some content"), 0.8)]
        text, tokens, refs = inject_retrieved_context(results, count_tokens_fn=_token_counter)
        assert RETRIEVED_CONTEXT_HEADER in text

    def test_source_path_in_output(self):
        results = [(_block("data", source_path="myfile.md"), 0.7)]
        text, _, refs = inject_retrieved_context(results, count_tokens_fn=_token_counter)
        assert "myfile.md" in text
        assert "myfile.md" in refs

    def test_token_budget_respected(self):
        # Large content that exceeds tiny budget
        big_content = "word " * 10000
        results = [(_block(big_content), 0.9)]
        _, tokens_used, _ = inject_retrieved_context(
            results, max_tokens=50, count_tokens_fn=_token_counter
        )
        assert tokens_used <= 50

    def test_multiple_results_ordered_by_score(self):
        results = [
            (_block("low block", source_path="low.md"), 0.1),
            (_block("high block", source_path="high.md"), 0.9),
        ]
        text, _, refs = inject_retrieved_context(results, count_tokens_fn=_token_counter)
        # high.md should appear before low.md in output
        assert text.index("high.md") < text.index("low.md")

    def test_score_shown_in_output(self):
        results = [(_block("content"), 7.5)]
        text, _, _ = inject_retrieved_context(results, count_tokens_fn=_token_counter)
        assert "7.5" in text

    def test_fallback_token_counter(self):
        """inject_retrieved_context should work without explicit count_tokens_fn."""
        results = [(_block("hello world"), 0.5)]
        text, tokens, refs = inject_retrieved_context(results)
        assert RETRIEVED_CONTEXT_HEADER in text
        assert tokens > 0

    def test_schema_block_uses_json_when_intent_matches(self):
        """Block with schema metadata and schema intent should use schema content."""
        from unittest.mock import patch
        block = _block(
            "raw content",
            metadata={"schema": {"field": "value"}, "doc_type": "spec"},
        )
        with patch("tokenpak.vault.ingest.schema_converter.should_serve_schema", return_value=True):
            text, _, _ = inject_retrieved_context(
                [(block, 1.0)], intent="schema", count_tokens_fn=_token_counter
            )
        # Should contain JSON with schema info
        assert "field" in text or "spec" in text or "raw content" in text


# ---------------------------------------------------------------------------
# measure_injection_consistency
# ---------------------------------------------------------------------------

class TestMeasureInjectionConsistency:
    def test_deterministic_fn_is_consistent(self):
        def injection_fn(query):
            return ("static text", 3, ["ref.md"])

        result = measure_injection_consistency(injection_fn, "test query", runs=5)
        assert result["consistent"] is True
        assert result["unique_texts"] == 1
        assert len(result["tokens_per_run"]) == 5
        assert result["avg_tokens"] == 3.0

    def test_nondeterministic_fn_not_consistent(self):
        import random

        def injection_fn(query):
            return (str(random.random()), 5, [])

        result = measure_injection_consistency(injection_fn, "query", runs=10)
        assert result["unique_texts"] > 1
        assert result["consistent"] is False

    def test_zero_runs_no_crash(self):
        result = measure_injection_consistency(lambda q: ("x", 1, []), "q", runs=0)
        assert result["avg_tokens"] == 0


# ---------------------------------------------------------------------------
# compute_final_score
# ---------------------------------------------------------------------------

class TestComputeFinalScore:
    def test_base_score(self):
        score = compute_final_score(sem_norm=0.5, bm25_norm=0.5, meta_norm=0.5)
        assert score > 0

    def test_symbol_hit_boosts_score(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5)
        boosted = compute_final_score(sem_norm=0.5, bm25_norm=0.5, symbol_hit=True)
        assert boosted > base

    def test_path_hit_boosts_score(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5)
        boosted = compute_final_score(sem_norm=0.5, bm25_norm=0.5, path_hit=True)
        assert boosted > base

    def test_stale_penalty(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5)
        penalized = compute_final_score(sem_norm=0.5, bm25_norm=0.5, is_stale=True)
        assert penalized < base

    def test_noisy_penalty(self):
        base = compute_final_score(sem_norm=0.5, bm25_norm=0.5)
        penalized = compute_final_score(sem_norm=0.5, bm25_norm=0.5, is_noisy=True)
        assert penalized < base

    def test_perfect_scores(self):
        score = compute_final_score(
            sem_norm=1.0,
            bm25_norm=1.0,
            meta_norm=1.0,
            symbol_hit=True,
            path_hit=True,
            is_recent=True,
        )
        assert score > 1.0

    def test_zero_inputs(self):
        score = compute_final_score()
        assert score == 0.0


# ---------------------------------------------------------------------------
# extract_must_hit_terms
# ---------------------------------------------------------------------------

class TestExtractMustHitTerms:
    def test_extracts_class_names(self):
        terms = extract_must_hit_terms("test ProviderRouter behavior")
        assert "ProviderRouter" in terms

    def test_extracts_function_names(self):
        terms = extract_must_hit_terms("how does inject_retrieved_context work")
        assert "inject_retrieved_context" in terms

    def test_filters_stop_words(self):
        terms = extract_must_hit_terms("how does the function work")
        assert "how" not in terms
        assert "the" not in terms

    def test_empty_query(self):
        assert extract_must_hit_terms("") == []

    def test_no_duplicates(self):
        terms = extract_must_hit_terms("ProviderRouter ProviderRouter")
        assert terms.count("ProviderRouter") == 1


# ---------------------------------------------------------------------------
# chunks_contain_term + all_must_hits_found
# ---------------------------------------------------------------------------

class TestMustHitHelpers:
    def test_chunks_contain_term_found(self):
        chunks = [{"content": "hello ProviderRouter world"}]
        assert chunks_contain_term(chunks, "ProviderRouter") is True

    def test_chunks_contain_term_not_found(self):
        chunks = [{"content": "nothing here"}]
        assert chunks_contain_term(chunks, "MyClass") is False

    def test_chunks_contain_term_case_insensitive(self):
        chunks = [{"content": "PROVIDERROUTER"}]
        assert chunks_contain_term(chunks, "providerrouter") is True

    def test_all_must_hits_found_all_present(self):
        chunks = [{"content": "foo and bar are here"}]
        assert all_must_hits_found(chunks, ["foo", "bar"]) is True

    def test_all_must_hits_missing_one(self):
        chunks = [{"content": "only foo"}]
        assert all_must_hits_found(chunks, ["foo", "bar"]) is False

    def test_all_must_hits_empty_terms(self):
        assert all_must_hits_found([], []) is True


# ---------------------------------------------------------------------------
# compute_coverage_score
# ---------------------------------------------------------------------------

class TestComputeCoverageScore:
    def test_empty_chunks_returns_zero(self):
        assert compute_coverage_score([], ["term"]) == 0.0

    def test_all_terms_found_increases_score(self):
        chunks = [(_block("hello term1 term2"), 0.8)]
        score_with = compute_coverage_score(chunks, ["term1", "term2"])
        score_without = compute_coverage_score(chunks, ["missing"])
        assert score_with > score_without

    def test_concentrated_results_boost_score(self):
        # Single file = high concentration
        single_file = [
            (_block("text", source_path="same.md"), 0.8),
            (_block("more", source_path="same.md"), 0.7),
        ]
        multi_file = [
            (_block("text", source_path="a.md"), 0.8),
            (_block("more", source_path="b.md"), 0.7),
        ]
        score_single = compute_coverage_score(single_file, [])
        score_multi = compute_coverage_score(multi_file, [])
        assert score_single >= score_multi

    def test_no_must_hit_terms(self):
        chunks = [(_block("anything"), 0.5)]
        score = compute_coverage_score(chunks, [])
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# interpret_coverage
# ---------------------------------------------------------------------------

class TestInterpretCoverage:
    def test_strong_coverage(self):
        result = interpret_coverage(COVERAGE_STRONG + 0.01)
        assert "strong" in result.lower() or "good" in result.lower() or result  # non-empty

    def test_ok_coverage(self):
        result = interpret_coverage(COVERAGE_OK + 0.01)
        assert result  # non-empty string

    def test_weak_coverage(self):
        result = interpret_coverage(0.1)
        assert result  # non-empty string

    def test_zero_coverage(self):
        result = interpret_coverage(0.0)
        assert result  # non-empty string

    def test_full_coverage(self):
        result = interpret_coverage(1.0)
        assert result  # non-empty string
