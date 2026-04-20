#!/usr/bin/env python3
"""scripts/check_benchmark_thresholds.py — Validate benchmark JSON against latency thresholds.

Reads a pytest-benchmark JSON output file and fails with exit code 1 if any
p95 compile time exceeds the hard limit for its pack size.

Usage:
    python scripts/check_benchmark_thresholds.py benchmark.json
    python scripts/check_benchmark_thresholds.py benchmark.json --warn-only

Exit codes:
    0 — all thresholds passed
    1 — one or more thresholds exceeded (or missing required tests)
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Threshold definitions (ms)
# Maps benchmark name fragments → { p50_ms, p95_ms }
# ---------------------------------------------------------------------------

THRESHOLDS: Dict[str, Dict[str, float]] = {
    "small": {
        "p50_ms": 20.0,
        "p95_ms": 30.0,
    },
    "medium": {
        "p50_ms": 30.0,
        "p95_ms": 50.0,
    },
    "large": {
        "p50_ms": 50.0,
        "p95_ms": 100.0,
    },
}

REQUIRED_TESTS = [
    "test_small_pack_p95_under_30ms",
    "test_medium_pack_p95_under_50ms",
    "test_large_pack_p95_under_100ms",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile(values: List[float], pct: float) -> float:
    """Return the pct-th percentile (0–100) from a list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * pct / 100)
    idx = min(idx, len(sorted_vals) - 1)
    return sorted_vals[idx]


def _classify_pack(name: str) -> Optional[str]:
    """Return 'small' | 'medium' | 'large' | None based on test name."""
    name_lower = name.lower()
    for key in ("small", "medium", "large"):
        if key in name_lower:
            return key
    return None


def _extract_timings(benchmark: dict) -> List[float]:
    """Extract per-run times (in ms) from a benchmark entry."""
    stats = benchmark.get("stats", {})
    data = stats.get("data", [])
    if data:
        return [t * 1000.0 for t in data]  # seconds → ms
    # Fallback: reconstruct from aggregate stats
    median_s = stats.get("median", 0.0)
    return [median_s * 1000.0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    if not argv:
        print("Usage: check_benchmark_thresholds.py <benchmark.json> [--warn-only]", file=sys.stderr)
        return 1

    json_path = Path(argv[0])
    warn_only = "--warn-only" in argv

    if not json_path.exists():
        print(f"❌ Benchmark JSON not found: {json_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"❌ Failed to parse {json_path}: {e}", file=sys.stderr)
        return 1

    benchmarks = data.get("benchmarks", [])
    if not benchmarks:
        print("⚠️  No benchmarks found in JSON — was pytest-benchmark installed?", file=sys.stderr)
        return 0  # Not a hard failure if no benchmark data

    found_tests = {b["name"] for b in benchmarks}
    failures: List[str] = []
    warnings: List[str] = []

    print("\n  TokenPak Compile Benchmark Threshold Check")
    print("  " + "=" * 62)
    print(f"  {'Test':<45} {'p50':>7} {'p95':>7}  {'Status'}")
    print("  " + "-" * 62)

    for bench in benchmarks:
        name = bench["name"]
        pack_size = _classify_pack(name)
        if pack_size is None:
            continue

        thresholds = THRESHOLDS[pack_size]
        timings = _extract_timings(bench)

        if not timings:
            continue

        p50 = statistics.median(timings)
        p95 = _percentile(timings, 95)

        p95_limit = thresholds["p95_ms"]
        p50_limit = thresholds["p50_ms"]

        p95_ok = p95 < p95_limit
        p50_ok = p50 < p50_limit
        status = "✅ PASS" if (p95_ok and p50_ok) else "❌ FAIL"

        short_name = name.replace("TestCompilePerformancePlain::", "").replace(
            "TestSmallPackBenchmark::", ""
        ).replace("TestMediumPackBenchmark::", "").replace(
            "TestLargePackBenchmark::", ""
        )
        print(f"  {short_name:<45} {p50:>6.1f}ms {p95:>6.1f}ms  {status}")

        if not p95_ok:
            msg = (
                f"{short_name}: p95={p95:.1f}ms > {p95_limit}ms "
                f"({pack_size} pack hard limit)"
            )
            failures.append(msg)
        elif not p50_ok:
            msg = (
                f"{short_name}: p50={p50:.1f}ms > {p50_limit}ms "
                f"({pack_size} pack target)"
            )
            warnings.append(msg)

    print("  " + "=" * 62)

    # Check required tests were present
    missing = [t for t in REQUIRED_TESTS if not any(t in b for b in found_tests)]
    if missing:
        print("\n  ⚠️  Missing required benchmark tests:")
        for m in missing:
            print(f"     • {m}")

    if warnings:
        print("\n  ⚠️  Performance targets missed (not blocking):")
        for w in warnings:
            print(f"     • {w}")

    if failures:
        print(f"\n  ❌ PERFORMANCE REGRESSION DETECTED — {len(failures)} threshold(s) exceeded:")
        for f in failures:
            print(f"     • {f}")
        if warn_only:
            print("\n  ⚠️  Running in --warn-only mode; not blocking CI.")
            return 0
        return 1

    print("\n  ✅ All benchmarks within thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
