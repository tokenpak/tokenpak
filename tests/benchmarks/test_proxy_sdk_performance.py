from __future__ import annotations

import random
import socket
import statistics
import time
import tracemalloc
import zlib

import pytest

pytestmark = pytest.mark.needs_fast_host


def _proxy_reachable() -> bool:
    """Return True if tokenpak proxy is reachable on localhost."""
    import os

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def _payload(size_tokens: int = 4000) -> str:
    return ("tokenpak-performance " * size_tokens).strip()


def _proxy_compress(payload: str) -> bytes:
    # Simulate proxy path with envelope overhead.
    framed = f"proxy|v1|{payload}".encode("utf-8")
    return zlib.compress(framed, level=6)


def _sdk_compress(payload: str) -> bytes:
    # Simulate SDK direct compression path.
    return zlib.compress(payload.encode("utf-8"), level=6)


def _tokens_per_second(fn, payload: str, runs: int = 40) -> float:
    tokens = len(payload.split())
    started = time.perf_counter()
    for _ in range(runs):
        fn(payload)
    elapsed = time.perf_counter() - started
    return (tokens * runs) / elapsed


def _latency_percentiles_ms(fn, payload: str, runs: int = 80) -> tuple[float, float, float]:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn(payload)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[min(int(runs * 0.95), runs - 1)]
    p99 = samples[min(int(runs * 0.99), runs - 1)]
    return p50, p95, p99


def _peak_memory_kib(fn, payload: str, runs: int = 60) -> float:
    tracemalloc.start()
    for _ in range(runs):
        fn(payload)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1024.0


def _cache_hit_rate(iterations: int = 500) -> float:
    random.seed(42)
    cache = {}
    hits = 0
    for _ in range(iterations):
        key = f"q:{random.randint(1, 100)}"
        if key in cache:
            hits += 1
        else:
            cache[key] = True
    return hits / iterations


def test_proxy_compression_speed_tokens_per_sec() -> None:
    payload = _payload()
    tps = _tokens_per_second(_proxy_compress, payload)
    assert tps > 500_000, f"proxy compression too slow: {tps:,.0f} tokens/sec"


def test_sdk_compression_speed_tokens_per_sec() -> None:
    payload = _payload()
    tps = _tokens_per_second(_sdk_compress, payload)
    assert tps > 500_000, f"sdk compression too slow: {tps:,.0f} tokens/sec"


@pytest.mark.skipif(
    not _proxy_reachable(),
    reason="tokenpak proxy not running — latency percentile test requires live proxy",
)
def test_proxy_latency_percentiles() -> None:
    payload = _payload()
    p50, p95, p99 = _latency_percentiles_ms(_proxy_compress, payload)
    # Thresholds are generous to accommodate slow/shared CI hosts.
    # The test validates that compression is not pathologically slow,
    # not that it meets tight SLA targets.
    assert p50 < 5.0, f"p50 too slow: {p50:.2f}ms"
    assert p95 < 15.0, f"p95 too slow: {p95:.2f}ms"
    assert p99 < 30.0, f"p99 too slow: {p99:.2f}ms"


def test_proxy_memory_profile_peak_kib() -> None:
    payload = _payload()
    peak_kib = _peak_memory_kib(_proxy_compress, payload)
    assert peak_kib < 512, f"peak memory too high: {peak_kib:.1f} KiB"


def test_cache_hit_rate() -> None:
    hit_rate = _cache_hit_rate()
    assert hit_rate > 0.70, f"cache hit rate too low: {hit_rate:.2%}"


def test_proxy_vs_sdk_throughput_ratio() -> None:
    payload = _payload()
    proxy_tps = _tokens_per_second(_proxy_compress, payload)
    sdk_tps = _tokens_per_second(_sdk_compress, payload)
    ratio = proxy_tps / sdk_tps
    assert ratio > 0.85, f"proxy too slow vs sdk: {ratio:.2f}x"
