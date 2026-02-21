"""Latency benchmarking for TokenPak operations."""

import time
import statistics
from pathlib import Path
from typing import List, Tuple

from .registry import BlockRegistry
from .walker import walk_directory
from .tokens import count_tokens, cache_info, clear_cache
from .processors import get_processor


def benchmark_tokenization(texts: List[str], iterations: int = 3) -> dict:
    """Benchmark token counting with and without cache."""
    results = {}
    
    # Warm up
    for t in texts[:10]:
        count_tokens(t)
    
    # With cache (warm)
    times = []
    for _ in range(iterations):
        clear_cache()
        # First pass (cold cache)
        start = time.perf_counter()
        for t in texts:
            count_tokens(t)
        times.append(time.perf_counter() - start)
    
    results["cold_cache_avg_ms"] = statistics.mean(times) * 1000
    
    # Second pass (warm cache)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        for t in texts:
            count_tokens(t)
        times.append(time.perf_counter() - start)
    
    results["warm_cache_avg_ms"] = statistics.mean(times) * 1000
    results["cache_speedup"] = results["cold_cache_avg_ms"] / max(results["warm_cache_avg_ms"], 0.001)
    results["cache_info"] = str(cache_info())
    
    return results


def benchmark_processing(files: List[Tuple[str, str, str]], iterations: int = 3) -> dict:
    """Benchmark file processing (regex patterns)."""
    results = {}
    
    # Group by type
    by_type = {}
    for path, file_type, _ in files:
        if file_type not in by_type:
            by_type[file_type] = []
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
            by_type[file_type].append((path, content))
        except Exception:
            pass
    
    for file_type, items in by_type.items():
        if not items:
            continue
        
        processor = get_processor(file_type)
        if not processor:
            continue
        
        times = []
        for _ in range(iterations):
            start = time.perf_counter()
            for path, content in items:
                processor.process(content, path)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
        
        avg_ms = statistics.mean(times) * 1000
        per_file_ms = avg_ms / len(items)
        results[file_type] = {
            "files": len(items),
            "total_ms": round(avg_ms, 2),
            "per_file_ms": round(per_file_ms, 3),
        }
    
    return results


def benchmark_indexing(directory: str, iterations: int = 3) -> dict:
    """Benchmark full indexing pipeline."""
    import tempfile
    import shutil
    
    results = {}
    times = []
    
    for i in range(iterations):
        # Use fresh temp DB each iteration
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/bench.db"
            registry = BlockRegistry(db_path)
            files = list(walk_directory(directory))
            
            start = time.perf_counter()
            processed = 0
            
            with registry.batch_transaction() as conn:
                for path, file_type, _ in files:
                    try:
                        content = Path(path).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        continue
                    
                    if not content.strip():
                        continue
                    
                    processor = get_processor(file_type)
                    if not processor:
                        continue
                    
                    compressed = processor.process(content, path)
                    
                    from .registry import Block
                    import hashlib
                    
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
                    processed += 1
            
            elapsed = time.perf_counter() - start
            times.append((elapsed, processed))
            registry.close()
    
    avg_time = statistics.mean([t[0] for t in times])
    avg_files = statistics.mean([t[1] for t in times])
    
    results["total_files"] = int(avg_files)
    results["total_ms"] = round(avg_time * 1000, 2)
    results["per_file_ms"] = round((avg_time * 1000) / max(avg_files, 1), 3)
    results["files_per_second"] = round(avg_files / max(avg_time, 0.001), 1)
    
    return results


def benchmark_search(registry: BlockRegistry, queries: List[str], iterations: int = 3) -> dict:
    """Benchmark search operations."""
    results = {}
    
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        for q in queries:
            registry.search(q, top_k=10)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    
    avg_ms = statistics.mean(times) * 1000
    results["queries"] = len(queries)
    results["total_ms"] = round(avg_ms, 2)
    results["per_query_ms"] = round(avg_ms / len(queries), 3)
    
    return results


def run_benchmark(directory: str, iterations: int = 3):
    """Run full benchmark suite."""
    print(f"TokenPak Latency Benchmark")
    print(f"Directory: {directory}")
    print(f"Iterations: {iterations}")
    print("=" * 60)
    
    # Collect files
    files = list(walk_directory(directory))
    print(f"Found {len(files)} files")
    
    # Read file contents
    texts = []
    for path, _, _ in files:
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
            texts.append(content)
        except Exception:
            pass
    
    print(f"Read {len(texts)} files\n")
    
    # 1. Tokenization benchmark
    print("1. TOKEN COUNTING")
    token_results = benchmark_tokenization(texts, iterations)
    print(f"   Cold cache: {token_results['cold_cache_avg_ms']:.2f}ms")
    print(f"   Warm cache: {token_results['warm_cache_avg_ms']:.2f}ms")
    print(f"   Speedup: {token_results['cache_speedup']:.1f}x")
    print()
    
    # 2. Processing benchmark
    print("2. FILE PROCESSING (regex)")
    proc_results = benchmark_processing(files, iterations)
    for ftype, stats in proc_results.items():
        print(f"   {ftype}: {stats['per_file_ms']:.3f}ms/file ({stats['files']} files)")
    print()
    
    # 3. Indexing benchmark
    print("3. FULL INDEXING")
    index_results = benchmark_indexing(directory, iterations)
    print(f"   Total: {index_results['total_ms']:.2f}ms for {index_results['total_files']} files")
    print(f"   Per file: {index_results['per_file_ms']:.3f}ms")
    print(f"   Throughput: {index_results['files_per_second']:.1f} files/sec")
    print()
    
    # 4. Search benchmark
    print("4. SEARCH")
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = f"{tmpdir}/bench.db"
        registry = BlockRegistry(db_path)
        
        # Index first
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
                
                from .registry import Block
                import hashlib
                
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
        
        # Search benchmark
        queries = ["import", "function", "class", "def", "return", "error", "config", "data"]
        search_results = benchmark_search(registry, queries, iterations)
        print(f"   Per query: {search_results['per_query_ms']:.3f}ms ({search_results['queries']} queries)")
        registry.close()
    
    print()
    print("=" * 60)
    print("SUMMARY")
    print(f"  Token cache speedup: {token_results['cache_speedup']:.1f}x")
    print(f"  Indexing throughput: {index_results['files_per_second']:.1f} files/sec")
    print(f"  Search latency: {search_results['per_query_ms']:.3f}ms/query")


if __name__ == "__main__":
    import sys
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    run_benchmark(directory)
