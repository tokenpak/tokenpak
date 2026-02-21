"""TokenPak CLI."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from .registry import BlockRegistry, Block
from .walker import walk_directory
from .tokens import count_tokens, truncate_to_tokens
from .processors import get_processor
from .budget import BudgetBlock, quadratic_allocate
from .wire import pack


def _process_file(path: str, file_type: str, registry: BlockRegistry) -> Block | None:
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    if not content.strip():
        return None

    if not registry.has_changed(path, content):
        return None

    processor = get_processor(file_type)
    if not processor:
        return None

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
    return registry.add_block(block)


def cmd_index(args):
    registry = BlockRegistry(args.db)
    files = walk_directory(args.directory)

    processed = 0
    skipped = 0

    for path, file_type, _ in files:
        b = _process_file(path, file_type, registry)
        if b:
            processed += 1
        else:
            skipped += 1

    stats = registry.get_stats()
    print(f"Indexed: {processed} files (skipped unchanged: {skipped})")
    print(json.dumps(stats, indent=2))


def cmd_search(args):
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
        content = truncate_to_tokens(m.compressed_content, max_tokens)
        wire_blocks.append({
            "ref": ref,
            "type": m.file_type,
            "quality": m.quality_score,
            "tokens": count_tokens(content),
            "content": content,
        })

    output = pack(wire_blocks, args.budget, {"query": args.query})
    print(output)


def cmd_stats(args):
    registry = BlockRegistry(args.db)
    print(json.dumps(registry.get_stats(), indent=2))


def cmd_serve(args):
    # Reuse existing Cali proxy implementation if available
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

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
