"""TokenPak CLI with optimized batch processing."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple

from .registry import BlockRegistry, Block
from .walker import walk_directory
from .tokens import count_tokens, truncate_to_tokens, cache_info
from .processors import get_processor
from .budget import BudgetBlock, quadratic_allocate
from .wire import pack


# Batch size for SQLite transactions
BATCH_SIZE = 50


def _process_file_content(path: str, file_type: str, content: str) -> Optional[Block]:
    """Process file content into a block (CPU-bound, can parallelize)."""
    if not content.strip():
        return None

    processor = get_processor(file_type)
    if not processor:
        return None

    compressed = processor.process(content, path)

    return Block(
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


def _read_file(path: str) -> Tuple[str, Optional[str]]:
    """Read file content, return (path, content or None)."""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
        return path, content
    except Exception:
        return path, None


def cmd_index(args):
    """Index a directory with batch transactions and optional parallelism."""
    registry = BlockRegistry(args.db)
    files = list(walk_directory(args.directory))
    
    start_time = time.perf_counter()
    processed = 0
    skipped = 0
    unchanged = 0
    
    # Process in batches with batch transactions
    with registry.batch_transaction() as conn:
        batch_count = 0
        
        for path, file_type, _ in files:
            try:
                content = Path(path).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                skipped += 1
                continue
            
            if not content.strip():
                skipped += 1
                continue
            
            # Check if unchanged
            if not registry.has_changed(path, content):
                unchanged += 1
                continue
            
            # Process
            block = _process_file_content(path, file_type, content)
            if block:
                registry.add_block_batch(block, conn)
                processed += 1
                batch_count += 1
                
                # Commit every BATCH_SIZE files
                if batch_count >= BATCH_SIZE:
                    conn.commit()
                    batch_count = 0
            else:
                skipped += 1
    
    elapsed = time.perf_counter() - start_time
    stats = registry.get_stats()
    
    print(f"Indexed: {processed} files in {elapsed:.2f}s")
    print(f"Skipped: {skipped} | Unchanged: {unchanged}")
    print(f"Token cache: {cache_info()}")
    print(json.dumps(stats, indent=2))


def cmd_search(args):
    """Search indexed content."""
    registry = BlockRegistry(args.db)
    matches = registry.search(args.query, top_k=args.top_k)
    if not matches:
        print("No matches found.")
        return

    budget_blocks = []
    type_weights = {"text": 0.8, "code": 0.7, "data": 0.6, "pdf": 0.7}

    for m in matches:
        budget_blocks.append(BudgetBlock(
            ref=f"{m.path}#v{m.version}",
            relevance_score=0.8,
            recency_score=0.6,
            quality_score=m.quality_score,
            type_weight=type_weights.get(m.file_type, 0.5),
        ))

    alloc = quadratic_allocate(budget_blocks, args.budget)

    wire_blocks = []
    for m in matches:
        ref = f"{m.path}#v{m.version}"
        max_tokens = alloc.get(ref, 200)
        # truncate_to_tokens now returns (text, count) tuple
        content, token_count = truncate_to_tokens(m.compressed_content, max_tokens)
        wire_blocks.append({
            "ref": ref,
            "type": m.file_type,
            "quality": m.quality_score,
            "tokens": token_count,
            "content": content,
        })

    output = pack(wire_blocks, args.budget, {"query": args.query})
    print(output)


def cmd_stats(args):
    """Show registry stats."""
    registry = BlockRegistry(args.db)
    stats = registry.get_stats()
    stats["token_cache"] = str(cache_info())
    print(json.dumps(stats, indent=2))


def cmd_serve(args):
    """Start monitoring proxy (if available)."""
    try:
        import sys
        proxy_path = str(Path.home() / ".openclaw" / "workspace" / ".ocp")
        if proxy_path not in sys.path:
            sys.path.insert(0, proxy_path)
        import proxy
        proxy.run_proxy(args.port)
    except Exception as e:
        print(f"Serve mode unavailable: {e}")
        print("Run the existing proxy directly if needed.")


def cmd_benchmark(args):
    """Run latency benchmark."""
    from .benchmark import run_benchmark
    run_benchmark(args.directory, args.iterations)


def build_parser():
    parser = argparse.ArgumentParser(prog="tokenpak", description="TokenPak CLI")
    parser.add_argument("--db", default=".tokenpak/registry.db", help="Registry SQLite path")

    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Index a directory")
    p_index.add_argument("directory", help="Directory to index")
    p_index.add_argument("--budget", type=int, default=8000)
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="Search indexed content")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--budget", type=int, default=8000)
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.set_defaults(func=cmd_search)

    p_stats = sub.add_parser("stats", help="Show registry stats")
    p_stats.set_defaults(func=cmd_stats)

    p_serve = sub.add_parser("serve", help="Start monitoring proxy")
    p_serve.add_argument("--port", type=int, default=8766)
    p_serve.set_defaults(func=cmd_serve)

    p_bench = sub.add_parser("benchmark", help="Run latency benchmark")
    p_bench.add_argument("directory", help="Directory to benchmark")
    p_bench.add_argument("--iterations", type=int, default=3)
    p_bench.set_defaults(func=cmd_benchmark)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
