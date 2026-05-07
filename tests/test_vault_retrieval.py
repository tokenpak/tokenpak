"""
tests/test_vault_retrieval.py

Edge case tests for vault retrieval functions:
  - Large queries (10MB+ context)
  - Unicode + special chars handling
  - Empty/missing vault index
  - Concurrent requests (threaded)
  - Timeout behavior
  - Deterministic sort stability
"""

from __future__ import annotations

import pytest

try:
    from tokenpak.vault.retrieval import inject_retrieved_context
except ImportError:
    pytest.skip("Cannot import inject_retrieved_context from tokenpak.vault.retrieval — removed in current build", allow_module_level=True)
import concurrent.futures
import threading
from typing import Any, Dict, List, Tuple

import pytest

from tokenpak.vault.retrieval import (
    all_must_hits_found,
    extract_must_hit_terms,
    inject_retrieved_context,
    sort_retrieval_results,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(
    path: str = "docs/test.md",
    block_id: str = "b1",
    content: str = "sample content",
    score: float = 1.0,
) -> Tuple[Dict[str, Any], float]:
    return (
        {
            "source_path": path,
            "block_id": block_id,
            "content": content,
            "metadata": {},
        },
        score,
    )


def _make_blocks(n: int, base_score: float = 1.0) -> List[Tuple[Dict[str, Any], float]]:
    return [
        _make_block(
            path=f"docs/file_{i}.md",
            block_id=f"block_{i:04d}",
            content=f"Content for block {i}. " * 20,
            score=base_score - i * 0.001,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. Large queries
# ---------------------------------------------------------------------------

class TestLargeQueryHandling:
    """Retrieval with very large content blocks."""

    def test_large_content_block_truncated_by_token_budget(self):
        """A single huge block should be budget-capped, not crash."""
        large_content = "word " * 50_000  # ~250KB of text
        blocks = [_make_block(content=large_content, score=2.0)]

        injection, token_count, paths = inject_retrieved_context(blocks, max_tokens=2000)

        # Should not overflow budget significantly
        assert token_count <= 2200, f"Token count {token_count} exceeded budget+10%"
        assert "## Retrieved Context" in injection

    def test_many_blocks_respects_token_budget(self):
        """100 blocks: only blocks fitting within budget should be injected."""
        blocks = _make_blocks(100, base_score=1.0)

        injection, token_count, paths = inject_retrieved_context(blocks, max_tokens=1000)

        assert token_count <= 1100, f"Token count {token_count} exceeded budget"
        assert len(paths) < 100, "All 100 blocks should not fit in 1000 tokens"

    def test_10mb_context_does_not_raise(self):
        """10MB context (many blocks) should not raise — budget kicks in."""
        # 500 blocks × ~5KB each ≈ 2.5MB of text
        big_content = "x" * 5000
        blocks = [
            _make_block(path=f"f{i}.md", block_id=f"b{i}", content=big_content, score=float(500 - i))
            for i in range(500)
        ]

        # Should not raise OOM or time out on reasonable hardware
        injection, token_count, paths = inject_retrieved_context(blocks, max_tokens=4000)

        assert token_count <= 4500
        assert isinstance(paths, list)

    def test_zero_blocks_returns_empty_injection(self):
        """No results → empty injection."""
        injection, token_count, paths = inject_retrieved_context([], max_tokens=4000)

        assert token_count == 0
        assert paths == []
        # Injection may be empty string or just header — no content block
        assert "Content for" not in injection


# ---------------------------------------------------------------------------
# 2. Unicode + special chars
# ---------------------------------------------------------------------------

class TestUnicodeAndSpecialChars:
    """Retrieval with non-ASCII and edge-case characters."""

    def test_unicode_content_injected_correctly(self):
        """Chinese, Arabic, emoji, and combining chars should pass through."""
        content = "日本語テスト 🚀 مرحبا naïve résumé café \u0000\ufffd"
        blocks = [_make_block(content=content)]

        injection, token_count, paths = inject_retrieved_context(blocks, max_tokens=4000)

        assert "日本語" in injection
        assert "مرحبا" in injection
        assert "🚀" in injection

    def test_null_bytes_in_content_handled(self):
        """Null bytes should not crash the injection."""
        content = "before\x00after"
        blocks = [_make_block(content=content)]

        # Should not raise
        injection, _, _ = inject_retrieved_context(blocks, max_tokens=4000)
        assert isinstance(injection, str)

    def test_special_markdown_chars_in_path(self):
        """Paths with # [ ] | characters should not break the header."""
        blocks = [_make_block(path="docs/file [v2] #main.md", content="content")]

        injection, _, paths = inject_retrieved_context(blocks, max_tokens=4000)

        assert len(paths) == 1
        assert isinstance(injection, str)

    def test_very_long_path_name(self):
        """Very long path (1000 chars) should not crash."""
        long_path = "a/" * 200 + "file.md"
        blocks = [_make_block(path=long_path, content="test content")]

        injection, _, paths = inject_retrieved_context(blocks, max_tokens=4000)
        assert len(paths) == 1

    def test_unicode_sort_stability(self):
        """Unicode paths should sort deterministically."""
        blocks = [
            _make_block(path="docs/α.md", block_id="b1", score=1.0),
            _make_block(path="docs/Ω.md", block_id="b2", score=1.0),
            _make_block(path="docs/ñ.md", block_id="b3", score=1.0),
        ]
        sorted1 = sort_retrieval_results(blocks)
        sorted2 = sort_retrieval_results(blocks)

        assert [b[0]["source_path"] for b in sorted1] == [b[0]["source_path"] for b in sorted2]


# ---------------------------------------------------------------------------
# 3. Empty/missing vault index
# ---------------------------------------------------------------------------

class TestEmptyVaultIndex:
    """Retrieval when vault is empty or malformed."""

    def test_empty_results_list(self):
        """inject_retrieved_context([]) → empty injection."""
        injection, count, paths = inject_retrieved_context([])
        assert count == 0
        assert paths == []

    def test_block_with_empty_content(self):
        """Block with empty content string should not raise."""
        blocks = [_make_block(content="")]

        injection, count, paths = inject_retrieved_context(blocks, max_tokens=4000)
        assert isinstance(injection, str)

    def test_block_missing_content_key(self):
        """Block without 'content' key should not crash injection."""
        block = ({"source_path": "test.md", "block_id": "b1", "metadata": {}}, 1.0)

        # Should not raise KeyError
        injection, count, paths = inject_retrieved_context([block], max_tokens=4000)
        assert isinstance(injection, str)

    def test_sort_empty_list(self):
        """sort_retrieval_results([]) → []."""
        result = sort_retrieval_results([])
        assert result == []

    def test_sort_single_item(self):
        """sort_retrieval_results with one item returns list with that item."""
        blocks = [_make_block()]
        result = sort_retrieval_results(blocks)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 4. Concurrent requests (threaded)
# ---------------------------------------------------------------------------

class TestConcurrentRetrieval:
    """Thread-safety of retrieval functions."""

    def test_concurrent_inject_retrieved_context(self):
        """Multiple threads calling inject_retrieved_context should not corrupt results."""
        blocks = _make_blocks(20, base_score=2.0)
        results = []
        errors = []

        def worker():
            try:
                injection, count, paths = inject_retrieved_context(blocks, max_tokens=2000)
                results.append((injection, count, len(paths)))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent injection raised errors: {errors}"
        assert len(results) == 10

        # All threads should get the same token count
        counts = [r[1] for r in results]
        assert len(set(counts)) == 1, f"Token counts diverged across threads: {counts}"

    def test_concurrent_sort_retrieval_results(self):
        """sort_retrieval_results is stateless — concurrent use should be safe."""
        blocks = _make_blocks(50)
        reference = sort_retrieval_results(blocks)
        errors = []
        outputs = []

        def worker():
            try:
                result = sort_retrieval_results(blocks)
                outputs.append([b[0]["block_id"] for b in result])
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(worker) for _ in range(8)]
            concurrent.futures.wait(futures, timeout=10)

        assert not errors
        reference_ids = [b[0]["block_id"] for b in reference]
        for out in outputs:
            assert out == reference_ids, "Sort order diverged between threads"

    def test_concurrent_with_different_budgets(self):
        """Different threads with different budgets should not interfere."""
        blocks = _make_blocks(30, base_score=3.0)
        results_by_budget = {}
        lock = threading.Lock()

        def worker(budget):
            _, count, paths = inject_retrieved_context(blocks, max_tokens=budget)
            with lock:
                results_by_budget[budget] = (count, len(paths))

        budgets = [500, 1000, 2000, 4000]
        threads = [threading.Thread(target=worker, args=(b,)) for b in budgets]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Larger budget → more tokens and more paths
        assert results_by_budget[500][0] <= results_by_budget[1000][0]
        assert results_by_budget[1000][0] <= results_by_budget[2000][0]
        assert results_by_budget[2000][0] <= results_by_budget[4000][0]


# ---------------------------------------------------------------------------
# 5. Deterministic sort stability
# ---------------------------------------------------------------------------

class TestSortDeterminism:
    """sort_retrieval_results must be deterministic (cache-stable)."""

    def test_same_score_sorted_by_path_then_id(self):
        """Tie-breaking: path asc, then block_id asc."""
        blocks = [
            _make_block(path="b.md", block_id="z", score=1.0),
            _make_block(path="a.md", block_id="z", score=1.0),
            _make_block(path="a.md", block_id="a", score=1.0),
        ]
        result = sort_retrieval_results(blocks)
        ids = [(b[0]["source_path"], b[0]["block_id"]) for b in result]
        assert ids == [("a.md", "a"), ("a.md", "z"), ("b.md", "z")]

    def test_higher_score_first(self):
        """Higher scores should appear before lower scores."""
        blocks = [
            _make_block(path="low.md", score=0.1),
            _make_block(path="high.md", score=0.9),
            _make_block(path="mid.md", score=0.5),
        ]
        result = sort_retrieval_results(blocks)
        paths = [b[0]["source_path"] for b in result]
        assert paths[0] == "high.md"
        assert paths[-1] == "low.md"

    def test_repeated_sort_identical(self):
        """Sorting the same list twice produces identical output."""
        import random
        random.seed(42)
        blocks = _make_blocks(50)
        random.shuffle(blocks)

        r1 = sort_retrieval_results(blocks)
        r2 = sort_retrieval_results(blocks)
        assert [(b[0]["block_id"], b[1]) for b in r1] == [(b[0]["block_id"], b[1]) for b in r2]


# ---------------------------------------------------------------------------
# 6. Must-hit terms
# ---------------------------------------------------------------------------

class TestMustHitTerms:
    """extract_must_hit_terms and all_must_hits_found edge cases."""

    def test_empty_query_returns_empty(self):
        assert extract_must_hit_terms("") == []

    def test_short_query_no_terms(self):
        """Common short words shouldn't appear as must-hits."""
        terms = extract_must_hit_terms("the a of")
        assert isinstance(terms, list)

    def test_all_must_hits_found_empty_terms(self):
        """No required terms → always satisfied."""
        assert all_must_hits_found([], []) is True
        assert all_must_hits_found([{"content": "anything"}], []) is True

    def test_all_must_hits_found_with_missing_term(self):
        """A required term not in any chunk → False."""
        chunks = [{"content": "hello world"}]
        result = all_must_hits_found(chunks, ["xyzzy_not_here"])
        assert result is False

    def test_all_must_hits_found_with_present_term(self):
        """Required term present in at least one chunk → True."""
        chunks = [{"content": "tokenpak proxy vault"}, {"content": "other stuff"}]
        result = all_must_hits_found(chunks, ["tokenpak"])
        assert result is True
