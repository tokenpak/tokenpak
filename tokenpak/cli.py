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


# Batch size for SQLite transactions
BATCH_SIZE = 100


def _process_file(args: Tuple[str, str]) -> Optional[Tuple[str, Block]]:
    """
    Process a single file into a block (CPU-bound, parallelizable).
    
    Args: (path, file_type)
    Returns: (path, Block) or None if skipped
    """
    path, file_type = args
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    
    if not content.strip():
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
    
    if workers > 1:
        # Parallel processing path
        print(f"Indexing with {workers} workers...")
        
        # Phase 1: Parallel file processing (CPU-bound)
        file_args = [(path, file_type) for path, file_type, _ in files]
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
                
                processor = get_processor(file_type)
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
    p_bench.add_argument("--compare", action="store_true",
                         help="Compare baseline vs optimized")
    p_bench.set_defaults(func=cmd_benchmark)

    p_cal = sub.add_parser("calibrate", help="Calibrate best worker count for this host")
    p_cal.add_argument("directory", help="Directory to sample for calibration")
    p_cal.add_argument("--max-workers", type=int, default=8)
    p_cal.add_argument("--rounds", type=int, default=2)
    p_cal.set_defaults(func=cmd_calibrate)

    _build_trigger_parser(sub)
    _build_cost_parser(sub)
    _build_budget_parser(sub)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()


# ── Trigger commands ──────────────────────────────────────────────────────────

def _trigger_store():
    from .agent.triggers.store import TriggerStore
    return TriggerStore()


def cmd_trigger_list(args):
    store = _trigger_store()
    triggers = store.list()
    if not triggers:
        print("No triggers registered.")
        return
    print(f"{'ID':<10} {'ENABLED':<8} {'EVENT':<35} ACTION")
    print("-" * 75)
    for t in triggers:
        enabled = "yes" if t.enabled else "no"
        print(f"{t.id:<10} {enabled:<8} {t.event:<35} {t.action}")


def cmd_trigger_add(args):
    store = _trigger_store()
    t = store.add(event=args.event, action=args.action)
    print(f"Trigger added: id={t.id}  event={t.event}  action={t.action}")


def cmd_trigger_remove(args):
    store = _trigger_store()
    if store.remove(args.id):
        print(f"Trigger {args.id} removed.")
    else:
        print(f"No trigger with id={args.id}")


def cmd_trigger_test(args):
    """Dry-run: show which registered triggers would fire for a given event."""
    from .agent.triggers.matcher import match_event
    store = _trigger_store()
    event = args.event
    print(f"Testing event: {event}")
    matched = [t for t in store.list() if t.enabled and match_event(t.event, event)]
    if not matched:
        print("  No triggers would fire.")
    for t in matched:
        print(f"  ✓ {t.id}  {t.event}  →  {t.action}")


def cmd_trigger_log(args):
    store = _trigger_store()
    logs = store.list_logs(limit=args.limit)
    if not logs:
        print("No trigger log entries.")
        return
    for lg in logs:
        status = "✓" if lg.exit_code == 0 else "✗"
        print(f"{status} [{lg.fired_at[:19]}] {lg.trigger_id}  {lg.event}  →  {lg.action}")
        if lg.output:
            print(f"   {lg.output[:120]}")


def cmd_trigger_daemon(args):
    from .agent.triggers.daemon import TriggerDaemon
    store = _trigger_store()
    daemon = TriggerDaemon(store=store)
    daemon.run()


def _build_trigger_parser(sub):
    p_trig = sub.add_parser("trigger", help="Manage event triggers")
    tsub = p_trig.add_subparsers(dest="trigger_cmd", required=True)

    tsub.add_parser("list", help="List all triggers").set_defaults(func=cmd_trigger_list)

    p_add = tsub.add_parser("add", help="Register a new trigger")
    p_add.add_argument("event", help="Event pattern (e.g. file:changed:*.py, timer:5m, cost:daily>10)")
    p_add.add_argument("action", help="Action: tokenpak sub-command or shell script path")
    p_add.set_defaults(func=cmd_trigger_add)

    p_rm = tsub.add_parser("remove", help="Remove a trigger by id")
    p_rm.add_argument("id", help="Trigger ID")
    p_rm.set_defaults(func=cmd_trigger_remove)

    p_test = tsub.add_parser("test", help="Dry-run: show which triggers match an event")
    p_test.add_argument("event", help="Event string to test")
    p_test.set_defaults(func=cmd_trigger_test)

    p_log = tsub.add_parser("log", help="Show recent trigger fire log")
    p_log.add_argument("--limit", type=int, default=20)
    p_log.set_defaults(func=cmd_trigger_log)

    tsub.add_parser("daemon", help="Start background trigger daemon").set_defaults(func=cmd_trigger_daemon)


# ── Cost / Budget commands ────────────────────────────────────────────────────

def _budget_tracker():
    from .agent.telemetry.budget import get_budget_tracker
    return get_budget_tracker()


def cmd_cost(args):
    """Show cost summary for a time period."""
    tracker = _budget_tracker()
    period = "monthly" if args.month else ("weekly" if args.week else "daily")

    if args.by_model:
        rows = tracker.by_model_summary(period=period)
        if not rows:
            print(f"No spend recorded for {period} period.")
            return
        print(f"{'MODEL':<30} {'REQUESTS':>9} {'INPUT':>9} {'OUTPUT':>9} {'COST':>10}")
        print("-" * 72)
        for r in rows:
            print(
                f"{(r['model'] or 'unknown'):<30} "
                f"{r['requests']:>9} "
                f"{r['tokens_input']:>9,} "
                f"{r['tokens_output']:>9,} "
                f"${r['cost_usd']:>9.4f}"
            )
        total = sum(r['cost_usd'] for r in rows)
        print(f"\nTotal: ${total:.4f}")
        return

    if args.export_csv:
        print(tracker.export_csv(period=period), end="")
        return

    total = tracker.total_spent(period)
    label = {"daily": "Today", "weekly": "This week", "monthly": "This month"}[period]

    print(f"TokenPak Cost Summary — {label}")
    print(f"  Spent:  ${total:.4f}")

    # Show budget status if configured
    for p in ("daily", "monthly"):
        status = tracker.get_status(p)
        if status:
            alert_tag = " ⚠️  ALERT" if status.alert_triggered else ""
            print(
                f"  {p.capitalize()} budget: ${status.spent_usd:.4f} / "
                f"${status.limit_usd:.2f} ({status.percent_used:.1f}%){alert_tag}"
            )


def cmd_budget_set(args):
    from .agent.telemetry.budget import load_budget_config, save_budget_config
    cfg = load_budget_config()
    changed = False
    if args.daily is not None:
        cfg.daily_limit_usd = args.daily
        changed = True
    if args.monthly is not None:
        cfg.monthly_limit_usd = args.monthly
        changed = True
    if args.alert_at is not None:
        cfg.alert_at_percent = args.alert_at
        changed = True
    if args.hard_stop is not None:
        cfg.hard_stop = args.hard_stop
        changed = True
    if changed:
        save_budget_config(cfg)
        print("Budget config saved.")
    print(f"  Daily limit:   {f'${cfg.daily_limit_usd:.2f}' if cfg.daily_limit_usd else 'not set'}")
    print(f"  Monthly limit: {f'${cfg.monthly_limit_usd:.2f}' if cfg.monthly_limit_usd else 'not set'}")
    print(f"  Alert at:      {cfg.alert_at_percent:.0f}%")
    print(f"  Hard stop:     {'yes' if cfg.hard_stop else 'no'}")


def cmd_budget_status(args):
    tracker = _budget_tracker()
    printed = False
    for period in ("daily", "monthly"):
        status = tracker.get_status(period)
        if status:
            bar_width = 30
            filled = int(bar_width * min(status.percent_used, 100) / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            alert_tag = " ⚠️  ALERT" if status.alert_triggered else ""
            print(f"{period.capitalize()} budget{alert_tag}")
            print(f"  [{bar}] {status.percent_used:.1f}%")
            print(f"  ${status.spent_usd:.4f} / ${status.limit_usd:.2f} (${status.remaining_usd:.4f} remaining)")
            printed = True
    if not printed:
        print("No budget limits configured. Use `tokenpak budget set --daily N` to set one.")


def cmd_budget_history(args):
    tracker = _budget_tracker()
    period = "monthly" if args.month else "daily"
    rows = tracker.list_spend(limit=args.limit, period=period)
    if not rows:
        print("No spend records found.")
        return
    print(f"{'TIMESTAMP':<22} {'MODEL':<25} {'COST':>10} {'TOKENS_IN':>10} {'TOKENS_OUT':>10}")
    print("-" * 82)
    for r in rows:
        print(
            f"{r['timestamp'][:19]:<22} "
            f"{(r['model'] or 'unknown'):<25} "
            f"${r['cost_usd']:>9.4f} "
            f"{r['tokens_input']:>10,} "
            f"{r['tokens_output']:>10,}"
        )


def _build_cost_parser(sub):
    p_cost = sub.add_parser("cost", help="Show API cost summary")
    p_cost.add_argument("--week", action="store_true", help="Show weekly totals")
    p_cost.add_argument("--month", action="store_true", help="Show monthly totals")
    p_cost.add_argument("--by-model", action="store_true", help="Break down by model")
    p_cost.add_argument("--export-csv", action="store_true", help="Export as CSV")
    p_cost.set_defaults(func=cmd_cost)


def _build_budget_parser(sub):
    p_budget = sub.add_parser("budget", help="Manage budget limits")
    bsub = p_budget.add_subparsers(dest="budget_cmd", required=True)

    p_set = bsub.add_parser("set", help="Configure budget limits")
    p_set.add_argument("--daily", type=float, metavar="USD", help="Daily spend limit in USD")
    p_set.add_argument("--monthly", type=float, metavar="USD", help="Monthly spend limit in USD")
    p_set.add_argument("--alert-at", type=float, metavar="PCT", help="Alert threshold %% (default 80)")
    p_set.add_argument("--hard-stop", action="store_true", default=None, help="Block requests when limit exceeded")
    p_set.set_defaults(func=cmd_budget_set)

    bsub.add_parser("status", help="Show current budget status").set_defaults(func=cmd_budget_status)

    p_hist = bsub.add_parser("history", help="Show recent spend records")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--month", action="store_true", help="Show this month")
    p_hist.set_defaults(func=cmd_budget_history)
