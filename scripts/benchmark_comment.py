#!/usr/bin/env python3
"""scripts/benchmark_comment.py — Format benchmark JSON as a GitHub PR comment.

Reads a pytest-benchmark JSON output file and prints a formatted Markdown
comment suitable for posting to a GitHub pull request.

Usage:
    python scripts/benchmark_comment.py benchmark.json
    python scripts/benchmark_comment.py benchmark.json --compare baseline.json

Output: Markdown block printed to stdout. Capture and post via gh CLI:
    python scripts/benchmark_comment.py benchmark.json | gh pr comment $PR --body-file -
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Thresholds (must match check_benchmark_thresholds.py)
# ---------------------------------------------------------------------------

THRESHOLDS: Dict[str, Dict[str, float]] = {
    "small": {"p50_ms": 20.0, "p95_ms": 30.0},
    "medium": {"p50_ms": 30.0, "p95_ms": 50.0},
    "large": {"p50_ms": 50.0, "p95_ms": 100.0},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = min(int(len(sorted_vals) * pct / 100), len(sorted_vals) - 1)
    return sorted_vals[idx]


def _classify_pack(name: str) -> Optional[str]:
    for key in ("small", "medium", "large"):
        if key in name.lower():
            return key
    return None


def _extract_timings_ms(benchmark: dict) -> List[float]:
    stats = benchmark.get("stats", {})
    data = stats.get("data", [])
    if data:
        return [t * 1000.0 for t in data]
    median_s = stats.get("median", 0.0)
    return [median_s * 1000.0] if median_s else []


def _load_benchmarks(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data.get("benchmarks", [])
    except (json.JSONDecodeError, OSError):
        return []


def _short_name(name: str) -> str:
    """Strip class prefix from test name."""
    for prefix in (
        "TestCompilePerformancePlain::",
        "TestSmallPackBenchmark::",
        "TestMediumPackBenchmark::",
        "TestLargePackBenchmark::",
    ):
        name = name.replace(prefix, "")
    return name


# ---------------------------------------------------------------------------
# Comment formatter
# ---------------------------------------------------------------------------

def format_comment(
    benchmarks: List[dict],
    baseline: Optional[List[dict]] = None,
) -> str:
    """Return a Markdown-formatted PR comment string."""
    lines: List[str] = []

    # ── Header ──────────────────────────────────────────────────────────
    any_failure = False
    all_results: List[dict] = []

    for bench in benchmarks:
        name = bench["name"]
        pack_size = _classify_pack(name)
        if pack_size is None:
            continue

        timings = _extract_timings_ms(bench)
        if not timings:
            continue

        p50 = statistics.median(timings)
        p95 = _percentile(timings, 95)
        p99 = _percentile(timings, 99)

        threshold = THRESHOLDS[pack_size]
        p95_ok = p95 < threshold["p95_ms"]
        p50_ok = p50 < threshold["p50_ms"]

        if not p95_ok:
            any_failure = True

        # Find baseline entry for comparison
        delta_p50: Optional[float] = None
        delta_p95: Optional[float] = None
        if baseline:
            base_match = next(
                (b for b in baseline if b["name"] == name), None
            )
            if base_match:
                base_timings = _extract_timings_ms(base_match)
                if base_timings:
                    base_p50 = statistics.median(base_timings)
                    base_p95 = _percentile(base_timings, 95)
                    delta_p50 = p50 - base_p50
                    delta_p95 = p95 - base_p95

        all_results.append({
            "name": _short_name(name),
            "pack_size": pack_size,
            "p50": p50,
            "p95": p95,
            "p99": p99,
            "p95_limit": threshold["p95_ms"],
            "p50_limit": threshold["p50_ms"],
            "p95_ok": p95_ok,
            "p50_ok": p50_ok,
            "delta_p50": delta_p50,
            "delta_p95": delta_p95,
        })

    status_icon = "❌" if any_failure else "✅"
    status_text = "REGRESSION DETECTED" if any_failure else "All thresholds passed"
    lines.append(f"## {status_icon} TokenPak Compile Benchmarks — {status_text}")
    lines.append("")
    lines.append("> Compile latency gates enforced on every PR. p95 breach blocks merge.")
    lines.append("")

    if not all_results:
        lines.append("⚠️ No benchmark data found — was pytest-benchmark installed?")
        return "\n".join(lines)

    # ── Results table ────────────────────────────────────────────────────
    has_delta = any(r["delta_p95"] is not None for r in all_results)

    if has_delta:
        lines.append("| Pack | p50 | p95 | p99 | p95 limit | Δp50 | Δp95 | Status |")
        lines.append("|------|-----|-----|-----|-----------|------|------|--------|")
    else:
        lines.append("| Pack | p50 | p95 | p99 | p95 limit | Status |")
        lines.append("|------|-----|-----|-----|-----------|--------|")

    seen_packs = set()
    for r in all_results:
        # Only show one row per pack (the p95 test is the gate)
        pack_key = r["pack_size"]
        if "p95" not in r["name"]:
            continue
        if pack_key in seen_packs:
            continue
        seen_packs.add(pack_key)

        icon = "✅" if r["p95_ok"] else "❌"
        name_label = r["pack_size"].capitalize()

        def _fmt_delta(d: Optional[float]) -> str:
            if d is None:
                return "—"
            sign = "+" if d >= 0 else ""
            color = "🔴" if d > 1.0 else ("🟡" if d > 0.3 else "🟢")
            return f"{color} {sign}{d:.1f}ms"

        if has_delta:
            lines.append(
                f"| **{name_label}** | {r['p50']:.1f}ms | {r['p95']:.1f}ms | {r['p99']:.1f}ms "
                f"| {r['p95_limit']:.0f}ms | {_fmt_delta(r['delta_p50'])} "
                f"| {_fmt_delta(r['delta_p95'])} | {icon} |"
            )
        else:
            lines.append(
                f"| **{name_label}** | {r['p50']:.1f}ms | {r['p95']:.1f}ms | {r['p99']:.1f}ms "
                f"| {r['p95_limit']:.0f}ms | {icon} |"
            )

    lines.append("")

    # ── Failures detail ──────────────────────────────────────────────────
    failures = [r for r in all_results if not r["p95_ok"]]
    if failures:
        lines.append("### ❌ Threshold Violations")
        lines.append("")
        for r in failures:
            lines.append(
                f"- **{r['pack_size']} pack** p95 = `{r['p95']:.1f}ms` "
                f"(limit: `{r['p95_limit']:.0f}ms`, exceeded by `{r['p95'] - r['p95_limit']:.1f}ms`)"
            )
        lines.append("")
        lines.append("> 🚫 **This PR cannot be merged until latency regressions are fixed.**")
        lines.append("")
        lines.append("**Optimization tips:**")
        lines.append("- Add `@cached_property` to `Block.tokens` to avoid re-counting")
        lines.append("- Cache priority rankings (`_ranked_blocks`) between compiles")
        lines.append("- Use streaming compaction — stop processing once budget is met")
        lines.append("")

    # ── Footer ───────────────────────────────────────────────────────────
    lines.append("---")
    lines.append(
        "_Generated by [TokenPak benchmark CI](/.github/workflows/benchmarks.yml). "
        "Thresholds defined in [`scripts/check_benchmark_thresholds.py`]"
        "(/scripts/check_benchmark_thresholds.py)._"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: List[str]) -> int:
    if not argv:
        print("Usage: benchmark_comment.py <benchmark.json> [--compare baseline.json]", file=sys.stderr)
        return 1

    bench_path = Path(argv[0])
    benchmarks = _load_benchmarks(bench_path)

    if not benchmarks:
        print(f"⚠️  No benchmark data in {bench_path}", file=sys.stderr)
        # Still print a comment so the PR has context
        print("## ⚠️ TokenPak Compile Benchmarks — No Data\n\nNo benchmark data found. "
              "Ensure `pytest-benchmark` is installed and benchmarks ran successfully.")
        return 0

    baseline: Optional[List[dict]] = None
    if "--compare" in argv:
        compare_idx = argv.index("--compare")
        if compare_idx + 1 < len(argv):
            baseline_path = Path(argv[compare_idx + 1])
            baseline = _load_benchmarks(baseline_path)

    comment = format_comment(benchmarks, baseline=baseline)
    print(comment)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
