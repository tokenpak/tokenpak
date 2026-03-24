#!/usr/bin/env python3
"""
TokenPak Proxy Performance AFTER Optimization Measurement
Run AFTER applying optimizations to compare against baseline.
"""
import time
import json
import re
import statistics
import sys
import os
from functools import lru_cache

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
    times = []
    for _ in range(10):
        fn()
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    mean_ms = statistics.mean(times)
    p95_ms = sorted(times)[int(0.95 * len(times))]
    rps = 1000 / mean_ms if mean_ms > 0 else 0
    print(f"  {name:<45} mean={mean_ms:.4f}ms  p95={p95_ms:.4f}ms  {rps:.0f} rps")
    return mean_ms, p95_ms, rps


print("=" * 70)
print("TokenPak Proxy — Performance AFTER Optimization")
print("=" * 70)
print()

results = {}

# ── 1. BM25 Tokenizer with lru_cache (OPT #1) ────────────────────────────────
print("[1] BM25 tokenizer — lru_cache optimization")

# Import optimized version from proxy_v4
try:
    import proxy_v4
    # Cache warm hit
    proxy_v4._bm25_tokenize(SAMPLE_TEXT)  # warmup
    results["bm25_opt_warm"] = measure(
        "_bm25_tokenize (lru_cache warm hit)",
        lambda: proxy_v4._bm25_tokenize(SAMPLE_TEXT)
    )
    # Baseline comparison
    def raw_bm25(text):
        return re.findall(r'[a-z0-9_]+', text.lower())
    results["bm25_raw"] = measure(
        "_bm25_tokenize (raw, no cache)",
        lambda: raw_bm25(SAMPLE_TEXT)
    )
    if results["bm25_raw"][0] > 0:
        speedup = results["bm25_raw"][0] / max(results["bm25_opt_warm"][0], 0.0001)
        print(f"  → Speedup: {speedup:.1f}x")
except Exception as e:
    print(f"  [ERROR] {e}")
print()

# ── 2. JSON parse — single parse with reuse (OPT #4) ─────────────────────────
print("[2] JSON parse — single parse optimization")
results["json_parse_1x"] = measure(
    "json.loads once (optimized)",
    lambda: json.loads(SAMPLE_BODY)
)
results["json_parse_6x"] = measure(
    "json.loads x6 (old behavior)",
    lambda: [json.loads(SAMPLE_BODY) for _ in range(6)]
)
saving_ms = results["json_parse_6x"][0] - results["json_parse_1x"][0]
print(f"  → Saved {saving_ms:.3f}ms per request (eliminated 5 redundant parses)")
print()

# ── 3. RouteEngine singleton (OPT #3) ────────────────────────────────────────
print("[3] RouteEngine singleton")
try:
    from tokenpak.routing.rules import RouteEngine

    # Old: new instance each call
    results["route_engine_new"] = measure(
        "RouteEngine() new per request (OLD)",
        lambda: RouteEngine()
    )

    # New: singleton + cached rules
    from proxy_v4 import _get_route_engine, _get_cached_route_rules
    _get_route_engine()  # init singleton
    _get_cached_route_rules()  # warm cache

    results["route_engine_singleton"] = measure(
        "_get_route_engine() singleton (NEW)",
        lambda: _get_route_engine()
    )
    results["route_rules_cached"] = measure(
        "_get_cached_route_rules() cached (NEW)",
        lambda: _get_cached_route_rules()
    )

    if results["route_engine_new"][0] > 0:
        se = results["route_engine_new"][0] / max(results["route_engine_singleton"][0], 0.0001)
        print(f"  → RouteEngine singleton speedup: {se:.0f}x")

    # Combined: old full flow vs new full flow
    def old_route_flow():
        e = RouteEngine()
        return e.store.list()

    def new_route_flow():
        e = _get_route_engine()
        return _get_cached_route_rules()

    results["old_full_route"] = measure(
        "Full route flow — OLD (new engine + YAML read)",
        old_route_flow
    )
    results["new_full_route"] = measure(
        "Full route flow — NEW (singleton + cached rules)",
        new_route_flow
    )
    speedup = results["old_full_route"][0] / max(results["new_full_route"][0], 0.0001)
    print(f"  → Full route flow speedup: {speedup:.1f}x")

except ImportError as e:
    print(f"  [SKIP] {e}")
print()

# ── 4. Singleton helpers (OPT #2, #3 for gates/budget) ───────────────────────
print("[4] Component singleton helpers")
try:
    from proxy_v4 import _get_precond_gates, _get_budget_controller

    results["precond_singleton"] = measure(
        "_get_precond_gates() (first call = init, rest = return)",
        lambda: _get_precond_gates()
    )
    results["budget_singleton"] = measure(
        "_get_budget_controller() (first call = init, rest = return)",
        lambda: _get_budget_controller()
    )
except Exception as e:
    print(f"  [SKIP] {e}")
print()

# ── Summary ───────────────────────────────────────────────────────────────────
print("=" * 70)
print("OPTIMIZATION SUMMARY")
print("=" * 70)

print()
print("Optimization 1: BM25 lru_cache")
if "bm25_raw" in results and "bm25_opt_warm" in results:
    s = results["bm25_raw"][0] / max(results["bm25_opt_warm"][0], 0.0001)
    print(f"  {s:.0f}x speedup on repeated queries (cache hit)")
    print(f"  {results['bm25_raw'][0]:.4f}ms → {results['bm25_opt_warm'][0]:.4f}ms per call")

print()
print("Optimization 2-3: Component singletons (RouteEngine, PrecondGates, BudgetCtrl)")
if "old_full_route" in results and "new_full_route" in results:
    s = results["old_full_route"][0] / max(results["new_full_route"][0], 0.0001)
    saved = results["old_full_route"][0] - results["new_full_route"][0]
    print(f"  {s:.1f}x speedup on routing phase")
    print(f"  {results['old_full_route'][0]:.3f}ms → {results['new_full_route'][0]:.3f}ms per request")
    print(f"  Saves {saved:.3f}ms per routed request")

print()
print("Optimization 4: Single JSON parse per request")
if "json_parse_1x" in results and "json_parse_6x" in results:
    saved = results["json_parse_6x"][0] - results["json_parse_1x"][0]
    print(f"  Saves {saved:.3f}ms per request (5 redundant json.loads eliminated)")

print()
print("Optimization 5: Route rules caching (mtime-guarded YAML cache)")
if "route_rules_cached" in results and "old_full_route" in results:
    saved = results["old_full_route"][0] - results["new_full_route"][0]
    print(f"  YAML file not re-read for {int(5000)}ms (TTL=5s)")
    print(f"  Saves ~{saved:.3f}ms on every routing call during TTL window")
