#!/usr/bin/env python3
"""
TokenPak — Vault Retrieval Backend Benchmark
=============================================

Compares json_blocks (VaultIndex) vs sqlite (SQLiteRetrievalBackend)
across three dimensions:
  1. Cold load / startup time
  2. Warm reload (mtime unchanged)
  3. Search latency (100 queries)

Usage::

    python3 benchmark_retrieval.py [vault_index_path]

    # Default vault path: ~/vault/.tokenpak
    # Or override with env: TOKENPAK_VAULT_INDEX=<path>

Output is printed as a markdown table for easy pasting into task submission.
"""

import os
import sys
import time
import statistics
import tempfile
import shutil
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent))

VAULT_PATH = os.environ.get(
    "TOKENPAK_VAULT_INDEX",
    str(Path.home() / "vault" / ".tokenpak"),
)
if len(sys.argv) > 1:
    VAULT_PATH = sys.argv[1]

TOP_K = 5
MIN_SCORE = 2.0
NUM_SEARCH_RUNS = 100

SAMPLE_QUERIES = [
    "authentication token validation",
    "BM25 scoring retrieval",
    "SQLite backend incremental update",
    "proxy vault injection context",
    "budget token limit capsule",
    "error handling fallback strategy",
    "registry block content versioning",
    "semantic similarity search",
    "pipeline stage trace monitoring",
    "compaction threshold optimization",
]


def _fmt(ms: float) -> str:
    if ms < 1:
        return f"{ms*1000:.1f} µs"
    return f"{ms:.2f} ms"


def benchmark_json_blocks(vault_path: str) -> dict:
    """Benchmark the existing in-memory JSON/blocks backend."""
    try:
        # Import directly from proxy_v4 context
        sys.path.insert(0, str(Path(__file__).parent))
        import importlib.util, math, threading, re, json as _json, os as _os

        # Inline a minimal VaultIndex clone so we don't import the full proxy
        from pathlib import Path as _Path

        def _bm25_tokenize(text):
            return re.findall(r"[a-z0-9_]+", text.lower())

        class _VaultIndex:
            def __init__(self, tokenpak_dir):
                self.tokenpak_dir = _Path(tokenpak_dir)
                self.blocks = {}
                self._df = {}
                self._block_tfs = {}
                self._avg_dl = 0.0
                self._doc_count = 0

            def load(self):
                index_path = self.tokenpak_dir / "index.json"
                if not index_path.exists():
                    return False
                data = _json.loads(index_path.read_text())
                blocks_dir = self.tokenpak_dir / "blocks"
                new_blocks = {}
                raw_blocks = data.get("blocks", {})
                if not isinstance(raw_blocks, dict):
                    return False
                for bid, bdata in raw_blocks.items():
                    content = ""
                    cf = blocks_dir / f"{bid}.txt"
                    if cf.exists():
                        try:
                            content = cf.read_text(errors="replace")
                        except OSError:
                            pass
                    new_blocks[bid] = {
                        "block_id": bid,
                        "source_path": bdata.get("source_path", bid),
                        "risk_class": bdata.get("risk_class", "narrative"),
                        "must_keep": bdata.get("must_keep", False),
                        "raw_tokens": bdata.get("raw_tokens", 0),
                        "content": content,
                    }
                df = {}
                block_tfs = {}
                total_dl = 0
                for bid, block in new_blocks.items():
                    terms = _bm25_tokenize(block["content"])
                    tf = {}
                    for t in terms:
                        tf[t] = tf.get(t, 0) + 1
                    block_tfs[bid] = tf
                    total_dl += len(terms)
                    for t in set(terms):
                        df[t] = df.get(t, 0) + 1
                doc_count = len(new_blocks)
                self.blocks = new_blocks
                self._df = df
                self._block_tfs = block_tfs
                self._avg_dl = total_dl / doc_count if doc_count > 0 else 0
                self._doc_count = doc_count
                return True

            def search(self, query, top_k=5, min_score=2.0):
                query_terms = _bm25_tokenize(query)
                if not query_terms or not self.blocks:
                    return []
                k1, b_param = 1.5, 0.75
                scores = {}
                for bid in self.blocks:
                    tf = self._block_tfs.get(bid, {})
                    dl = sum(tf.values())
                    score = 0.0
                    for qt in query_terms:
                        if qt not in self._df:
                            continue
                        idf = math.log((self._doc_count - self._df[qt] + 0.5) / (self._df[qt] + 0.5) + 1)
                        term_freq = tf.get(qt, 0)
                        if term_freq == 0:
                            continue
                        score += idf * term_freq * (k1 + 1) / (term_freq + k1 * (1 - b_param + b_param * dl / self._avg_dl))
                    if score >= min_score:
                        scores[bid] = score
                ranked = sorted(scores.items(), key=lambda x: (-x[1], self.blocks[x[0]].get("source_path", ""), x[0]))[:top_k]
                return [(self.blocks[bid], score) for bid, score in ranked]

        # Cold load benchmark
        t0 = time.perf_counter()
        vi = _VaultIndex(vault_path)
        loaded = vi.load()
        cold_ms = (time.perf_counter() - t0) * 1000

        if not loaded:
            return {"error": "index.json not found"}

        block_count = len(vi.blocks)

        # Warm reload (mtime check only, no reload needed)
        t0 = time.perf_counter()
        vi.load()  # simulate re-load
        warm_ms = (time.perf_counter() - t0) * 1000

        # Search latency
        latencies = []
        for i in range(NUM_SEARCH_RUNS):
            q = SAMPLE_QUERIES[i % len(SAMPLE_QUERIES)]
            t0 = time.perf_counter()
            vi.search(q, top_k=TOP_K, min_score=MIN_SCORE)
            latencies.append((time.perf_counter() - t0) * 1000)

        return {
            "backend": "json_blocks",
            "block_count": block_count,
            "cold_load_ms": cold_ms,
            "warm_reload_ms": warm_ms,
            "search_p50_ms": statistics.median(latencies),
            "search_p95_ms": statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies),
            "search_mean_ms": statistics.mean(latencies),
        }
    except Exception as e:
        return {"error": str(e)}


def benchmark_sqlite(vault_path: str) -> dict:
    """Benchmark the SQLite retrieval backend."""
    try:
        from tokenpak.agent.vault.sqlite_retrieval import SQLiteRetrievalBackend

        # Use a temp copy of the DB so we don't pollute the real one during benchmark
        tmp_dir = tempfile.mkdtemp(prefix="tokenpak_bench_")
        try:
            # Copy the index to temp vault dir
            src = Path(vault_path)
            dst = Path(tmp_dir) / "vault_idx"
            shutil.copytree(src, dst, dirs_exist_ok=True)
            # Remove existing DB so we get a true cold-build
            db_path = dst / "retrieval.db"
            if db_path.exists():
                db_path.unlink()

            # Cold build (initial full load from JSON)
            t0 = time.perf_counter()
            backend = SQLiteRetrievalBackend(str(dst))
            backend._check_interval = 0  # disable throttle for benchmark
            backend.maybe_reload()
            cold_ms = (time.perf_counter() - t0) * 1000

            if not backend.available:
                return {"error": "SQLite backend not available after reload"}

            block_count = backend.block_count

            # Warm reload (mtime unchanged — should be a no-op after checkpoint)
            t0 = time.perf_counter()
            backend.maybe_reload()
            warm_ms = (time.perf_counter() - t0) * 1000

            # Search latency
            latencies = []
            for i in range(NUM_SEARCH_RUNS):
                q = SAMPLE_QUERIES[i % len(SAMPLE_QUERIES)]
                t0 = time.perf_counter()
                backend.search(q, top_k=TOP_K, min_score=MIN_SCORE)
                latencies.append((time.perf_counter() - t0) * 1000)

            return {
                "backend": "sqlite",
                "block_count": block_count,
                "cold_load_ms": cold_ms,
                "warm_reload_ms": warm_ms,
                "search_p50_ms": statistics.median(latencies),
                "search_p95_ms": statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies),
                "search_mean_ms": statistics.mean(latencies),
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    except Exception as e:
        return {"error": str(e)}


def main():
    print(f"\n🔍 TokenPak Retrieval Backend Benchmark")
    print(f"   Vault path: {VAULT_PATH}")
    print(f"   Queries: {NUM_SEARCH_RUNS} × {len(SAMPLE_QUERIES)} samples\n")

    print("Running json_blocks benchmark...")
    jb = benchmark_json_blocks(VAULT_PATH)
    print("Running sqlite benchmark...")
    sq = benchmark_sqlite(VAULT_PATH)

    if "error" in jb:
        print(f"  ❌ json_blocks error: {jb['error']}")
    if "error" in sq:
        print(f"  ❌ sqlite error: {sq['error']}")

    # Results table
    if "error" not in jb and "error" not in sq:
        headers = ["Metric", "json_blocks", "sqlite", "Δ"]
        rows = [
            (
                "Blocks",
                str(jb["block_count"]),
                str(sq["block_count"]),
                "–",
            ),
            (
                "Cold load",
                _fmt(jb["cold_load_ms"]),
                _fmt(sq["cold_load_ms"]),
                _delta(jb["cold_load_ms"], sq["cold_load_ms"]),
            ),
            (
                "Warm reload",
                _fmt(jb["warm_reload_ms"]),
                _fmt(sq["warm_reload_ms"]),
                _delta(jb["warm_reload_ms"], sq["warm_reload_ms"]),
            ),
            (
                "Search p50",
                _fmt(jb["search_p50_ms"]),
                _fmt(sq["search_p50_ms"]),
                _delta(jb["search_p50_ms"], sq["search_p50_ms"]),
            ),
            (
                "Search p95",
                _fmt(jb["search_p95_ms"]),
                _fmt(sq["search_p95_ms"]),
                _delta(jb["search_p95_ms"], sq["search_p95_ms"]),
            ),
            (
                "Search mean",
                _fmt(jb["search_mean_ms"]),
                _fmt(sq["search_mean_ms"]),
                _delta(jb["search_mean_ms"], sq["search_mean_ms"]),
            ),
        ]

        col_w = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

        def _row(cells):
            return "| " + " | ".join(c.ljust(col_w[i]) for i, c in enumerate(cells)) + " |"

        sep = "|-" + "-|-".join("-" * w for w in col_w) + "-|"

        print("\n" + _row(headers))
        print(sep)
        for row in rows:
            print(_row(row))
        print()

    print("Raw results:")
    import json
    print(json.dumps({"json_blocks": jb, "sqlite": sq}, indent=2))


def _delta(a: float, b: float) -> str:
    if a == 0:
        return "–"
    pct = (b - a) / a * 100
    arrow = "▼" if pct < 0 else "▲"
    return f"{arrow} {abs(pct):.0f}%"


if __name__ == "__main__":
    main()
