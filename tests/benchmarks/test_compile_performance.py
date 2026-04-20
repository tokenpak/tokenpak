"""tests/benchmarks/test_compile_performance.py — CI-enforced compile-time benchmarks.

Ensures TokenPak compilation stays within latency targets across pack sizes:

  Pack size   | p50 target | p95 hard limit
  ------------|------------|---------------
  Small       | < 20ms     | < 30ms  (blocks merge)
  Medium      | < 30ms     | < 50ms  (blocks merge)
  Large       | < 50ms     | < 100ms (alert only)

Run manually:
    pytest tests/benchmarks/ -v --benchmark-json=benchmark.json

Run with regression enforcement:
    pytest tests/benchmarks/ --benchmark-json=benchmark.json \\
        --benchmark-fail-on-regression --benchmark-compare

CI usage:
    pytest tests/benchmarks/ -v -m benchmark --benchmark-json=benchmark.json
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.pack", reason="module not available in current build")
import statistics
import time
from typing import List

import pytest

from tokenpak.pack import ContextPack, PackBlock

from .conftest import make_large_pack, make_medium_pack, make_small_pack

# ---------------------------------------------------------------------------
# Thresholds (all values in milliseconds)
# ---------------------------------------------------------------------------

THRESHOLDS = {
    "small": {
        "p50_ms": 20.0,
        "p95_ms": 30.0,  # hard CI gate — blocks merge on breach
    },
    "medium": {
        "p50_ms": 30.0,
        "p95_ms": 50.0,  # hard CI gate — blocks merge on breach
    },
    "large": {
        "p50_ms": 50.0,
        "p95_ms": 100.0,  # hard CI gate — blocks merge on breach
    },
}

N_RUNS_SMALL = 100
N_RUNS_MEDIUM = 100
N_RUNS_LARGE = 50


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compile_times_ms(pack: ContextPack, n: int) -> List[float]:
    """Run compile() n times on fresh copies; return latencies in ms."""
    times: List[float] = []
    blocks_snapshot = [
        PackBlock(
            id=b.id,
            type=b.type,
            content=b.content,
            priority=b.priority,
            quality=b.quality,
            max_tokens=b.max_tokens,
        )
        for b in pack._blocks
    ]
    for _ in range(n):
        fresh = ContextPack(
            budget=pack.budget,
            quality_threshold=pack.quality_threshold,
            separator=pack.separator,
        )
        for b in blocks_snapshot:
            fresh.add(b)
        t0 = time.perf_counter()
        fresh.compile()
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def _percentile(values: List[float], pct: float) -> float:
    """Return the pct-th percentile (0–100) from a list."""
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def _fmt_stats(times: List[float]) -> str:
    p50 = statistics.median(times)
    p95 = _percentile(times, 95)
    p99 = _percentile(times, 99)
    return (
        f"min={min(times):.1f}ms  p50={p50:.1f}ms  "
        f"p95={p95:.1f}ms  p99={p99:.1f}ms  max={max(times):.1f}ms"
    )


# ---------------------------------------------------------------------------
# pytest-benchmark integration
# If pytest-benchmark is installed, these run with full benchmark harness.
# If not, they fall back to the plain timing helpers above.
# ---------------------------------------------------------------------------


class TestSmallPackBenchmark:
    """Small pack: ~500 tokens, 2-3 blocks, no compaction."""

    @pytest.mark.benchmark(group="compile", min_rounds=100)
    def test_small_pack_p50_under_20ms(self, benchmark):
        """p50 compile time for a small pack must be < 20ms."""
        pack = make_small_pack()

        def compile_once():
            fresh = ContextPack(budget=pack.budget)
            for b in pack._blocks:
                fresh.add(
                    PackBlock(
                        id=b.id,
                        type=b.type,
                        content=b.content,
                        priority=b.priority,
                        quality=b.quality,
                        max_tokens=b.max_tokens,
                    )
                )
            fresh.compile()

        benchmark(compile_once)
        # median in seconds → ms
        p50_ms = benchmark.stats["median"] * 1000.0
        assert p50_ms < THRESHOLDS["small"]["p50_ms"], (
            f"Small pack p50={p50_ms:.1f}ms exceeds {THRESHOLDS['small']['p50_ms']}ms target"
        )

    @pytest.mark.benchmark(group="compile", min_rounds=100)
    def test_small_pack_p95_under_30ms(self, benchmark):
        """p95 compile time for a small pack must be < 30ms (CI gate)."""
        pack = make_small_pack()

        def compile_once():
            fresh = ContextPack(budget=pack.budget)
            for b in pack._blocks:
                fresh.add(
                    PackBlock(
                        id=b.id,
                        type=b.type,
                        content=b.content,
                        priority=b.priority,
                        quality=b.quality,
                        max_tokens=b.max_tokens,
                    )
                )
            fresh.compile()

        benchmark.pedantic(compile_once, rounds=100, iterations=1)
        p95_ms = _percentile([t * 1000.0 for t in benchmark.stats["data"]], 95)
        assert p95_ms < THRESHOLDS["small"]["p95_ms"], (
            f"Small pack p95={p95_ms:.1f}ms exceeds {THRESHOLDS['small']['p95_ms']}ms hard limit — "
            f"this would block merge"
        )


class TestMediumPackBenchmark:
    """Medium pack: ~5,000 tokens, 10 blocks, compaction to 4,000."""

    @pytest.mark.benchmark(group="compile", min_rounds=100)
    def test_medium_pack_p50_under_30ms(self, benchmark):
        """p50 compile time for a medium pack must be < 30ms."""
        pack = make_medium_pack()

        def compile_once():
            fresh = ContextPack(budget=pack.budget)
            for b in pack._blocks:
                fresh.add(
                    PackBlock(
                        id=b.id,
                        type=b.type,
                        content=b.content,
                        priority=b.priority,
                        quality=b.quality,
                        max_tokens=b.max_tokens,
                    )
                )
            fresh.compile()

        benchmark(compile_once)
        p50_ms = benchmark.stats["median"] * 1000.0
        assert p50_ms < THRESHOLDS["medium"]["p50_ms"], (
            f"Medium pack p50={p50_ms:.1f}ms exceeds {THRESHOLDS['medium']['p50_ms']}ms target"
        )

    @pytest.mark.benchmark(group="compile", min_rounds=100)
    def test_medium_pack_p95_under_50ms(self, benchmark):
        """p95 compile time for a medium pack must be < 50ms (CI gate)."""
        pack = make_medium_pack()

        def compile_once():
            fresh = ContextPack(budget=pack.budget)
            for b in pack._blocks:
                fresh.add(
                    PackBlock(
                        id=b.id,
                        type=b.type,
                        content=b.content,
                        priority=b.priority,
                        quality=b.quality,
                        max_tokens=b.max_tokens,
                    )
                )
            fresh.compile()

        benchmark.pedantic(compile_once, rounds=100, iterations=1)
        p95_ms = _percentile([t * 1000.0 for t in benchmark.stats["data"]], 95)
        assert p95_ms < THRESHOLDS["medium"]["p95_ms"], (
            f"Medium pack p95={p95_ms:.1f}ms exceeds {THRESHOLDS['medium']['p95_ms']}ms hard limit — "
            f"this would block merge"
        )


class TestLargePackBenchmark:
    """Large pack: ~50,000 tokens, 50 blocks, heavy compaction to 8,000 (84% reduction)."""

    @pytest.mark.benchmark(group="compile", min_rounds=50)
    def test_large_pack_p50_under_50ms(self, benchmark):
        """p50 compile time for a large pack must be < 50ms."""
        pack = make_large_pack()

        def compile_once():
            fresh = ContextPack(budget=pack.budget)
            for b in pack._blocks:
                fresh.add(
                    PackBlock(
                        id=b.id,
                        type=b.type,
                        content=b.content,
                        priority=b.priority,
                        quality=b.quality,
                        max_tokens=b.max_tokens,
                    )
                )
            fresh.compile()

        benchmark(compile_once)
        p50_ms = benchmark.stats["median"] * 1000.0
        assert p50_ms < THRESHOLDS["large"]["p50_ms"], (
            f"Large pack p50={p50_ms:.1f}ms exceeds {THRESHOLDS['large']['p50_ms']}ms target"
        )

    @pytest.mark.benchmark(group="compile", min_rounds=50)
    def test_large_pack_p95_under_100ms(self, benchmark):
        """p95 compile time for a large pack must be < 100ms (CI gate)."""
        pack = make_large_pack()

        def compile_once():
            fresh = ContextPack(budget=pack.budget)
            for b in pack._blocks:
                fresh.add(
                    PackBlock(
                        id=b.id,
                        type=b.type,
                        content=b.content,
                        priority=b.priority,
                        quality=b.quality,
                        max_tokens=b.max_tokens,
                    )
                )
            fresh.compile()

        benchmark.pedantic(compile_once, rounds=50, iterations=1)
        p95_ms = _percentile([t * 1000.0 for t in benchmark.stats["data"]], 95)
        assert p95_ms < THRESHOLDS["large"]["p95_ms"], (
            f"Large pack p95={p95_ms:.1f}ms exceeds {THRESHOLDS['large']['p95_ms']}ms hard limit — "
            f"this would block merge"
        )


# ---------------------------------------------------------------------------
# Fallback: plain timing tests (run without pytest-benchmark installed)
# These are always collected and enforce the same thresholds.
# ---------------------------------------------------------------------------


class TestCompilePerformancePlain:
    """Plain-timing fallback — runs without pytest-benchmark.

    These tests are ALWAYS executed (not marked @benchmark) to ensure
    latency gates are enforced even in lightweight CI environments.
    """

    def test_small_pack_p50_under_20ms(self):
        """Small pack: p50 < 20ms."""
        times = _compile_times_ms(make_small_pack(), N_RUNS_SMALL)
        p50 = statistics.median(times)
        assert p50 < THRESHOLDS["small"]["p50_ms"], (
            f"Small pack p50={p50:.1f}ms — {_fmt_stats(times)}"
        )

    def test_small_pack_p95_under_30ms(self):
        """Small pack: p95 < 30ms (hard CI gate)."""
        times = _compile_times_ms(make_small_pack(), N_RUNS_SMALL)
        p95 = _percentile(times, 95)
        assert p95 < THRESHOLDS["small"]["p95_ms"], (
            f"Small pack p95={p95:.1f}ms — {_fmt_stats(times)}"
        )

    def test_medium_pack_p50_under_30ms(self):
        """Medium pack: p50 < 30ms."""
        times = _compile_times_ms(make_medium_pack(), N_RUNS_MEDIUM)
        p50 = statistics.median(times)
        assert p50 < THRESHOLDS["medium"]["p50_ms"], (
            f"Medium pack p50={p50:.1f}ms — {_fmt_stats(times)}"
        )

    def test_medium_pack_p95_under_50ms(self):
        """Medium pack: p95 < 50ms (hard CI gate)."""
        times = _compile_times_ms(make_medium_pack(), N_RUNS_MEDIUM)
        p95 = _percentile(times, 95)
        assert p95 < THRESHOLDS["medium"]["p95_ms"], (
            f"Medium pack p95={p95:.1f}ms — {_fmt_stats(times)}"
        )

    def test_large_pack_p50_under_50ms(self):
        """Large pack: p50 < 50ms."""
        times = _compile_times_ms(make_large_pack(), N_RUNS_LARGE)
        p50 = statistics.median(times)
        assert p50 < THRESHOLDS["large"]["p50_ms"], (
            f"Large pack p50={p50:.1f}ms — {_fmt_stats(times)}"
        )

    def test_large_pack_p95_under_100ms(self):
        """Large pack: p95 < 100ms (hard CI gate)."""
        times = _compile_times_ms(make_large_pack(), N_RUNS_LARGE)
        p95 = _percentile(times, 95)
        assert p95 < THRESHOLDS["large"]["p95_ms"], (
            f"Large pack p95={p95:.1f}ms — {_fmt_stats(times)}"
        )

    def test_all_three_packs_summary(self, capsys):
        """Print a full latency summary table for all three pack sizes."""
        results = {}
        for name, factory, n in [
            ("small", make_small_pack, N_RUNS_SMALL),
            ("medium", make_medium_pack, N_RUNS_MEDIUM),
            ("large", make_large_pack, N_RUNS_LARGE),
        ]:
            times = _compile_times_ms(factory(), n)
            results[name] = {
                "p50": statistics.median(times),
                "p95": _percentile(times, 95),
                "p99": _percentile(times, 99),
                "min": min(times),
                "max": max(times),
            }

        print("\n\n  TokenPak Compile Latency Report")
        print("  " + "=" * 60)
        print(f"  {'Pack':<10} {'p50':>8} {'p95':>8} {'p99':>8} {'min':>8} {'max':>8}")
        print("  " + "-" * 60)
        for name, stats in results.items():
            limit = THRESHOLDS[name]["p95_ms"]
            flag = "✅" if stats["p95"] < limit else "❌"
            print(
                f"  {name:<10} {stats['p50']:>7.1f}ms {stats['p95']:>7.1f}ms "
                f"{stats['p99']:>7.1f}ms {stats['min']:>7.1f}ms {stats['max']:>7.1f}ms {flag}"
            )
        print("  " + "=" * 60)

        # All p95 thresholds must be met
        for name, stats in results.items():
            assert stats["p95"] < THRESHOLDS[name]["p95_ms"], (
                f"{name} pack p95={stats['p95']:.1f}ms exceeds "
                f"{THRESHOLDS[name]['p95_ms']}ms hard limit"
            )
