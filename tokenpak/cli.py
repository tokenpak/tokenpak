"""TokenPak CLI with parallel processing and optimized batch operations."""

import argparse
import hashlib
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple, List
import os

from .registry import BlockRegistry, Block
from .walker import walk_directory
from .tokens import count_tokens, truncate_to_tokens, cache_info, estimate_tokens
from .processors import get_processor
from .budget import BudgetBlock, quadratic_allocate
from .wire import pack
from .calibration import calibrate_workers, get_recommended_workers, load_profile
from .miss_detector import should_expand_retrieval, DEFAULT_GAPS_PATH


# Batch size for SQLite transactions
BATCH_SIZE = 100


def _process_file(args: Tuple) -> Optional[Tuple[str, Block]]:
    """
    Process a single file into a block (CPU-bound, parallelizable).

    Args: (path, file_type) or (path, file_type, no_treesitter)
    Returns: (path, content, Block) or None if skipped
    """
    path = args[0]
    file_type = args[1]
    no_treesitter = args[2] if len(args) > 2 else False

    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    if not content.strip():
        return None

    processor = get_processor(file_type, no_treesitter=no_treesitter)
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
    return (path, content, block)


def cmd_index(args):
    """Index a directory with parallel processing and batch transactions."""
    registry = BlockRegistry(args.db)
    files = list(walk_directory(args.directory))
    
    start_time = time.perf_counter()
    processed = 0
    skipped = 0
    unchanged = 0
    
    workers = getattr(args, 'workers', 1) or 1

    if getattr(args, 'recalibrate', False):
        result = calibrate_workers(args.directory, max_workers=getattr(args, 'max_workers', 8), rounds=getattr(args, 'calibration_rounds', 2))
        if "error" in result:
            print(f"Calibration skipped: {result['error']}")
        else:
            print(f"Calibration complete: best_workers={result['best_workers']} on {result['sample_files']} files")

    if getattr(args, 'auto_workers', False):
        workers = get_recommended_workers(default_workers=max(1, workers), max_workers=getattr(args, 'max_workers', 8))
        print(f"Auto workers selected: {workers}")
    
    no_treesitter = getattr(args, 'no_treesitter', False)

    if workers > 1:
        # Parallel processing path
        print(f"Indexing with {workers} workers...")

        # Phase 1: Parallel file processing (CPU-bound)
        file_args = [(path, file_type, no_treesitter) for path, file_type, _ in files]
        results = []
        
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_process_file, fa): fa for fa in file_args}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    results.append(result)
                else:
                    skipped += 1
        
        # Phase 2: Serial DB writes (I/O-bound, needs locking)
        with registry.batch_transaction() as conn:
            batch_count = 0
            for path, content, block in results:
                if not registry.has_changed(path, content):
                    unchanged += 1
                    continue
                
                registry.add_block_batch(block, conn)
                processed += 1
                batch_count += 1
                
                if batch_count >= BATCH_SIZE:
                    conn.commit()
                    conn.execute("BEGIN IMMEDIATE")
                    batch_count = 0
    else:
        # Single-threaded path (original behavior)
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
                
                if not registry.has_changed(path, content):
                    unchanged += 1
                    continue
                
                processor = get_processor(file_type, no_treesitter=no_treesitter)
                if not processor:
                    skipped += 1
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
                processed += 1
                batch_count += 1
                
                if batch_count >= BATCH_SIZE:
                    conn.commit()
                    conn.execute("BEGIN IMMEDIATE")
                    batch_count = 0
    
    elapsed = time.perf_counter() - start_time
    stats = registry.get_stats()
    
    print(f"Indexed: {processed} files in {elapsed:.2f}s ({processed/max(elapsed,0.001):.1f} files/sec)")
    print(f"Skipped: {skipped} | Unchanged: {unchanged}")
    print(f"Token cache: {cache_info()}")
    print(json.dumps(stats, indent=2))


def cmd_search(args):
    """Search indexed content."""
    registry = BlockRegistry(args.db)

    # Retrieval expansion: if query overlaps with a prior miss, double top_k
    top_k = args.top_k
    gaps_path = getattr(args, 'gaps', DEFAULT_GAPS_PATH)
    if should_expand_retrieval(args.query, gaps_path=gaps_path):
        top_k = top_k * 2
        print(f"[miss-detector] expanded due to prior miss: top_k={top_k}", flush=True)

    matches = registry.search(args.query, top_k=top_k)
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
        content, token_count = truncate_to_tokens(m.compressed_content, max_tokens)
        wire_blocks.append({
            "ref": ref,
            "type": m.file_type,
            "quality": m.quality_score,
            "tokens": token_count,
            "content": content,
        })

    if getattr(args, 'inject_refs', False):
        from .compiler import compile_with_refs
        output = compile_with_refs(wire_blocks, args.query, args.budget)
    else:
        output = pack(wire_blocks, args.budget, {"query": args.query})
    print(output)


def cmd_stats(args):
    """Show compression telemetry stats (last 100 requests)."""
    SEP = "─" * 45

    # Try to pull live stats from the running proxy
    proxy_data = None
    try:
        import urllib.request as _urlreq
        proxy_base = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
        with _urlreq.urlopen(f"{proxy_base}/health", timeout=3) as r:
            proxy_data = json.loads(r.read())
    except Exception:
        proxy_data = None

    # Also read from the JSONL file for accurate rolling stats
    from tokenpak.agent.proxy.stats import CompressionStats
    cs = CompressionStats()
    file_stats = cs.stats_from_file(limit=100)

    # Prefer live proxy data for request counts / uptime when available
    if proxy_data:
        requests_total = proxy_data.get("requests_total", file_stats["requests_total"])
        requests_errors = proxy_data.get("requests_errors", file_stats["requests_errors"])
        avg_ratio = proxy_data.get("compression_ratio_avg", file_stats["avg_ratio"])
        uptime_s = proxy_data.get("uptime_seconds")
    else:
        requests_total = file_stats["requests_total"]
        requests_errors = file_stats["requests_errors"]
        avg_ratio = file_stats["avg_ratio"]
        uptime_s = None

    avg_latency = file_stats["avg_latency_ms"]
    pct_reduction = round((1.0 - avg_ratio) * 100, 1) if avg_ratio else 0.0

    # Format uptime
    if uptime_s is not None:
        h, rem = divmod(int(uptime_s), 3600)
        m = rem // 60
        uptime_str = f"{h}h {m:02d}m" if h else f"{m}m"
    else:
        uptime_str = "n/a (proxy not running)"

    if getattr(args, "raw", False):
        print(json.dumps({
            "requests_total": requests_total,
            "requests_errors": requests_errors,
            "avg_ratio": avg_ratio,
            "avg_latency_ms": avg_latency,
            "uptime": uptime_str,
        }, indent=2))
        return

    print(f"TokenPak Compression Stats (last 100 requests)")
    print(SEP)
    print(f"{'Requests:':<17}{requests_total} total, {requests_errors} errors")
    print(f"{'Avg ratio:':<17}{avg_ratio} ({pct_reduction}% token reduction)")
    print(f"{'Avg latency:':<17}{avg_latency}ms")
    print(f"{'Uptime:':<17}{uptime_str}")


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
    run_benchmark(args.directory, args.iterations, compare=args.compare)


def cmd_calibrate(args):
    """Run static worker calibration and save host profile."""
    result = calibrate_workers(args.directory, max_workers=args.max_workers, rounds=args.rounds)
    print(json.dumps(result, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(prog="tokenpak", description="TokenPak CLI")
    parser.add_argument("--db", default=".tokenpak/registry.db", help="Registry SQLite path")

    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Index a directory")
    p_index.add_argument("directory", help="Directory to index")
    p_index.add_argument("--budget", type=int, default=8000)
    p_index.add_argument("--workers", "-w", type=int, default=4,
                         help="Parallel workers (default: 4)")
    p_index.add_argument("--auto-workers", action="store_true",
                         help="Use hybrid calibration (static baseline + dynamic adjustment)")
    p_index.add_argument("--recalibrate", action="store_true",
                         help="Run static calibration before indexing")
    p_index.add_argument("--calibration-rounds", type=int, default=2,
                         help="Calibration rounds per candidate worker count")
    p_index.add_argument("--max-workers", type=int, default=8,
                         help="Upper worker cap for auto/recalibration")
    p_index.add_argument("--no-treesitter", action="store_true",
                         help="Force regex-based code processing (skip tree-sitter)")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="Search indexed content")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--budget", type=int, default=8000)
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument("--gaps", default=DEFAULT_GAPS_PATH,
                          help="Path to gaps.json for miss-based retrieval expansion")
    p_search.add_argument("--inject-refs", action="store_true",
                          help="Enable compile-time reference injection (GitHub, URLs)")
    p_search.set_defaults(func=cmd_search)

    p_stats = sub.add_parser("stats", help="Show registry stats")
    p_stats.set_defaults(func=cmd_stats)

    p_serve = sub.add_parser("serve", help="Start monitoring proxy")
    p_serve.add_argument("--port", type=int, default=8766)
    p_serve.set_defaults(func=cmd_serve)

    p_bench = sub.add_parser("benchmark", help="Run latency benchmark")
    p_bench.add_argument("directory", help="Directory to benchmark")
    p_bench.add_argument("--iterations", type=int, default=3)
    p_bench.add_argument("--compare", action="store_true",
                         help="Compare baseline vs optimized")
    p_bench.set_defaults(func=cmd_benchmark)

    p_cal = sub.add_parser("calibrate", help="Calibrate best worker count for this host")
    p_cal.add_argument("directory", help="Directory to sample for calibration")
    p_cal.add_argument("--max-workers", type=int, default=8)
    p_cal.add_argument("--rounds", type=int, default=2)
    p_cal.set_defaults(func=cmd_calibrate)

    # TRIGGER COMMANDS
    p_trigger = sub.add_parser("trigger", help="Manage event triggers")
    trigger_sub = p_trigger.add_subparsers(dest="trigger_command", required=True)

    # trigger add
    p_trig_add = trigger_sub.add_parser("add", help="Add a new trigger")
    p_trig_add.add_argument("event", help="Event type (file:changed, git:push, cost:threshold, agent:finished)")
    p_trig_add.add_argument("pattern", help="Pattern to match (glob for files, * for any)")
    p_trig_add.add_argument("action", help="CLI command to execute")
    p_trig_add.add_argument("--description", "-d", help="Optional description")
    p_trig_add.set_defaults(func=cmd_trigger_add)

    # trigger list
    p_trig_list = trigger_sub.add_parser("list", help="List all triggers")
    p_trig_list.add_argument("--event", help="Filter by event type")
    p_trig_list.set_defaults(func=cmd_trigger_list)

    # trigger remove
    p_trig_rm = trigger_sub.add_parser("remove", help="Remove a trigger")
    p_trig_rm.add_argument("id", help="Trigger ID to remove")
    p_trig_rm.set_defaults(func=cmd_trigger_remove)

    # trigger test
    p_trig_test = trigger_sub.add_parser("test", help="Dry-run: show what would fire")
    p_trig_test.add_argument("event", help="Event type to test")
    p_trig_test.add_argument("--data", help="Event data (e.g., file path)")
    p_trig_test.set_defaults(func=cmd_trigger_test)

    # trigger log
    p_trig_log = trigger_sub.add_parser("log", help="Show recent trigger activations")
    p_trig_log.add_argument("--limit", type=int, default=20, help="Max entries to show")
    p_trig_log.add_argument("--trigger", help="Filter by trigger ID")
    p_trig_log.set_defaults(func=cmd_trigger_log)

    # trigger fire (manual event firing)
    p_trig_fire = trigger_sub.add_parser("fire", help="Manually fire an event")
    p_trig_fire.add_argument("event", help="Event type")
    p_trig_fire.add_argument("data", help="Event data")
    p_trig_fire.add_argument("--dry-run", action="store_true", help="Do not execute actions")
    p_trig_fire.set_defaults(func=cmd_trigger_fire)

    # trigger watch (start file watcher)
    p_trig_watch = trigger_sub.add_parser("watch", help="Start file watcher")
    p_trig_watch.add_argument("paths", nargs="*", help="Paths to watch (default: .)")
    p_trig_watch.set_defaults(func=cmd_trigger_watch)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()


# ============================================================================
# TRIGGER COMMANDS (Event-driven automation)
# ============================================================================

def cmd_trigger_add(args):
    """Add a new event trigger."""
    from .agent.macros.hooks import add_trigger, EventType
    
    # Validate event type early
    try:
        EventType.from_string(args.event)
    except ValueError as e:
        print(f"Error: {e}")
        return
    
    trigger = add_trigger(
        event_type=args.event,
        pattern=args.pattern,
        action=args.action,
        description=getattr(args, 'description', '') or ''
    )
    
    print(f"✓ Trigger created: {trigger.id}")
    print(f"  Event:   {trigger.event_type}")
    print(f"  Pattern: {trigger.pattern}")
    print(f"  Action:  {trigger.action}")


def cmd_trigger_list(args):
    """List all triggers."""
    from .agent.macros.hooks import list_triggers
    
    triggers = list_triggers(event_type=getattr(args, 'event', None))
    
    if not triggers:
        print("No triggers registered.")
        return
    
    print(f"{'ID':<10} {'EVENT':<18} {'PATTERN':<25} {'ACTION':<30} {'ENABLED'}")
    print("-" * 95)
    
    for t in triggers:
        enabled = "✓" if t.enabled else "✗"
        pattern = t.pattern[:23] + ".." if len(t.pattern) > 25 else t.pattern
        action = t.action[:28] + ".." if len(t.action) > 30 else t.action
        print(f"{t.id:<10} {t.event_type:<18} {pattern:<25} {action:<30} {enabled}")


def cmd_trigger_remove(args):
    """Remove a trigger."""
    from .agent.macros.hooks import remove_trigger, _get_registry
    
    trigger = _get_registry().get(args.id)
    if not trigger:
        print(f"Error: Trigger '{args.id}' not found.")
        return
    
    if remove_trigger(args.id):
        print(f"✓ Trigger '{args.id}' removed.")
    else:
        print(f"Error: Failed to remove trigger '{args.id}'.")


def cmd_trigger_test(args):
    """Dry-run: show what triggers would fire for an event."""
    from .agent.macros.hooks import test_trigger, EventType
    
    # Validate event type
    try:
        EventType.from_string(args.event)
    except ValueError as e:
        print(f"Error: {e}")
        return
    
    event_data = getattr(args, 'data', '*') or '*'
    results = test_trigger(args.event, event_data)
    
    if not results:
        print(f"No triggers would fire for event '{args.event}' with data '{event_data}'.")
        return
    
    print(f"Triggers that would fire for '{args.event}' ({event_data}):")
    print("-" * 60)
    
    for r in results:
        print(f"  [{r['id']}] {r['pattern']}")
        print(f"    → {r['action']}")
        print()


def cmd_trigger_log(args):
    """Show recent trigger activations."""
    from .agent.macros.hooks import get_trigger_log
    
    limit = getattr(args, 'limit', 20) or 20
    trigger_id = getattr(args, 'trigger', None)
    
    entries = get_trigger_log(limit=limit, trigger_id=trigger_id)
    
    if not entries:
        print("No trigger activations logged.")
        return
    
    for entry in entries:
        status = "✓" if entry.success else "✗"
        dry = " [dry-run]" if entry.dry_run else ""
        print(f"{status} {entry.timestamp[:19]} [{entry.trigger_id}]{dry}")
        print(f"   Event: {entry.event_type} → {entry.event_data[:50]}")
        print(f"   Action: {entry.action[:60]}")
        if entry.error:
            print(f"   Error: {entry.error[:80]}")
        print()


def cmd_trigger_fire(args):
    """Manually fire an event (for testing)."""
    from .agent.macros.hooks import fire_event, EventType
    
    # Validate event type
    try:
        EventType.from_string(args.event)
    except ValueError as e:
        print(f"Error: {e}")
        return
    
    dry_run = getattr(args, 'dry_run', False)
    entries = fire_event(args.event, args.data, dry_run=dry_run)
    
    if not entries:
        print(f"No triggers matched event '{args.event}' with data '{args.data}'.")
        return
    
    mode = "[DRY-RUN] " if dry_run else ""
    print(f"{mode}Fired {len(entries)} trigger(s):")
    
    for entry in entries:
        status = "✓" if entry.success else "✗"
        print(f"  {status} [{entry.trigger_id}] {entry.action}")
        if entry.output:
            for line in entry.output.strip().split('\n')[:5]:
                print(f"      {line}")
        if entry.error:
            print(f"      Error: {entry.error[:80]}")


def cmd_trigger_watch(args):
    """Start file watcher for file:changed events."""
    from .agent.macros.hooks import start_file_watcher, stop_file_watcher, is_file_watcher_running
    import signal
    
    paths = args.paths if args.paths else ["."]
    
    if not start_file_watcher(paths):
        if is_file_watcher_running():
            print("File watcher already running.")
        else:
            print("Error: Could not start file watcher. Is 'watchdog' installed?")
            print("  pip install watchdog")
        return
    
    print(f"File watcher started. Watching: {', '.join(paths)}")
    print("Press Ctrl+C to stop.")
    
    def handle_sigint(sig, frame):
        stop_file_watcher()
        print("\nFile watcher stopped.")
        exit(0)
    
    signal.signal(signal.SIGINT, handle_sigint)
    
    # Block forever
    import time
    while True:
        time.sleep(1)
