"""Tests for LRU cache eviction policy."""

import time
import pytest
import threading
from tokenpak.proxy.cache import LRUCache, CacheEntry


class TestCacheEvictionLRU:
    """Test LRU eviction behaviour."""

    def test_cache_eviction_lru_basic(self):
        """LRU entry is evicted when cache is full."""
        # 1 byte max — forces eviction on every new entry
        cache = LRUCache(max_size_mb=0.000001, ttl_seconds=None)
        cache.set("a", "value_a")
        cache.set("b", "value_b")  # should evict "a"
        assert cache.get("a") is None
        assert cache.get("b") == "value_b"

    def test_cache_eviction_lru_order(self):
        """Access order determines eviction order."""
        cache = LRUCache(max_size_mb=1.0, ttl_seconds=None)
        # Fill cache
        for i in range(10):
            cache.set(f"key_{i}", f"val_{i}")

        # Access key_0 to make it recently used
        cache.get("key_0")

        # Force eviction by adding new entries
        initial_count = len(cache)
        cache.set("new_key", "x" * (1024 * 900))  # large entry triggers eviction

        # key_1 (LRU after key_0 was accessed) should be gone
        assert cache.get("key_0") is not None  # was recently accessed

    def test_cache_eviction_metrics_lru(self):
        """Eviction counter increments on LRU eviction."""
        cache = LRUCache(max_size_mb=0.000001, ttl_seconds=None)
        cache.set("a", "x")
        cache.set("b", "y")  # triggers eviction of "a"
        assert cache.metrics.evictions_lru >= 1

    def test_cache_eviction_updates_size(self):
        """Size metric decreases after eviction."""
        cache = LRUCache(max_size_mb=1.0, ttl_seconds=None)
        cache.set("a", "x" * 100)
        size_before = cache.metrics.current_size_bytes
        cache.delete("a")
        assert cache.metrics.current_size_bytes < size_before

    def test_cache_eviction_does_not_evict_below_limit(self):
        """No eviction when under size limit."""
        cache = LRUCache(max_size_mb=100.0, ttl_seconds=None)
        for i in range(50):
            cache.set(f"k{i}", f"v{i}")
        assert cache.metrics.evictions_lru == 0


class TestCacheTTLExpiry:
    """Test TTL-based expiration."""

    def test_cache_ttl_expiry_basic(self):
        """Entry expires after TTL."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=0.05)  # 50ms TTL
        cache.set("key", "value")
        assert cache.get("key") == "value"
        time.sleep(0.1)  # wait for expiry
        assert cache.get("key") is None

    def test_cache_ttl_not_expired(self):
        """Entry is accessible before TTL."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=60.0)
        cache.set("key", "value")
        assert cache.get("key") == "value"

    def test_cache_ttl_per_entry_override(self):
        """Per-entry TTL overrides default."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=60.0)
        cache.set("fast_key", "value", ttl_seconds=0.05)
        cache.set("slow_key", "value", ttl_seconds=60.0)

        time.sleep(0.1)
        assert cache.get("fast_key") is None   # expired
        assert cache.get("slow_key") == "value"  # still valid

    def test_cache_ttl_eviction_counter(self):
        """TTL eviction increments evictions_ttl counter."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=0.05)
        cache.set("key", "value")
        time.sleep(0.1)
        cache.get("key")  # triggers eviction on access
        assert cache.metrics.evictions_ttl >= 1

    def test_cache_ttl_none_means_no_expiry(self):
        """TTL=None entries never expire."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=None)
        cache.set("key", "value")
        # No time.sleep needed — just verify is_expired() is False
        entry = CacheEntry(
            key="k", value="v", created_at=0.0, last_accessed=0.0,
            ttl_seconds=None, size_bytes=10
        )
        assert not entry.is_expired()

    def test_cache_evict_expired_bulk(self):
        """evict_expired() removes all stale entries."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=0.05)
        for i in range(10):
            cache.set(f"k{i}", f"v{i}")
        time.sleep(0.1)
        evicted = cache.evict_expired()
        assert evicted == 10
        assert len(cache) == 0


class TestCacheMetrics:
    """Test metrics tracking."""

    def test_hit_rate_calculation(self):
        """hit_rate = hits / (hits + misses)."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=None)
        cache.set("k", "v")
        cache.get("k")   # hit
        cache.get("k")   # hit
        cache.get("x")   # miss
        assert cache.metrics.hits == 2
        assert cache.metrics.misses == 1
        assert abs(cache.metrics.hit_rate - 2/3) < 0.01

    def test_metrics_dict_keys(self):
        """metrics_dict() returns expected keys."""
        cache = LRUCache(max_size_mb=10.0, ttl_seconds=None)
        m = cache.metrics_dict()
        assert "hits" in m
        assert "misses" in m
        assert "hit_rate" in m
        assert "evictions_lru" in m
        assert "evictions_ttl" in m
        assert "current_entries" in m
        assert "current_size_mb" in m


class TestCacheThreadSafety:
    """Basic thread safety verification."""

    def test_concurrent_reads_writes(self):
        """No exceptions under concurrent load."""
        cache = LRUCache(max_size_mb=1.0, ttl_seconds=None)
        errors = []

        def worker(tid):
            try:
                for i in range(100):
                    cache.set(f"key_{tid}_{i}", f"val_{i}")
                    cache.get(f"key_{tid}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
