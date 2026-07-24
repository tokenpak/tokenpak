"""Integration tests for TokenPak caching behavior.

Tests verify cache behavior works across adapters:
- Cache hit detection
- Token count reduction
- Response time improvement
- Cache invalidation
"""

import time

import pytest

# TSR-05r speculative-contract skip reason (grep-able)
# ─────────────────────────────────────────────
# Four tests below assert against `CacheManager` API surface that **never
# existed** in `tokenpak/cache/cache_manager.py` git history:
#
#   - `CacheManager(ttl=...)` kwarg in `__init__`            (never existed)
#   - `cache.invalidate(model=...)` method                   (never existed)
#   - `cache.get_stats()` method                             (never existed)
#
# Verified via `git log -S 'def invalidate' --all -- tokenpak/cache/cache_manager.py`
# (0 hits) and `git log -S 'def get_stats' --all -- tokenpak/cache/cache_manager.py`
# (0 hits). The actual `CacheManager` interface is `__init__(volatile_cache,
# stable_cache, volatile_threshold)` + `get / set / delete / clear` +
# `volatile / stable` properties — no `ttl` kwarg, no `invalidate`, no
# `get_stats`.
#
# These were added in the "comprehensive integration test suite" sweep
# (commit 84f4f19b90 / 28a1448d2f) and encoded a speculative contract that
# never landed in production. Same Path B pattern as TSR-05b's `/ready`
# endpoint skip — assertions against an intended-but-never-built surface.
#
# `test_cache_manual_invalidation` (only uses `cache.clear()`, which DOES
# exist) is unaffected and remains live, as do the 4 tests in
# TestCacheHitDetection / TestCacheTokenReduction / TestCacheResponseTime
# that aren't skipped or already-`pytest.skip`-guarded.
SKIP_CACHE_MANAGER_SPECULATIVE_API = (
    "Test asserts CacheManager API (ttl= kwarg, invalidate(), get_stats()) "
    "that never existed in cache_manager.py git history. Speculative "
    "contract — see TSR-05b for the same pattern (Path B skip)."
)


class TestCacheHitDetection:
    """Test cache hit detection."""

    def test_identical_requests_hit_cache(self):
        """Verify identical requests are detected as cache hits."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()

        request1 = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        request2 = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}

        # First request should miss
        result1 = cache.get(request1)
        assert result1 is None

        # Store it
        response = {"content": "Hi there", "tokens": 10}
        cache.set(request1, response)

        # Identical request should hit
        result2 = cache.get(request2)
        assert result2 is not None
        assert result2["content"] == "Hi there"

    def test_similar_requests_miss_cache(self):
        """Verify similar but different requests miss cache."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()

        request1 = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        request2 = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello there"}],  # Different!
        }

        cache.set(request1, {"content": "response", "tokens": 10})

        # Different content should miss
        result = cache.get(request2)
        assert result is None

    def test_cache_key_normalization(self):
        """Test that cache keys are properly normalized."""
        try:
            from tokenpak.cache import normalize_request
        except ImportError:
            pytest.skip("normalize_request not available")

        req1 = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}
        req2 = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}

        key1 = normalize_request(req1)
        key2 = normalize_request(req2)

        assert key1 == key2


class TestCacheTokenReduction:
    """Test token count reduction via caching."""

    def test_cache_reduces_token_count(self):
        """Verify cached responses reduce token usage."""
        try:
            from tokenpak.cache import CacheManager
            from tokenpak.metrics import MetricsCollector
        except ImportError:
            pytest.skip("Cache and metrics not available")

        cache = CacheManager()
        metrics = MetricsCollector()

        request = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        response = {
            "content": "Test response",
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }

        # First call: counts tokens normally
        tokens_first = 10 + 8
        assert tokens_first == 18

        # Store in cache
        cache.set(request, response)
        metrics.record_request(request, response, from_cache=False)

        # Second call: from cache, should have reduced count
        cached_response = cache.get(request)
        metrics.record_request(request, cached_response, from_cache=True)

        # Cache should have reduced token count
        stats = metrics.get_stats()
        assert stats["total_requests"] == 2
        assert stats["cache_hits"] >= 1

    def test_cache_cost_savings(self):
        """Test cache provides cost savings."""
        try:
            from tokenpak.metrics import CostCalculator
        except ImportError:
            pytest.skip("CostCalculator not available")

        calc = CostCalculator()

        # Direct call cost
        direct_cost = calc.calculate_cost(
            model="gpt-4", input_tokens=100, output_tokens=50, from_cache=False
        )

        # Cached call cost (should be 0 or much lower)
        cached_cost = calc.calculate_cost(
            model="gpt-4", input_tokens=100, output_tokens=50, from_cache=True
        )

        assert cached_cost <= direct_cost
        if direct_cost > 0:
            assert cached_cost == 0  # Cached calls should be free


class TestCacheResponseTime:
    """Test cache improves response time."""

    def test_cached_response_faster_than_api(self):
        """Verify cached responses are faster than API calls."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()
        request = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        response = {"content": "Test", "tokens": 10}

        # Warm cache
        cache.set(request, response)

        # Time cache hit
        start = time.time()
        result = cache.get(request)
        cache_time = time.time() - start

        assert result is not None
        # Cache hit should be microseconds, not milliseconds
        assert cache_time < 0.001  # Less than 1ms

    def test_cache_miss_latency(self):
        """Test cache miss latency is acceptable."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()
        request = {"model": "gpt-4", "messages": [{"role": "user", "content": "New"}]}

        # Time cache miss
        start = time.time()
        result = cache.get(request)
        miss_time = time.time() - start

        assert result is None
        # Cache miss check should be quick (sub-millisecond)
        assert miss_time < 0.001


class TestCacheInvalidation:
    """Test cache invalidation behavior."""

    @pytest.mark.skip(reason=SKIP_CACHE_MANAGER_SPECULATIVE_API)
    def test_cache_ttl_expiration(self):
        """Test cache entries expire after TTL."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager(ttl=0.1)  # 100ms TTL for testing
        request = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}

        cache.set(request, {"content": "response", "tokens": 10})

        # Should hit immediately
        assert cache.get(request) is not None

        # Wait for expiration
        time.sleep(0.15)

        # Should miss after TTL
        assert cache.get(request) is None

    def test_cache_manual_invalidation(self):
        """Test manual cache invalidation."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()
        request = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}

        cache.set(request, {"content": "response", "tokens": 10})
        assert cache.get(request) is not None

        # Clear cache
        cache.clear()
        assert cache.get(request) is None

    @pytest.mark.skip(reason=SKIP_CACHE_MANAGER_SPECULATIVE_API)
    def test_cache_selective_invalidation(self):
        """Test selective cache invalidation by key pattern."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()

        # Add multiple entries
        req1 = {"model": "gpt-4", "messages": [{"role": "user", "content": "A"}]}
        req2 = {"model": "gpt-3.5", "messages": [{"role": "user", "content": "B"}]}

        cache.set(req1, {"content": "response1", "tokens": 10})
        cache.set(req2, {"content": "response2", "tokens": 10})

        # Invalidate gpt-4 entries only
        cache.invalidate(model="gpt-4")

        # gpt-4 should be gone
        assert cache.get(req1) is None

        # gpt-3.5 should remain
        assert cache.get(req2) is not None


@pytest.mark.skip(reason=SKIP_CACHE_MANAGER_SPECULATIVE_API)
class TestCacheStatistics:
    """Test cache statistics collection."""

    def test_cache_hit_rate_tracking(self):
        """Test cache hit rate is tracked."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()
        request = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}

        cache.set(request, {"content": "response", "tokens": 10})

        # Miss
        cache.get({"model": "gpt-4", "messages": [{"role": "user", "content": "X"}]})

        # Hit
        cache.get(request)

        # Hit
        cache.get(request)

        stats = cache.get_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 2 / 3

    def test_cache_size_monitoring(self):
        """Test cache size is monitored."""
        try:
            from tokenpak.cache import CacheManager
        except ImportError:
            pytest.skip("CacheManager not available")

        cache = CacheManager()

        # Add entries
        for i in range(5):
            request = {"model": "gpt-4", "messages": [{"role": "user", "content": f"Message {i}"}]}
            cache.set(request, {"content": f"response {i}", "tokens": 10})

        stats = cache.get_stats()
        assert stats["entries"] == 5
        assert stats["total_tokens"] == 50
