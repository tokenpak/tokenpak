"""
Unit tests for tokenpak.proxy.middleware.semantic_cache_middleware.SemanticCacheMiddleware

Covers: initialization, scope management, check/record lifecycle, build_trace,
        stats, and clear.  Uses real SemanticCache (pure Python, no network I/O).
"""

import time
import unittest

from tokenpak.cache.semantic_cache import (
    SemanticCache,
    SemanticCacheConfig,
    SemanticCacheEntry,
    SemanticCacheLookup,
)
from tokenpak.proxy.middleware.semantic_cache_middleware import SemanticCacheMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_mw(**kwargs) -> SemanticCacheMiddleware:
    """Middleware with session scope."""
    return SemanticCacheMiddleware(SemanticCacheConfig(scope="session", **kwargs))


def _global_mw(**kwargs) -> SemanticCacheMiddleware:
    """Middleware with global scope."""
    return SemanticCacheMiddleware(SemanticCacheConfig(scope="global", **kwargs))


def _make_lookup_hit(
    query_hash: str = "abc123def456abcdef",
    match_strategy: str = "exact",
    similarity: float = 1.0,
    savings_tokens: int = 0,
) -> SemanticCacheLookup:
    entry = SemanticCacheEntry(
        query_normalized="test query",
        query_hash=query_hash,
        response=b'{"answer": "test"}',
        content_type="application/json",
        wire_format="json",
        created_at=time.monotonic(),
    )
    return SemanticCacheLookup(
        hit=True,
        query_hash=query_hash,
        matched_hash=query_hash,
        similarity=similarity,
        match_strategy=match_strategy,
        entry=entry,
        savings_tokens=savings_tokens,
    )


def _make_lookup_miss() -> SemanticCacheLookup:
    return SemanticCacheLookup(
        hit=False,
        query_hash="",
        matched_hash="",
        similarity=0.0,
        match_strategy="none",
    )


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestSemanticCacheMiddlewareInit(unittest.TestCase):
    def test_default_config_is_session_scoped(self):
        mw = SemanticCacheMiddleware()
        self.assertEqual(mw._cfg.scope, "session")

    def test_session_scope_starts_with_no_caches(self):
        mw = _session_mw()
        self.assertEqual(mw._caches, {})

    def test_agent_scope_starts_with_no_caches(self):
        mw = SemanticCacheMiddleware(SemanticCacheConfig(scope="agent"))
        self.assertEqual(mw._caches, {})

    def test_global_scope_pre_creates_global_cache(self):
        mw = _global_mw()
        self.assertIn("__global__", mw._caches)

    def test_global_scope_only_one_cache_created(self):
        mw = _global_mw()
        self.assertEqual(len(mw._caches), 1)

    def test_global_cache_is_semantic_cache_instance(self):
        mw = _global_mw()
        self.assertIsInstance(mw._caches["__global__"], SemanticCache)

    def test_custom_config_stored(self):
        cfg = SemanticCacheConfig(enabled=True, ttl_seconds=600)
        mw = SemanticCacheMiddleware(cfg)
        self.assertIs(mw._cfg, cfg)


# ---------------------------------------------------------------------------
# _resolve_scope_key
# ---------------------------------------------------------------------------


class TestResolveScopeKey(unittest.TestCase):
    def test_session_scope_returns_given_key(self):
        mw = _session_mw()
        self.assertEqual(mw._resolve_scope_key("sess-abc"), "sess-abc")

    def test_session_scope_empty_key_returns_default(self):
        mw = _session_mw()
        self.assertEqual(mw._resolve_scope_key(""), "__default__")

    def test_global_scope_ignores_key(self):
        mw = _global_mw()
        self.assertEqual(mw._resolve_scope_key("sess-xyz"), "__global__")
        self.assertEqual(mw._resolve_scope_key(""), "__global__")
        self.assertEqual(mw._resolve_scope_key("anything"), "__global__")

    def test_agent_scope_returns_given_key(self):
        mw = SemanticCacheMiddleware(SemanticCacheConfig(scope="agent"))
        self.assertEqual(mw._resolve_scope_key("agent-001"), "agent-001")


# ---------------------------------------------------------------------------
# _get_or_create_cache
# ---------------------------------------------------------------------------


class TestGetOrCreateCache(unittest.TestCase):
    def test_creates_cache_on_first_access(self):
        mw = _session_mw()
        cache = mw._get_or_create_cache("s1")
        self.assertIsInstance(cache, SemanticCache)
        self.assertIn("s1", mw._caches)

    def test_returns_same_instance_on_repeated_access(self):
        mw = _session_mw()
        c1 = mw._get_or_create_cache("s1")
        c2 = mw._get_or_create_cache("s1")
        self.assertIs(c1, c2)

    def test_different_scope_keys_give_different_caches(self):
        mw = _session_mw()
        c1 = mw._get_or_create_cache("s1")
        c2 = mw._get_or_create_cache("s2")
        self.assertIsNot(c1, c2)

    def test_global_scope_all_keys_route_to_same_cache(self):
        mw = _global_mw()
        c1 = mw._get_or_create_cache("sess-A")
        c2 = mw._get_or_create_cache("sess-B")
        self.assertIs(c1, c2)


# ---------------------------------------------------------------------------
# check / record
# ---------------------------------------------------------------------------


class TestCheckAndRecord(unittest.TestCase):
    def test_check_returns_miss_on_empty_cache(self):
        mw = _session_mw()
        result = mw.check("What is AI?", scope_key="s1")
        self.assertFalse(result.hit)

    def test_check_miss_has_no_entry(self):
        mw = _session_mw()
        result = mw.check("some query", scope_key="s1")
        self.assertIsNone(result.entry)

    def test_record_then_check_exact_hit(self):
        mw = _session_mw()
        q = "What is the capital of France?"
        mw.record(q, b'{"answer": "Paris"}', scope_key="s1")
        result = mw.check(q, scope_key="s1")
        self.assertTrue(result.hit)
        self.assertEqual(result.match_strategy, "exact")

    def test_record_then_check_entry_contains_response(self):
        mw = _session_mw()
        q = "What is Python?"
        resp = b'{"answer": "A programming language"}'
        mw.record(q, resp, scope_key="s1")
        result = mw.check(q, scope_key="s1")
        self.assertIsNotNone(result.entry)
        self.assertEqual(result.entry.response, resp)

    def test_record_rejects_parsed_response_objects(self):
        mw = _session_mw()

        with self.assertRaisesRegex(TypeError, "response_bytes must be bytes"):
            mw.record("What is Python?", {"answer": "A programming language"})  # type: ignore[arg-type]

    def test_session_scope_isolation_between_keys(self):
        mw = _session_mw()
        q = "What is machine learning?"
        mw.record(q, b'{"answer": "..."}', scope_key="sess-A")
        result = mw.check(q, scope_key="sess-B")
        self.assertFalse(result.hit)

    def test_global_scope_shared_across_keys(self):
        mw = _global_mw()
        q = "What is deep learning?"
        mw.record(q, b'{"answer": "..."}', scope_key="sess-A")
        result = mw.check(q, scope_key="sess-B")
        self.assertTrue(result.hit)

    def test_record_stores_in_correct_cache(self):
        mw = _session_mw()
        mw.record("hello world", b"{}", scope_key="sess-X")
        self.assertIn("sess-X", mw._caches)
        self.assertEqual(mw._caches["sess-X"].size(), 1)

    def test_multiple_records_in_same_scope(self):
        mw = _session_mw()
        mw.record("query one", b'{"a":1}', scope_key="s1")
        mw.record("query two", b'{"b":2}', scope_key="s1")
        self.assertEqual(mw._caches["s1"].size(), 2)

    def test_jaccard_hit_on_similar_query(self):
        mw = _session_mw(similarity_threshold=0.50)
        # Store original; then check with a very close paraphrase
        mw.record("What is artificial intelligence research", b"{}", scope_key="s1")
        # Similar but not identical (shares most tokens)
        result = mw.check("What is artificial intelligence research today", scope_key="s1")
        # Jaccard over shared tokens should hit at 0.50 threshold
        # 5 shared / 6 union = 0.833 > 0.50
        self.assertTrue(result.hit)

    def test_disabled_cache_always_miss(self):
        mw = SemanticCacheMiddleware(SemanticCacheConfig(enabled=False, scope="session"))
        mw.record("hello", b"{}", scope_key="s1")
        result = mw.check("hello", scope_key="s1")
        self.assertFalse(result.hit)


# ---------------------------------------------------------------------------
# build_trace
# ---------------------------------------------------------------------------


class TestBuildTrace(unittest.TestCase):
    def test_trace_structure_on_hit(self):
        mw = SemanticCacheMiddleware()
        lookup = _make_lookup_hit(
            query_hash="abcdef123456abcdef",
            match_strategy="exact",
            similarity=1.0,
            savings_tokens=50,
        )
        trace = mw.build_trace(lookup)
        self.assertIn("semantic_cache", trace)

    def test_hit_flag_in_trace(self):
        mw = SemanticCacheMiddleware()
        trace = mw.build_trace(_make_lookup_hit())
        self.assertTrue(trace["semantic_cache"]["hit"])

    def test_strategy_in_trace(self):
        mw = SemanticCacheMiddleware()
        trace = mw.build_trace(_make_lookup_hit(match_strategy="jaccard"))
        self.assertEqual(trace["semantic_cache"]["strategy"], "jaccard")

    def test_similarity_rounded_to_4dp(self):
        mw = SemanticCacheMiddleware()
        trace = mw.build_trace(_make_lookup_hit(similarity=0.987654321))
        self.assertEqual(trace["semantic_cache"]["similarity"], round(0.987654321, 4))

    def test_savings_tokens_in_trace(self):
        mw = SemanticCacheMiddleware()
        trace = mw.build_trace(_make_lookup_hit(savings_tokens=120))
        self.assertEqual(trace["semantic_cache"]["savings_tokens"], 120)

    def test_query_hash_truncated_to_12_chars(self):
        mw = SemanticCacheMiddleware()
        trace = mw.build_trace(_make_lookup_hit(query_hash="abcdef123456abcdefgh"))
        self.assertEqual(trace["semantic_cache"]["query_hash"], "abcdef123456")

    def test_empty_hash_gives_empty_string(self):
        mw = SemanticCacheMiddleware()
        lookup = _make_lookup_miss()
        trace = mw.build_trace(lookup)
        self.assertEqual(trace["semantic_cache"]["query_hash"], "")

    def test_miss_flag_in_trace(self):
        mw = SemanticCacheMiddleware()
        trace = mw.build_trace(_make_lookup_miss())
        self.assertFalse(trace["semantic_cache"]["hit"])


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStats(unittest.TestCase):
    def test_stats_zero_for_nonexistent_scope(self):
        mw = _session_mw()
        stats = mw.stats(scope_key="nonexistent")
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(stats["misses"], 0)
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["hit_rate"], 0.0)
        self.assertEqual(stats["size"], 0)

    def test_stats_miss_increments_misses(self):
        mw = _session_mw()
        mw.check("some query", scope_key="s-stat")
        stats = mw.stats(scope_key="s-stat")
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hits"], 0)

    def test_stats_hit_increments_hits(self):
        mw = _session_mw()
        q = "What is NLP?"
        mw.record(q, b"{}", scope_key="s-stat2")
        mw.check(q, scope_key="s-stat2")
        stats = mw.stats(scope_key="s-stat2")
        self.assertEqual(stats["hits"], 1)

    def test_stats_total_is_sum(self):
        mw = _session_mw()
        q = "What is NLP?"
        mw.record(q, b"{}", scope_key="s-stat3")
        mw.check(q, scope_key="s-stat3")  # hit
        mw.check("other query", scope_key="s-stat3")  # miss
        stats = mw.stats(scope_key="s-stat3")
        self.assertEqual(stats["total"], 2)

    def test_stats_hit_rate_calculation(self):
        mw = _session_mw()
        q = "What is NLP?"
        mw.record(q, b"{}", scope_key="s-rate")
        mw.check(q, scope_key="s-rate")  # hit
        mw.check("other query", scope_key="s-rate")  # miss
        stats = mw.stats(scope_key="s-rate")
        self.assertAlmostEqual(stats["hit_rate"], 0.5)


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear(unittest.TestCase):
    def test_clear_empties_cache(self):
        mw = _session_mw()
        mw.record("test query", b"{}", scope_key="s-clear")
        self.assertEqual(mw._caches["s-clear"].size(), 1)
        mw.clear(scope_key="s-clear")
        self.assertEqual(mw._caches["s-clear"].size(), 0)

    def test_clear_nonexistent_scope_does_not_raise(self):
        mw = _session_mw()
        mw.clear(scope_key="does-not-exist")  # should not raise

    def test_clear_global_scope_empties_shared_cache(self):
        mw = _global_mw()
        mw.record("global query", b"{}", scope_key="any-key")
        self.assertGreater(mw._caches["__global__"].size(), 0)
        mw.clear(scope_key="any-key")
        self.assertEqual(mw._caches["__global__"].size(), 0)

    def test_clear_one_scope_does_not_affect_another(self):
        mw = _session_mw()
        mw.record("query A", b"{}", scope_key="scope-A")
        mw.record("query B", b"{}", scope_key="scope-B")
        mw.clear(scope_key="scope-A")
        self.assertEqual(mw._caches["scope-A"].size(), 0)
        self.assertEqual(mw._caches["scope-B"].size(), 1)


if __name__ == "__main__":
    unittest.main()
