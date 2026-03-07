"""
Cache Management Example
========================
Demonstrates TokenPak's CacheManager for avoiding redundant LLM calls.

Problem: Repeatedly compressing or processing the same content wastes compute.
Solution: CacheManager stores results with TTL, giving instant cache hits.

Expected benefit: Near-zero latency on repeated queries (100x+ speedup).
Setup: pip install tokenpak
"""

import sys
import os
import time
import hashlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import CacheManager, HeuristicEngine


def basic_cache_demo():
    """Show basic cache get/set/hit patterns."""
    print("=== Basic Cache Demo ===\n")

    cache = CacheManager(default_ttl=300)  # 5-minute TTL

    # Cache a compressed result
    cache.set("my_key", "compressed content here", ttl=60)

    # Retrieve it
    hit, value = cache.get("my_key")
    print(f"Cache hit:  {hit}")
    print(f"Value:      {value!r}")

    # Miss on unknown key
    hit2, value2 = cache.get("unknown_key")
    print(f"\nCache miss: {not hit2}")
    print(f"Value:      {value2!r}")


def compression_cache_demo():
    """Cache compression results to avoid reprocessing."""
    print("\n=== Compression Cache Demo ===\n")

    cache = CacheManager(default_ttl=300)
    engine = HeuristicEngine()

    documents = [
        "The quick brown fox jumps over the lazy dog. " * 20,
        "Machine learning models require large amounts of training data. " * 15,
        "The quick brown fox jumps over the lazy dog. " * 20,  # duplicate!
    ]

    hits = 0
    misses = 0

    for i, doc in enumerate(documents):
        # Use content hash as cache key
        cache_key = hashlib.sha256(doc.encode()).hexdigest()[:16]

        hit, cached = cache.get(cache_key)
        if hit:
            print(f"Doc {i+1}: ✅ Cache HIT  — {len(cached)} chars (saved recompression!)")
            hits += 1
        else:
            start = time.perf_counter()
            compressed = engine.compact(doc)
            elapsed_ms = (time.perf_counter() - start) * 1000

            cache.set(cache_key, compressed, ttl=300)
            print(f"Doc {i+1}: ❌ Cache MISS — compressed {len(doc)}→{len(compressed)} chars "
                  f"in {elapsed_ms:.1f}ms, cached for 5min")
            misses += 1

    print(f"\nSummary: {hits} hits, {misses} misses — {hits/(hits+misses):.0%} hit rate")


def ttl_demo():
    """Demonstrate TTL expiry."""
    print("\n=== TTL Expiry Demo ===\n")

    cache = CacheManager()

    # Set a very short TTL
    cache.set("short_lived", "expires soon", ttl=0.1)  # 100ms TTL
    cache.set("long_lived", "stays around", ttl=3600)

    hit1, _ = cache.get("short_lived")
    print(f"Immediately after set — short_lived hit: {hit1}")

    time.sleep(0.2)  # Wait for TTL to expire

    hit2, _ = cache.get("short_lived")
    hit3, _ = cache.get("long_lived")
    print(f"After 200ms           — short_lived hit: {hit2} (expired)")
    print(f"After 200ms           — long_lived hit:  {hit3} (still valid)")


if __name__ == "__main__":
    basic_cache_demo()
    compression_cache_demo()
    ttl_demo()
    print("\n✅ Cache management example complete!")
