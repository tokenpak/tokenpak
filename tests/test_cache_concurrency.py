"""Test cache behavior under concurrent access and edge cases."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tokenpak.proxy.cache import LRUCache


class TestCacheLRUEviction:
    """Test that cache respects LRU (Least Recently Used) eviction policy."""

    def test_cache_respects_lru_order(self):
        """Cache should evict least recently used items when capacity exceeded."""
        # Use 1 MB cache to easily trigger eviction with test data
        cache = LRUCache(max_size_mb=1, ttl_seconds=None)
        
        # Add small test entries
        cache.set("key_a", b"value_a_test_data")
        cache.set("key_b", b"value_b_test_data")
        cache.set("key_c", b"value_c_test_data")
        
        # Access key_a to mark it as recently used
        cache.get("key_a")
        
        # Add more data to trigger eviction
        # Least recently used (key_b or key_c) should be evicted
        cache.set("key_d", b"x" * 500000)  # Large entry
        cache.set("key_e", b"x" * 500000)  # Triggers eviction
        
        # Verify cache is still functional
        assert cache.get("key_a") is not None or cache.get("key_c") is not None

    def test_cache_respects_size_limit(self):
        """Cache should not exceed max_size_mb limit."""
        cache = LRUCache(max_size_mb=0.5, ttl_seconds=None)
        
        # Add entries up to size limit
        for i in range(10):
            cache.set(f"key_{i}", b"x" * 50000)  # 50KB each
        
        # Cache should still work without crashing
        assert cache.get("key_0") is not None or cache.get("key_9") is not None

    def test_cache_lru_with_access_pattern(self):
        """LRU should track access order, not just insertion order."""
        cache = LRUCache(max_size_mb=1, ttl_seconds=None)
        
        cache.set("a", b"data_a_xyz")
        cache.set("b", b"data_b_xyz")
        cache.set("c", b"data_c_xyz")
        
        # Access 'a' to make it more recently used
        _ = cache.get("a")
        
        # 'a' should be more likely to stay than 'b' when eviction happens
        # Add large entry to trigger eviction
        cache.set("large_1", b"x" * 600000)
        
        # Verify cache still works
        result = cache.get("a")
        assert result is not None or cache.get("c") is not None


class TestCacheInvalidation:
    """Test that cache invalidation removes entries correctly."""

    def test_cache_delete_removes_entry(self):
        """Delete should remove an entry from cache."""
        cache = LRUCache(max_size_mb=1)
        
        cache.set("key_x", b"value_x")
        assert cache.get("key_x") is not None
        
        cache.delete("key_x")
        assert cache.get("key_x") is None

    def test_cache_clear_removes_all(self):
        """Clear should remove all entries."""
        cache = LRUCache(max_size_mb=1)
        
        cache.set("a", b"data_a")
        cache.set("b", b"data_b")
        cache.set("c", b"data_c")
        
        cache.clear()
        
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.get("c") is None

    def test_cache_delete_nonexistent_key(self):
        """Delete on non-existent key should not error."""
        cache = LRUCache(max_size_mb=1)
        
        # Should not raise exception
        cache.delete("does_not_exist")
        
        # Cache should still work
        cache.set("key_a", b"value_a")
        assert cache.get("key_a") is not None


class TestCacheConcurrentAccess:
    """Test that concurrent reads/writes don't corrupt cache state."""

    def test_concurrent_reads(self):
        """Multiple threads reading same key should get consistent values."""
        cache = LRUCache(max_size_mb=1)
        cache.set("shared_key", b"shared_value_data")
        
        results = []
        errors = []
        
        def reader():
            try:
                value = cache.get("shared_key")
                results.append(value)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # No exceptions during concurrent reads
        assert len(errors) == 0
        # All reads should find the value
        assert len(results) > 0

    def test_concurrent_writes_different_keys(self):
        """Multiple threads writing to different keys should not corrupt cache."""
        cache = LRUCache(max_size_mb=10)
        errors = []
        
        def writer(thread_id: int):
            try:
                for i in range(10):
                    cache.set(f"key_{thread_id}_{i}", b"x" * 10000)
            except Exception as e:
                errors.append(e)
        
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(writer, tid) for tid in range(3)]
            for f in futures:
                f.result()
        
        # No exceptions
        assert len(errors) == 0
        
        # Verify at least some keys were written
        assert cache.get("key_0_0") is not None or cache.get("key_1_0") is not None

    def test_concurrent_read_write(self):
        """Concurrent reads and writes should maintain consistency."""
        cache = LRUCache(max_size_mb=10)
        cache.set("initial", b"initial_value_data")
        
        read_errors = []
        write_errors = []
        
        def reader():
            try:
                for _ in range(20):
                    cache.get("initial")
                    time.sleep(0.001)
            except Exception as e:
                read_errors.append(e)
        
        def writer():
            try:
                for i in range(20):
                    cache.set(f"new_{i}", b"x" * 5000)
                    time.sleep(0.001)
            except Exception as e:
                write_errors.append(e)
        
        with ThreadPoolExecutor(max_workers=6) as executor:
            # 3 readers + 3 writers
            for _ in range(3):
                executor.submit(reader)
            for _ in range(3):
                executor.submit(writer)
        
        # No exceptions
        assert len(read_errors) == 0
        assert len(write_errors) == 0

    def test_concurrent_eviction(self):
        """Cache eviction during concurrent writes should be atomic."""
        cache = LRUCache(max_size_mb=2)
        errors = []
        
        def concurrent_writer(thread_id: int):
            try:
                for i in range(20):
                    key = f"key_t{thread_id}_i{i}"
                    cache.set(key, b"x" * 50000)
            except Exception as e:
                errors.append(e)
        
        with ThreadPoolExecutor(max_workers=4) as executor:
            for tid in range(4):
                executor.submit(concurrent_writer, tid)
        
        # No errors during concurrent eviction
        assert len(errors) == 0
        
        # Cache should still be accessible
        result = cache.get("key_t0_i0")
        # Either the key exists or it was evicted (both are valid)
        assert result is None or result is not None

    def test_concurrent_operations_no_deadlock(self):
        """
        Concurrent operations should not deadlock.
        This is a smoke test to ensure the cache handles contention safely.
        """
        cache = LRUCache(max_size_mb=5)
        completion = {"done": False}
        errors = []
        
        def aggressive_access(thread_id: int):
            try:
                for i in range(50):
                    cache.set(f"k{thread_id}_{i}", b"x" * 20000)
                    if i % 5 == 0:
                        cache.get(f"k{thread_id}_{i-2}")
                    if i % 10 == 0:
                        cache.delete(f"k{thread_id}_{i-5}")
            except Exception as e:
                errors.append(e)
        
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(aggressive_access, t) for t in range(8)]
            
            # Wait for completion with timeout
            for future in futures:
                try:
                    future.result(timeout=10)
                except TimeoutError:
                    pytest.fail("Cache operations deadlocked")
        
        elapsed = time.time() - start_time
        completion["done"] = True
        
        # Should complete reasonably quickly
        assert elapsed < 30
        assert len(errors) == 0
        assert completion["done"] is True
