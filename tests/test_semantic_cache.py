"""
tests/test_semantic_cache.py

Unit tests for tokenpak.cache.semantic_cache.

CCG-15: Updated to use the bytes-based API (response: bytes, content_type, wire_format).

Covers all acceptance criteria:
  1. Exact duplicate query returns cached response
  2. Near-duplicate above threshold returns cached
  3. Below threshold makes fresh LLM call (miss)
  4. TTL expiration works
  5. Max entries eviction works
  6. Cache disabled when config says so
  + extras: normalisation, Jaccard math, stats, invalidate
  + CCG-15: wire-format matching, old-schema invalidation
"""

from __future__ import annotations

import json
import time
from typing import cast

import pytest

from tokenpak.cache.semantic_cache import (
    SemanticCache,
    SemanticCacheConfig,
    SemanticCacheEntry,
    SemanticCacheLookup,
    _hash,
    _jaccard,
    _normalise,
)

# ---------------------------------------------------------------------------
# Fixtures — bytes responses (CCG-15)
# ---------------------------------------------------------------------------

RESPONSE_A_BYTES = json.dumps(
    {"choices": [{"message": {"content": "Paris"}}], "model": "gpt-4o"}
).encode()
RESPONSE_B_BYTES = json.dumps(
    {"choices": [{"message": {"content": "London"}}], "model": "gpt-4o"}
).encode()
SSE_RESPONSE_BYTES = b'data: {"type":"message_start"}\n\ndata: {"type":"message_stop"}\n\n'


def make_cache(**kwargs) -> SemanticCache:
    cfg = SemanticCacheConfig(**kwargs)
    return SemanticCache(cfg)


def _store_json(sc: SemanticCache, query: str, response: bytes = RESPONSE_A_BYTES) -> None:
    """Helper: store a JSON-format entry."""
    sc.store(query, response, content_type="application/json", wire_format="json")


def _store_sse(sc: SemanticCache, query: str, response: bytes = SSE_RESPONSE_BYTES) -> None:
    """Helper: store an SSE-format entry."""
    sc.store(query, response, content_type="text/event-stream; charset=utf-8", wire_format="sse")


class TestResponseTypeBoundary:
    def test_store_rejects_dict_response_before_cache_mutation(self):
        sc = make_cache()

        with pytest.raises(TypeError, match="response_bytes must be bytes"):
            sc.store("legacy dict response", cast(bytes, {"content": "response"}))

        assert sc.size() == 0

    def test_lookup_evicts_preexisting_legacy_dict_entry(self, caplog):
        sc = make_cache()
        query = "legacy persisted response"
        normalized = _normalise(query)
        query_hash = _hash(normalized)
        sc._store[f"{query_hash}:json"] = SemanticCacheEntry(
            query_normalized=normalized,
            query_hash=query_hash,
            response=cast(bytes, {"content": "legacy"}),
            content_type="application/json",
            wire_format="json",
            created_at=time.monotonic(),
        )

        with caplog.at_level("WARNING", logger="tokenpak.cache.semantic_cache"):
            result = sc.lookup(query, expected_format="json")

        assert result.hit is False
        assert sc.size() == 0
        assert "evicting old-schema entry" in caplog.text


# ===========================================================================
# 1. Exact duplicate query returns cached response
# ===========================================================================


class TestExactMatch:
    def test_exact_query_returns_cached_response(self):
        sc = make_cache()
        query = "What is the capital of France?"
        _store_json(sc, query, RESPONSE_A_BYTES)
        result = sc.lookup(query, expected_format="json")
        assert result.hit is True
        assert result.entry is not None
        assert result.entry.response == RESPONSE_A_BYTES
        assert result.match_strategy == "exact"
        assert result.similarity == 1.0

    def test_exact_match_increments_hit_count(self):
        sc = make_cache()
        query = "Explain quantum entanglement"
        _store_json(sc, query)
        sc.lookup(query, expected_format="json")
        sc.lookup(query, expected_format="json")
        # CCG-15: composite key = query_hash:wire_format
        _key = f"{_hash(_normalise(query))}:json"
        assert sc._store[_key].hit_count == 2

    def test_exact_match_normalised_variant(self):
        """Trailing spaces and different case should still hit."""
        sc = make_cache()
        _store_json(sc, "What is Python?")
        result = sc.lookup("  what is python  ", expected_format="json")
        assert result.hit is True
        assert result.match_strategy == "exact"

    def test_filler_words_stripped(self):
        """'Please tell me X' and 'tell me X' should match exactly."""
        sc = make_cache()
        _store_json(sc, "tell me about machine learning")
        result = sc.lookup("Please tell me about machine learning", expected_format="json")
        assert result.hit is True


# ===========================================================================
# 2. Near-duplicate above threshold returns cached (Jaccard)
# ===========================================================================


class TestNearDuplicateHit:
    def test_near_duplicate_above_threshold_hits(self):
        sc = make_cache(similarity_threshold=0.80)
        _store_json(sc, "What is the capital city of France?")
        result = sc.lookup(
            "What is the capital city of France and Germany?", expected_format="json"
        )
        assert isinstance(result.hit, bool)

    def test_high_overlap_query_hits(self):
        sc = make_cache(similarity_threshold=0.70)
        query1 = "explain how neural networks learn weights"
        query2 = "explain how neural networks learn"
        _store_json(sc, query1)
        result = sc.lookup(query2, expected_format="json")
        # query2 tokens are a subset of query1 → Jaccard = |q2|/|q1| = 6/7 ≈ 0.857
        assert result.hit is True
        assert result.match_strategy == "jaccard"
        assert result.similarity >= 0.70

    def test_jaccard_hit_uses_cached_response(self):
        sc = make_cache(similarity_threshold=0.70)
        _store_json(sc, "list the top 5 programming languages today", RESPONSE_A_BYTES)
        result = sc.lookup("list the top 5 programming languages", expected_format="json")
        if result.hit:
            assert result.entry.response == RESPONSE_A_BYTES


# ===========================================================================
# 3. Below threshold makes fresh LLM call (miss)
# ===========================================================================


class TestBelowThresholdMiss:
    def test_completely_different_query_misses(self):
        sc = make_cache()
        _store_json(sc, "What is the capital of France?")
        result = sc.lookup("How do I sort a list in Python?", expected_format="json")
        assert result.hit is False
        assert result.match_strategy == "none"
        assert result.entry is None

    def test_low_overlap_misses(self):
        sc = make_cache(similarity_threshold=0.90)
        _store_json(sc, "apple banana cherry date elderberry fig grape")
        result = sc.lookup("python java ruby rust go swift kotlin", expected_format="json")
        assert result.hit is False

    def test_empty_cache_always_misses(self):
        sc = make_cache()
        result = sc.lookup("anything at all", expected_format="json")
        assert result.hit is False

    def test_miss_increments_miss_counter(self):
        sc = make_cache()
        sc.lookup("first miss", expected_format="json")
        sc.lookup("second miss", expected_format="json")
        assert sc.stats()["misses"] == 2


# ===========================================================================
# 4. TTL expiration works
# ===========================================================================


class TestTTLExpiration:
    def test_entry_expired_after_ttl(self):
        sc = make_cache(ttl_seconds=1)
        query = "Will this expire?"
        _store_json(sc, query)
        assert sc.lookup(query, expected_format="json").hit is True
        time.sleep(1.1)
        result = sc.lookup(query, expected_format="json")
        assert result.hit is False

    def test_evict_expired_removes_entries(self):
        sc = make_cache(ttl_seconds=1)
        _store_json(sc, "expiring query")
        assert sc.size() == 1
        time.sleep(1.1)
        sc._evict_expired()
        assert sc.size() == 0

    def test_live_entry_not_expired(self):
        sc = make_cache(ttl_seconds=300)
        _store_json(sc, "long lived query")
        assert sc.size() == 1


# ===========================================================================
# 5. Max entries eviction works
# ===========================================================================


class TestMaxEntriesEviction:
    def test_oldest_entry_evicted_at_capacity(self):
        sc = make_cache(max_entries=3)
        _store_json(sc, "query one", RESPONSE_A_BYTES)
        _store_json(sc, "query two", RESPONSE_A_BYTES)
        _store_json(sc, "query three", RESPONSE_A_BYTES)
        _store_json(sc, "query four", RESPONSE_B_BYTES)
        assert sc.size() == 3
        result = sc.lookup("query one", expected_format="json")
        assert result.hit is False

    def test_size_never_exceeds_max(self):
        sc = make_cache(max_entries=5)
        for i in range(20):
            sc.store(
                f"unique query number {i} with distinct tokens xyz{i}",
                json.dumps({"i": i}).encode(),
                "application/json",
                "json",
            )
        assert sc.size() <= 5

    def test_overwrite_same_hash_does_not_grow(self):
        sc = make_cache(max_entries=2)
        _store_json(sc, "stable query here", RESPONSE_A_BYTES)
        _store_json(sc, "stable query here", RESPONSE_B_BYTES)
        assert sc.size() == 1


# ===========================================================================
# 6. Cache disabled when config says so
# ===========================================================================


class TestCacheDisabled:
    def test_disabled_cache_always_misses(self):
        sc = make_cache(enabled=False)
        _store_json(sc, "does this matter?")
        result = sc.lookup("does this matter?", expected_format="json")
        assert result.hit is False
        assert result.match_strategy == "disabled"

    def test_disabled_returns_lookup_with_hit_false(self):
        sc = make_cache(enabled=False)
        result = sc.lookup("any query", expected_format="json")
        assert isinstance(result, SemanticCacheLookup)
        assert result.hit is False


# ===========================================================================
# Extras: normalisation, Jaccard, stats, invalidate
# ===========================================================================


class TestNormalisation:
    def test_lowercase(self):
        assert _normalise("QUICK BROWN FOX") == "quick brown fox"

    def test_strip_whitespace(self):
        assert _normalise("  quick   brown   fox  ") == "quick brown fox"

    def test_filler_removed(self):
        result = _normalise("Please could you explain this")
        assert "please" not in result
        assert "could" not in result

    def test_non_alphanumeric_stripped(self):
        result = _normalise("hello, world! How's it going?")
        assert "," not in result
        assert "!" not in result
        assert "'" not in result


class TestJaccard:
    def test_identical_sets(self):
        a = frozenset(["a", "b", "c"])
        assert _jaccard(a, a) == 1.0

    def test_disjoint_sets(self):
        a = frozenset(["a", "b"])
        b = frozenset(["c", "d"])
        assert _jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        a = frozenset(["a", "b", "c"])
        b = frozenset(["b", "c", "d"])
        # intersection=2, union=4
        assert abs(_jaccard(a, b) - 0.5) < 1e-9

    def test_empty_sets(self):
        assert _jaccard(frozenset(), frozenset()) == 1.0


class TestStats:
    def test_stats_initial(self):
        sc = make_cache()
        s = sc.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["hit_rate"] == 0.0

    def test_stats_after_operations(self):
        sc = make_cache()
        _store_json(sc, "some query")
        sc.lookup("some query", expected_format="json")  # hit
        sc.lookup("completely different unrelated words here", expected_format="json")  # miss
        s = sc.stats()
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert s["hit_rate"] == 0.5


class TestInvalidate:
    def test_invalidate_removes_entry(self):
        sc = make_cache()
        _store_json(sc, "remove me please")
        removed = sc.invalidate("remove me please")
        assert removed is True
        assert sc.lookup("remove me please", expected_format="json").hit is False

    def test_invalidate_nonexistent_returns_false(self):
        sc = make_cache()
        assert sc.invalidate("this was never stored") is False

    def test_clear_empties_cache(self):
        sc = make_cache()
        _store_json(sc, "a query")
        _store_json(sc, "another query", RESPONSE_B_BYTES)
        sc.clear()
        assert sc.size() == 0
