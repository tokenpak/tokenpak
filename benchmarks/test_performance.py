"""
TokenPak Performance Benchmark Suite
=====================================

Scenarios:
  1. Compression benchmark — 10 sample files across code/data/text types
  2. Token counting — cold vs warm cache
  3. Indexing — optimized throughput (files/sec)
  4. Indexing baseline vs optimized comparison (13.72x speedup)
  5. Search latency
  6. Proxy live-session stats (requires running proxy at localhost:8766)

Run:
  pytest benchmarks/test_performance.py -v
  pytest benchmarks/test_performance.py -v -m proxy  # proxy-dependent only
  pytest benchmarks/test_performance.py -v -m "not proxy"  # no proxy needed

Generates: benchmarks/results.md
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
BENCH_DIR = Path(__file__).parent
TOKENPAK_ROOT = BENCH_DIR.parent  # ~/Projects/tokenpak
RESULTS_PATH = BENCH_DIR / "results.md"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def benchmark_dir():
    """Return the tokenpak source directory for full indexing benchmarks."""
    return str(TOKENPAK_ROOT)


@pytest.fixture(scope="session")
def compression_results():
    """Run compression benchmark once and cache results for the session."""
    from tokenpak.benchmark import run_compression_benchmark
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        run_compression_benchmark(as_json=True)

    return json.loads(buf.getvalue())


# ── Scenario 1: Compression ────────────────────────────────────────────────────

class TestCompressionBenchmark:
    """Scenario 1: Compression effectiveness across file types."""

    def test_overall_compression_above_threshold(self, compression_results):
        """Overall token reduction should be ≥ 30%."""
        ratio = compression_results["summary"]["overall_compression_pct"]
        assert ratio >= 30.0, f"Expected ≥30% compression, got {ratio}%"

    def test_all_samples_processed(self, compression_results):
        """All 10 built-in samples should be processed."""
        assert compression_results["summary"]["total_tests"] == 10

    def test_compression_variance_acceptable(self, compression_results):
        """Compression ratio should be stable across 3 runs (±5%)."""
        from tokenpak.benchmark import run_compression_benchmark
        import io
        from contextlib import redirect_stdout

        runs = []
        for _ in range(3):
            buf = io.StringIO()
            with redirect_stdout(buf):
                run_compression_benchmark(as_json=True)
            r = json.loads(buf.getvalue())
            runs.append(r["summary"]["overall_compression_pct"])

        stdev = statistics.stdev(runs)
        assert stdev <= 5.0, f"Compression variance too high: stdev={stdev:.2f}%"

    def test_code_files_compress_well(self, compression_results):
        """Python and JS files should compress ≥ 20%."""
        code_tests = [
            t for t in compression_results["tests"]
            if t["file_type"] == "code"
            and t["name"] not in ("shell_script",)  # shell is intentionally 0%
        ]
        for t in code_tests:
            assert t["compression_ratio_pct"] >= 20.0, (
                f"{t['name']} only compressed {t['compression_ratio_pct']}%"
            )

    def test_data_files_compress_well(self, compression_results):
        """YAML/JSON/CI files should compress ≥ 25%."""
        data_tests = [t for t in compression_results["tests"] if t["file_type"] == "data"]
        for t in data_tests:
            assert t["compression_ratio_pct"] >= 25.0, (
                f"{t['name']} only compressed {t['compression_ratio_pct']}%"
            )

    def test_processing_time_per_file(self, compression_results):
        """Each file should process in < 50ms (regex is fast)."""
        for t in compression_results["tests"]:
            assert t["time_ms"] < 50.0, (
                f"{t['name']} took {t['time_ms']}ms (> 50ms threshold)"
            )

    def test_recipe_hits_populated(self, compression_results):
        """Most files should match at least 3 recipes."""
        matched = [
            t for t in compression_results["tests"]
            if len(t["recipe_hits"]) >= 3
        ]
        total = len(compression_results["tests"])
        assert len(matched) >= total * 0.7, (
            f"Only {len(matched)}/{total} files matched ≥3 recipes"
        )


# ── Scenario 2: Token Counting Cache ─────────────────────────────────────────

class TestTokenCountingCache:
    """Scenario 2: Token counting cold vs warm cache performance."""

    def test_cache_speedup_significant(self):
        """Warm cache should be at least 100x faster than cold cache."""
        from tokenpak.benchmark import benchmark_tokenization

        sample_texts = [
            "def hello():\n    return 'world'\n" * 50,
            "import os\nimport sys\n" * 30,
            "# Comment\n" * 100,
        ]

        results = benchmark_tokenization(sample_texts, iterations=3)
        assert results["cache_speedup"] >= 100.0, (
            f"Cache speedup only {results['cache_speedup']:.1f}x (expected ≥100x)"
        )

    def test_cold_cache_completes(self):
        """Cold cache token counting should complete in reasonable time."""
        from tokenpak.benchmark import benchmark_tokenization

        texts = ["some content " * 200] * 10
        results = benchmark_tokenization(texts, iterations=1)
        assert results["cold_cache_avg_ms"] < 30000, (
            f"Cold cache took {results['cold_cache_avg_ms']:.0f}ms"
        )

    def test_warm_cache_is_fast(self):
        """Warm cache should process texts in under 50ms total."""
        from tokenpak.benchmark import benchmark_tokenization

        texts = ["sample text content " * 100] * 20
        results = benchmark_tokenization(texts, iterations=3)
        assert results["warm_cache_avg_ms"] < 50.0, (
            f"Warm cache took {results['warm_cache_avg_ms']:.2f}ms (expected <50ms)"
        )


# ── Scenario 3: Indexing Throughput ──────────────────────────────────────────

class TestIndexingThroughput:
    """Scenario 3: Indexing files/sec with optimized path."""

    def test_indexing_throughput_acceptable(self, benchmark_dir):
        """Optimized indexing should exceed 100 files/sec."""
        from tokenpak.benchmark import benchmark_indexing_optimized

        results = benchmark_indexing_optimized(benchmark_dir, iterations=1)
        fps = results["files_per_second"]
        assert fps >= 100.0, f"Throughput {fps:.1f} files/sec (expected ≥100)"

    def test_indexing_per_file_latency(self, benchmark_dir):
        """Each file should index in under 20ms on average."""
        from tokenpak.benchmark import benchmark_indexing_optimized

        results = benchmark_indexing_optimized(benchmark_dir, iterations=1)
        per_file = results["per_file_ms"]
        assert per_file < 20.0, f"Per-file latency {per_file:.3f}ms (expected <20ms)"

    def test_indexing_processes_files(self, benchmark_dir):
        """Indexing run should process at least 10 files."""
        from tokenpak.benchmark import benchmark_indexing_optimized

        results = benchmark_indexing_optimized(benchmark_dir, iterations=1)
        assert results["total_files"] >= 10, (
            f"Only {results['total_files']} files indexed"
        )


# ── Scenario 4: Baseline vs Optimized ────────────────────────────────────────

class TestBaselineVsOptimized:
    """Scenario 4: Verify optimized indexing is significantly faster than baseline."""

    def test_optimized_faster_than_baseline(self, benchmark_dir):
        """Optimized indexing should be at least 5x faster than baseline."""
        from tokenpak.benchmark import benchmark_indexing_baseline, benchmark_indexing_optimized

        baseline = benchmark_indexing_baseline(benchmark_dir, iterations=1)
        optimized = benchmark_indexing_optimized(benchmark_dir, iterations=1)

        speedup = baseline["total_ms"] / max(optimized["total_ms"], 0.001)
        assert speedup >= 5.0, (
            f"Speedup only {speedup:.2f}x "
            f"(baseline={baseline['total_ms']:.0f}ms, "
            f"optimized={optimized['total_ms']:.0f}ms)"
        )

    def test_optimized_throughput_exceeds_baseline(self, benchmark_dir):
        """Optimized throughput (files/sec) should exceed baseline by 5x."""
        from tokenpak.benchmark import benchmark_indexing_baseline, benchmark_indexing_optimized

        baseline = benchmark_indexing_baseline(benchmark_dir, iterations=1)
        optimized = benchmark_indexing_optimized(benchmark_dir, iterations=1)

        ratio = optimized["files_per_second"] / max(baseline["files_per_second"], 0.001)
        assert ratio >= 5.0, (
            f"Throughput ratio only {ratio:.2f}x "
            f"(baseline={baseline['files_per_second']:.1f}, "
            f"optimized={optimized['files_per_second']:.1f} files/sec)"
        )


# ── Scenario 5: Search Latency ────────────────────────────────────────────────

class TestSearchLatency:
    """Scenario 5: Search query latency."""

    def test_search_latency_per_query(self, benchmark_dir):
        """Each search query should complete in under 500ms."""
        from tokenpak.benchmark import benchmark_search
        from tokenpak.registry import Block, BlockRegistry
        from tokenpak.processors import get_processor
        from tokenpak.tokens import count_tokens
        from tokenpak.walker import walk_directory
        import hashlib, tempfile

        queries = ["import", "function", "class", "def", "return"]

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/search_bench.db"
            registry = BlockRegistry(db_path)

            files = list(walk_directory(benchmark_dir))[:50]
            with registry.batch_transaction() as conn:
                for path, file_type, _ in files:
                    try:
                        content = Path(path).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    processor = get_processor(file_type)
                    if not processor:
                        continue
                    compressed = processor.process(content, path)
                    block = Block(
                        path=path,
                        content_hash=hashlib.sha256(content.encode()).hexdigest(),
                        version=1,
                        file_type=file_type,
                        raw_tokens=count_tokens(content),
                        compressed_tokens=count_tokens(compressed),
                        compressed_content=compressed,
                        quality_score=1.0,
                        importance=5.0,
                    )
                    registry.add_block_batch(block, conn)

            results = benchmark_search(registry, queries, iterations=3)
            registry.close()

        per_query = results["per_query_ms"]
        assert per_query < 500.0, f"Search latency {per_query:.1f}ms/query (expected <500ms)"


# ── Scenario 6: Proxy Live Stats (requires running proxy) ─────────────────────

@pytest.mark.proxy
class TestProxyLiveStats:
    """Scenario 6: Validate live proxy stats from running instance."""

    PROXY_URL = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")

    @pytest.fixture(scope="class")
    def proxy_stats(self):
        """Fetch live stats from the running proxy."""
        import urllib.request
        import urllib.error

        try:
            with urllib.request.urlopen(f"{self.PROXY_URL}/stats", timeout=5) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, OSError):
            pytest.skip("TokenPak proxy not running at localhost:8766")

    def test_proxy_is_healthy(self):
        """Proxy /health endpoint returns ok."""
        import urllib.request
        import urllib.error

        try:
            with urllib.request.urlopen(f"{self.PROXY_URL}/health", timeout=5) as resp:
                data = json.loads(resp.read())
                assert data.get("status") == "ok"
        except (urllib.error.URLError, OSError):
            pytest.skip("Proxy not running")

    def test_cache_hit_rate_above_threshold(self, proxy_stats):
        """Live cache hit rate should be ≥ 70%."""
        s = proxy_stats["session"]
        total = s["cache_hits"] + s["cache_misses"]
        if total < 10:
            pytest.skip("Not enough requests to assess cache hit rate")
        hit_rate = s["cache_hits"] / total * 100
        assert hit_rate >= 70.0, f"Cache hit rate {hit_rate:.1f}% (expected ≥70%)"

    def test_token_reduction_reported(self, proxy_stats):
        """Proxy should report token savings > 0."""
        s = proxy_stats["session"]
        assert s["saved_tokens"] > 0, "No token savings reported"

    def test_proxy_error_rate_acceptable(self, proxy_stats):
        """Error rate should be under 5%."""
        s = proxy_stats["session"]
        if s["requests"] < 10:
            pytest.skip("Not enough requests to assess error rate")
        error_rate = s["errors"] / s["requests"] * 100
        assert error_rate < 5.0, f"Error rate {error_rate:.1f}% (expected <5%)"


# ── Report Generator ──────────────────────────────────────────────────────────

def generate_results_report(
    compression: dict,
    token_bench: dict,
    indexing: dict,
    compare: dict | None = None,
    proxy: dict | None = None,
) -> str:
    """Generate markdown results report from collected benchmark data."""
    now = time.strftime("%Y-%m-%d %H:%M")
    s = compression["summary"]
    lines = [
        f"# TokenPak Performance Benchmark Results",
        f"",
        f"**Generated:** {now}  ",
        f"**Environment:** {sys.platform} | Python {sys.version.split()[0]}",
        f"",
        f"---",
        f"",
        f"## Scenario 1: Compression Effectiveness",
        f"",
        f"| Test | Type | Tokens Before | Tokens After | Saved | Ratio |",
        f"|------|------|--------------|--------------|-------|-------|",
    ]

    for t in compression["tests"]:
        lines.append(
            f"| {t['name']} | {t['file_type']} "
            f"| {t['tokens_before']:,} | {t['tokens_after']:,} "
            f"| {t['tokens_saved']:,} | {t['compression_ratio_pct']}% |"
        )

    lines += [
        f"| **TOTAL** | | **{s['tokens_before']:,}** | **{s['tokens_after']:,}** "
        f"| **{s['tokens_saved']:,}** | **{s['overall_compression_pct']}%** |",
        f"",
        f"- Average processing time: {s['avg_time_ms']:.2f}ms/file",
        f"",
    ]

    lines += [
        f"## Scenario 2: Token Counting Cache",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Cold cache (avg) | {token_bench['cold_cache_avg_ms']:.2f}ms |",
        f"| Warm cache (avg) | {token_bench['warm_cache_avg_ms']:.2f}ms |",
        f"| Cache speedup | **{token_bench['cache_speedup']:.0f}x** |",
        f"",
    ]

    lines += [
        f"## Scenario 3 & 4: Indexing Performance",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total files | {indexing['total_files']:,} |",
        f"| Total time | {indexing['total_ms']:.0f}ms |",
        f"| Per-file latency | {indexing['per_file_ms']:.3f}ms |",
        f"| Throughput | **{indexing['files_per_second']:.1f} files/sec** |",
    ]

    if compare:
        speedup = compare.get("speedup", 0)
        lines += [
            f"| Baseline throughput | {compare['baseline_fps']:.1f} files/sec |",
            f"| Optimized speedup | **{speedup:.1f}x faster** |",
        ]

    lines += [f""]

    if proxy:
        session = proxy.get("session", {})
        today = proxy.get("today", {})
        total = session.get("cache_hits", 0) + session.get("cache_misses", 0)
        hit_rate = round(session.get("cache_hits", 0) / max(total, 1) * 100, 1)
        saved_pct = round(
            session.get("saved_tokens", 0) / max(session.get("input_tokens", 1), 1) * 100, 1
        )
        lines += [
            f"## Scenario 5: Live Proxy Metrics (Session)",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total requests | {session.get('requests', 0):,} |",
            f"| Cache hit rate | **{hit_rate}%** |",
            f"| Token reduction | **{saved_pct}%** |",
            f"| Avg latency (today) | {today.get('avg_latency_ms', 'N/A')}ms |",
            f"| Total cost | ${session.get('cost', 0):.2f} |",
            f"| Error rate | {round(session.get('errors', 0) / max(session.get('requests', 1), 1) * 100, 2)}% |",
            f"",
        ]

    lines += [
        f"---",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Result | Target | Status |",
        f"|--------|--------|--------|--------|",
        f"| Token reduction | {s['overall_compression_pct']}% | ≥30% | {'✅' if s['overall_compression_pct'] >= 30 else '❌'} |",
        f"| Cache speedup | {token_bench['cache_speedup']:.0f}x | ≥100x | {'✅' if token_bench['cache_speedup'] >= 100 else '❌'} |",
        f"| Indexing throughput | {indexing['files_per_second']:.0f} files/sec | ≥100 | {'✅' if indexing['files_per_second'] >= 100 else '❌'} |",
        f"| Per-file latency | {indexing['per_file_ms']:.3f}ms | <20ms | {'✅' if indexing['per_file_ms'] < 20 else '❌'} |",
    ]

    return "\n".join(lines) + "\n"


# ── CLI entrypoint: generate results.md ──────────────────────────────────────

if __name__ == "__main__":
    import urllib.request
    import io
    from contextlib import redirect_stdout
    from tokenpak.benchmark import (
        benchmark_tokenization,
        benchmark_indexing_baseline,
        benchmark_indexing_optimized,
        run_compression_benchmark,
    )
    from tokenpak.walker import walk_directory

    bench_dir = sys.argv[1] if len(sys.argv) > 1 else str(TOKENPAK_ROOT)
    print(f"Running benchmarks on: {bench_dir}")

    # 1. Compression
    print("1. Compression benchmark...")
    buf = io.StringIO()
    with redirect_stdout(buf):
        run_compression_benchmark(as_json=True)
    compression = json.loads(buf.getvalue())

    # 2. Token cache
    print("2. Token cache benchmark...")
    files = list(walk_directory(bench_dir))[:50]
    texts = []
    for path, _, _ in files:
        try:
            texts.append(Path(path).read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    token_bench = benchmark_tokenization(texts, iterations=3)

    # 3. Indexing
    print("3. Indexing benchmark...")
    indexing = benchmark_indexing_optimized(bench_dir, iterations=3)

    # 4. Compare
    print("4. Baseline comparison...")
    baseline = benchmark_indexing_baseline(bench_dir, iterations=1)
    speedup = baseline["total_ms"] / max(indexing["total_ms"], 0.001)
    compare = {
        "speedup": speedup,
        "baseline_fps": baseline["files_per_second"],
    }

    # 5. Proxy stats (optional)
    proxy = None
    try:
        with urllib.request.urlopen("http://localhost:8766/stats", timeout=3) as resp:
            proxy = json.loads(resp.read())
        print("5. Proxy stats collected.")
    except Exception:
        print("5. Proxy not running — skipping live stats.")

    # Generate report
    report = generate_results_report(compression, token_bench, indexing, compare, proxy)
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(report)
    print(f"\nReport written to: {RESULTS_PATH}")
    print("\n" + report)
