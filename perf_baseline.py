#!/usr/bin/env python3
"""
TokenPak Proxy Performance Baseline Measurement
Run BEFORE applying optimizations to capture baseline metrics.

Tests:
1. BM25 tokenizer throughput (used per vault search)
2. JSON parse overhead per request (6x json.loads in hot path)
3. RouteEngine instantiation cost (per-request vs singleton)
4. VaultIndex search latency
5. compact_text throughput

Usage:
    cd ~/tokenpak && python3 perf_baseline.py
"""
import time
import json
import re
import statistics
import sys
import os

# Add tokenpak to path
sys.path.insert(0, os.path.expanduser("~/tokenpak"))

ITERATIONS = 1000
SAMPLE_TEXT = (
    "Please analyze this code and explain what optimizations could be applied "
    "to improve performance. The function processes a list of user requests and "
    "routes them to the appropriate model based on token count and intent classification. "
    "Consider caching strategies, lazy initialization, and object pooling."
)

SAMPLE_BODY = json.dumps({
    "model": "anthropic/claude-sonnet-4-6",
    "messages": [
        {"role": "user", "content": SAMPLE_TEXT}
    ],
    "stream": False
}).encode()


def measure(name, fn, iterations=ITERATIONS):
    """Run fn for iterations, return (mean_ms, p95_ms, throughput_rps)."""
    times = []
    # Warmup
    for _ in range(10):
        fn()
    # Measure
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    mean_ms = statistics.mean(times)
    p95_ms = sorted(times)[int(0.95 * len(times))]
    rps = 1000 / mean_ms if mean_ms > 0 else 0
    print(f"  {name:<45} mean={mean_ms:.3f}ms  p95={p95_ms:.3f}ms  {rps:.0f} rps")
    return mean_ms, p95_ms, rps


print("=" * 70)
print("TokenPak Proxy — Performance Baseline")
print("=" * 70)
print()

results = {}

# ── 1. BM25 Tokenizer ────────────────────────────────────────────────────────
print("[1] BM25 tokenizer")
def bm25_tokenize(text):
    return re.findall(r'[a-z0-9_]+', text.lower())

results["bm25_raw"] = measure(
    "bm25_tokenize (raw regex, per call)",
    lambda: bm25_tokenize(SAMPLE_TEXT)
)

# With lru_cache
from functools import lru_cache

@lru_cache(maxsize=512)
def bm25_tokenize_cached(text):
    return re.findall(r'[a-z0-9_]+', text.lower())

results["bm25_cached_cold"] = measure(
    "bm25_tokenize_cached (cache miss, unique texts)",
    lambda: bm25_tokenize(SAMPLE_TEXT + " x")  # simulate unique
)
results["bm25_cached_warm"] = measure(
    "bm25_tokenize_cached (cache hit, same text)",
    lambda: bm25_tokenize_cached(SAMPLE_TEXT)
)
print()

# ── 2. JSON Parse ────────────────────────────────────────────────────────────
print("[2] JSON parse overhead")
results["json_parse_1x"] = measure(
    "json.loads once",
    lambda: json.loads(SAMPLE_BODY)
)
results["json_parse_6x"] = measure(
    "json.loads x6 (current hot path)",
    lambda: [json.loads(SAMPLE_BODY) for _ in range(6)]
)
results["json_parse_1x_reuse"] = measure(
    "json.loads once + dict reuse x6 (optimized)",
    lambda: (lambda d: [d] * 6)(json.loads(SAMPLE_BODY))
)
print()

# ── 3. RouteEngine instantiation ─────────────────────────────────────────────
print("[3] RouteEngine instantiation")
try:
    from tokenpak.routing.rules import RouteEngine, RouteStore

    results["route_engine_new"] = measure(
        "RouteEngine() — new instance per call",
        lambda: RouteEngine()
    )

    _singleton = RouteEngine()
    results["route_engine_singleton"] = measure(
        "RouteEngine — singleton reuse",
        lambda: _singleton
    )
    print()
except ImportError as e:
    print(f"  [SKIP] RouteEngine not importable: {e}")
    print()

# ── 4. VaultIndex search ─────────────────────────────────────────────────────
print("[4] VaultIndex BM25 search")
try:
    import proxy_v4
    vault = proxy_v4.VAULT_INDEX
    if vault.available:
        vault.maybe_reload()
        results["vault_search"] = measure(
            "VaultIndex.search (BM25, 5 results)",
            lambda: vault.search(SAMPLE_TEXT, top_k=5),
            iterations=200
        )
    else:
        print("  [SKIP] VaultIndex not available (no blocks loaded)")
except Exception as e:
    print(f"  [SKIP] VaultIndex error: {e}")
print()

# ── 5. compact_text ──────────────────────────────────────────────────────────
print("[5] compact_text pipeline")
try:
    import proxy_v4
    long_text = SAMPLE_TEXT * 5  # simulate longer input
    results["compact_text"] = measure(
        "compact_text (full pipeline)",
        lambda: proxy_v4.compact_text(long_text),
        iterations=200
    )
except Exception as e:
    print(f"  [SKIP] compact_text error: {e}")
print()

# ── Summary ───────────────────────────────────────────────────────────────────
print("=" * 70)
print("BASELINE SUMMARY")
print("=" * 70)

# Key findings
if "route_engine_new" in results and "route_engine_singleton" in results:
    speedup = results["route_engine_new"][0] / max(results["route_engine_singleton"][0], 0.001)
    print(f"  RouteEngine singleton speedup:     {speedup:.1f}x")

if "json_parse_1x" in results and "json_parse_6x" in results:
    wasted_ms = results["json_parse_6x"][0] - results["json_parse_1x"][0]
    print(f"  JSON re-parse waste per request:   {wasted_ms:.3f}ms (5 extra calls)")

if "bm25_cached_warm" in results and "bm25_raw" in results:
    speedup = results["bm25_raw"][0] / max(results["bm25_cached_warm"][0], 0.0001)
    print(f"  BM25 lru_cache speedup:            {speedup:.1f}x")

print()
print("Baseline saved. Run perf_after.py post-optimization for comparison.")
