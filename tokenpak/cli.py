"""TokenPak CLI with parallel processing and optimized batch operations."""

import argparse
import hashlib
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Tuple, List
import os
import sys
import socket

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
    if len(args) == 3:
        path, file_type, no_treesitter = args
    else:
        path, file_type = args
        no_treesitter = False
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
    # --watch mode: initial index then watch for changes
    if getattr(args, 'watch', False):
        from tokenpak.agent.vault.watcher import VaultWatcher, WatcherConfig
        # Run initial full index first
        _do_index(args)
        # Then start watcher
        config = WatcherConfig(
            watch_paths=[args.directory],
            debounce_ms=getattr(args, 'debounce', 500),
        )
        watcher = VaultWatcher(config)
        watcher.start(blocking=True)
        return
    _do_index(args)


def _do_index(args):
    """Core index logic (used by cmd_index and watch mode)."""
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



class Colors:
    """ANSI color codes."""
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"
    
    @staticmethod
    def ok(text):
        return f"{Colors.GREEN}✅{Colors.RESET}  {text}"
    
    @staticmethod
    def warn(text):
        return f"{Colors.YELLOW}⚠️{Colors.RESET}   {text}"
    
    @staticmethod
    def fail(text):
        return f"{Colors.RED}❌{Colors.RESET}  {text}"


def cmd_doctor(args):
    """Run comprehensive diagnostics on TokenPak installation."""
    print("\nTOKENPAK  |  Doctor")
    print("──────────────────────────────\n")
    
    results = {"pass": 0, "warn": 0, "fail": 0}
    fixes_needed = []
    
    # Check 1: Python version
    py_major, py_minor, py_micro = sys.version_info[:3]
    py_version = f"{py_major}.{py_minor}.{py_micro}"
    if sys.version_info >= (3, 10):
        print(Colors.ok(f"Python version      {py_version} — OK"))
        results["pass"] += 1
    else:
        print(Colors.fail(f"Python version      {py_version} — requires ≥3.10"))
        results["fail"] += 1
    
    # Check 2: Config file
    config_path = Path.home() / ".tokenpak" / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                json.load(f)
            print(Colors.ok(f"Config file         {config_path} — valid"))
            results["pass"] += 1
        except json.JSONDecodeError:
            print(Colors.fail(f"Config file         {config_path} — invalid JSON"))
            results["fail"] += 1
            fixes_needed.append(("reset config", config_path))
    else:
        print(Colors.warn(f"Config file         {config_path} — not found"))
        results["warn"] += 1
        fixes_needed.append(("create config", config_path))
    
    # Check 3: Vault index
    index_path = Path.home() / ".tokenpak" / "index.json"
    if index_path.exists():
        try:
            with open(index_path) as f:
                data = json.load(f)
                block_count = len(data.get("blocks", []))
            if block_count > 0:
                print(Colors.ok(f"Vault index         {index_path} — {block_count} blocks"))
                results["pass"] += 1
            else:
                print(Colors.warn(f"Vault index         {index_path} — 0 blocks (run: tokenpak index)"))
                results["warn"] += 1
        except json.JSONDecodeError:
            print(Colors.fail(f"Vault index         {index_path} — invalid JSON"))
            results["fail"] += 1
    else:
        print(Colors.warn(f"Vault index         {index_path} — not found"))
        results["warn"] += 1
    
    # Check 4: Proxy port
    proxy_port = 8765
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", proxy_port))
        sock.close()
        if result == 0:
            print(Colors.ok(f"Proxy reachable     port {proxy_port} — OK"))
            results["pass"] += 1
        else:
            print(Colors.warn(f"Proxy reachable     port {proxy_port} — connection refused (run: tokenpak start)"))
            results["warn"] += 1
    except Exception:
        print(Colors.warn(f"Proxy reachable     port {proxy_port} — check failed"))
        results["warn"] += 1
    
    # Check 5: Disk usage
    tokenpak_dir = Path.home() / ".tokenpak"
    try:
        total_size = sum(f.stat().st_size for f in tokenpak_dir.rglob("*") if f.is_file())
        size_mb = total_size / (1024 * 1024)
        if size_mb < 500:
            print(Colors.ok(f"Disk usage          {size_mb:.1f} MB — OK"))
            results["pass"] += 1
        else:
            print(Colors.warn(f"Disk usage          {size_mb:.1f} MB — consider cleanup"))
            results["warn"] += 1
    except Exception:
        print(Colors.warn(f"Disk usage          could not measure"))
        results["warn"] += 1
    
    # Check 6: Log file
    log_path = Path.home() / ".tokenpak" / "debug.log"
    if log_path.exists():
        log_size_mb = log_path.stat().st_size / (1024 * 1024)
        print(Colors.ok(f"Debug log           {log_path} — {log_size_mb:.2f} MB"))
        results["pass"] += 1
    else:
        print(Colors.ok(f"Debug log           (not present)"))
        results["pass"] += 1
    
    # Summary
    print(f"\n──────────────────────────────")
    summary = f"{results['fail']} error{'s' if results['fail'] != 1 else ''}, {results['warn']} warning{'s' if results['warn'] != 1 else ''}."
    print(summary)
    
    if hasattr(args, 'fix') and args.fix and fixes_needed:
        print("\nAuto-fix requested. Fixing issues...")
        for fix_type, fix_path in fixes_needed:
            if fix_type == "create config":
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_config = {"version": "1.0", "port": 8765, "compress": True}
                with open(fix_path, "w") as f:
                    json.dump(default_config, f, indent=2)
                print(f"  ✓ Created {fix_path}")
            elif fix_type == "reset config":
                # Backup before overwriting
                backup_path = Path(str(fix_path) + ".backup")
                if fix_path.exists():
                    fix_path.rename(backup_path)
                    print(f"  ✓ Backed up invalid config to {backup_path}")
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_config = {"version": "1.0", "port": 8765, "compress": True}
                with open(fix_path, "w") as f:
                    json.dump(default_config, f, indent=2)
                print(f"  ✓ Recreated {fix_path}")
    
    if results["fail"] > 0:
        sys.exit(1)


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
    p_index.add_argument("--watch", action="store_true",
                         help="Watch directory and auto-reindex on file changes")
    p_index.add_argument("--debounce", type=int, default=500,
                         help="Debounce delay in ms for watch mode (default: 500)")
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

    p_doctor = sub.add_parser("doctor", help="Run system diagnostics")
    p_doctor.add_argument("--fix", action="store_true", help="Auto-fix issues where possible")
    p_doctor.set_defaults(func=cmd_doctor)

    _build_trigger_parser(sub)
    _build_cost_parser(sub)
    _build_budget_parser(sub)
    _build_agent_parser(sub)
    _build_replay_parser(sub)
    _build_status_parser(sub)

    return parser


def cmd_status(args):
    """Show system status including recent retry events."""
    import time as _time
    from .agent.agentic.retry import load_recent_retry_events

    print("TokenPak Status\n" + "─" * 40)

    # ── Recent retry events ──
    n = getattr(args, "limit", 20)
    events = load_recent_retry_events(n=n)
    if not events:
        print("\n  Retry events: none\n")
    else:
        print(f"\n  Recent retry events (last {len(events)}):\n")
        for ev in events:
            ts = ev.get("timestamp", 0)
            try:
                ts_str = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(ts))
            except Exception:
                ts_str = str(ts)
            event_type = ev.get("event", "unknown")
            task = ev.get("task_id") or ev.get("task") or "—"
            extra = ""
            if ev.get("http_status"):
                extra += f" [HTTP {ev['http_status']}]"
            if ev.get("error"):
                extra += f" — {ev['error'][:60]}"
            if ev.get("from_model") and ev.get("to_model"):
                extra += f" {ev['from_model']} → {ev['to_model']}"
            if ev.get("from_provider") and ev.get("to_provider"):
                extra += f" {ev['from_provider']} → {ev['to_provider']}"
            print(f"    {ts_str}  {event_type:<30}  task={task}{extra}")
        print()

    # ── Budget status (if available) ──
    try:
        from .budgeter import BudgetTracker
        tracker = BudgetTracker()
        any_budget = False
        for period in ("daily", "weekly", "monthly"):
            status = tracker.get_status(period)
            if status:
                any_budget = True
                alert_tag = " ⚠️  ALERT" if status.alert_triggered else ""
                print(
                    f"  {period.capitalize()} budget: ${status.spent_usd:.4f} / "
                    f"${status.limit_usd:.2f} ({status.percent_used:.1f}%){alert_tag}"
                )
        if not any_budget:
            print("  Budget: not configured")
    except Exception:
        pass

    print()


def _build_status_parser(sub):
    p_status = sub.add_parser("status", help="Show system status and recent retry events")
    p_status.add_argument("--limit", type=int, default=20, help="Max retry events to show")
    p_status.set_defaults(func=cmd_status)


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



def cmd_trigger_fire(args):
    """Fire an event string immediately — executes all matching enabled triggers."""
    import subprocess
    from .agent.triggers.matcher import match_event
    store = _trigger_store()
    event = args.event
    matched = [t for t in store.list() if t.enabled and match_event(t.event, event)]
    if not matched:
        print(f"No triggers matched event: {event}")
        return
    print(f"Firing event: {event} ({len(matched)} trigger(s))")
    for t in matched:
        print(f"  -> {t.id}  {t.action}")
        cmd = t.action
        if not cmd.startswith("/") and not cmd.startswith("./") and not cmd.startswith("~"):
            cmd = f"tokenpak {cmd}"
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            store.log_fire(t, result.returncode, output)
            if output:
                print(f"     {output[:200]}")
        except subprocess.TimeoutExpired:
            store.log_fire(t, -1, "timeout")
            print("     [timeout]")


_GIT_POST_COMMIT = """#!/bin/sh
# Installed by: tokenpak trigger hook install
tokenpak trigger fire git:commit
"""

_GIT_POST_PUSH = """#!/bin/sh
# Installed by: tokenpak trigger hook install
tokenpak trigger fire git:push
"""


def cmd_trigger_hook(args):
    """Install or uninstall git hooks that emit trigger events."""
    import stat as _stat
    from pathlib import Path as _Path

    subcmd = args.hook_cmd
    git_dir = _Path(".git")
    if not git_dir.exists():
        print("Not in a git repository (no .git directory found).")
        return

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    hooks = {
        "post-commit": _GIT_POST_COMMIT,
        "post-push": _GIT_POST_PUSH,
    }

    if subcmd == "install":
        for name, body in hooks.items():
            hook_path = hooks_dir / name
            existing = hook_path.read_text() if hook_path.exists() else ""
            if "tokenpak trigger fire" in existing:
                print(f"  {name}: already installed (skip)")
            elif existing.strip():
                hook_path.write_text(existing.rstrip() + "\n\n" + body.strip() + "\n")
                print(f"  {name}: appended to existing hook")
            else:
                hook_path.write_text(body)
                hook_path.chmod(hook_path.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)
                print(f"  {name}: installed")
        print("Git hooks installed. Events: git:commit, git:push")

    elif subcmd == "uninstall":
        for name in hooks:
            hook_path = hooks_dir / name
            if not hook_path.exists():
                continue
            body = hook_path.read_text()
            lines = body.splitlines(keepends=True)
            filtered = [l for l in lines if "tokenpak trigger fire" not in l and "Installed by: tokenpak" not in l]
            new_body = "".join(filtered).strip()
            if new_body:
                hook_path.write_text(new_body + "\n")
            else:
                hook_path.unlink()
            print(f"  {name}: uninstalled")
        print("Git hooks removed.")


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

    p_fire = tsub.add_parser("fire", help="Fire an event string and execute matching triggers")
    p_fire.add_argument("event", help="Event string to fire (e.g. git:push, agent:finished:cali)")
    p_fire.set_defaults(func=cmd_trigger_fire)

    p_hook = tsub.add_parser("hook", help="Install/uninstall git hooks for trigger events")
    hsub = p_hook.add_subparsers(dest="hook_cmd", required=True)
    hsub.add_parser("install", help="Install post-commit and post-push git hooks").set_defaults(func=cmd_trigger_hook)
    hsub.add_parser("uninstall", help="Remove tokenpak git hooks").set_defaults(func=cmd_trigger_hook)

    p_watch = tsub.add_parser("watch", help="Start file watcher for file:changed events")
    p_watch.add_argument("paths", nargs="*", help="Paths to watch (default: .)")
    p_watch.set_defaults(func=cmd_trigger_watch)


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

    import time
    while True:
        time.sleep(1)


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
    bsub.add_parser("show", help="Alias for status — show current budget status").set_defaults(func=cmd_budget_status)

    p_hist = bsub.add_parser("history", help="Show recent spend records")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--month", action="store_true", help="Show this month")
    p_hist.set_defaults(func=cmd_budget_history)


# ── agent lock/unlock/locks commands ─────────────────────────────────────────

def cmd_agent_lock(args):
    from .agent.agentic.locks import FileLockManager, LockConflictError
    mgr = FileLockManager(agent_id=args.agent or None)
    try:
        record = mgr.claim(args.path, timeout_s=args.timeout)
        print(f"✅ Lock acquired: {record['path']}")
        print(f"   Agent:   {record['agent']}")
        print(f"   Expires: {record['expires']:.0f} (in {record['expires'] - __import__('time').time():.0f}s)")
    except LockConflictError as e:
        print(f"❌ {e}")
        raise SystemExit(1)


def cmd_agent_unlock(args):
    from .agent.agentic.locks import FileLockManager
    mgr = FileLockManager(agent_id=args.agent or None)
    released = mgr.release(args.path)
    if released:
        print(f"✅ Lock released: {args.path}")
    else:
        print(f"⚠️  No lock held by this agent on: {args.path}")


def cmd_agent_locks(args):
    from .agent.agentic.locks import FileLockManager
    import time
    mgr = FileLockManager(agent_id=args.agent or None)
    mgr.prune_expired()
    locks = mgr.locks()
    if not locks:
        print("No active locks.")
        return
    print(f"{'Path':<50} {'Agent':<15} {'Expires In':>12}")
    print("-" * 80)
    now = time.time()
    for lock in locks:
        remaining = max(0, lock.get("expires", 0) - now)
        path = lock.get("path", "?")
        if len(path) > 49:
            path = "…" + path[-48:]
        print(f"{path:<50} {lock.get('agent','?'):<15} {remaining:>10.0f}s")


def _build_agent_parser(sub):
    p_agent = sub.add_parser("agent", help="Agent coordination (locks, retry)")
    asub = p_agent.add_subparsers(dest="agent_cmd", required=True)

    p_lock = asub.add_parser("lock", help="Claim a file lock")
    p_lock.add_argument("path", help="File path to lock")
    p_lock.add_argument("--timeout", type=int, default=600, metavar="SECONDS", help="Lock TTL in seconds (default 600)")
    p_lock.add_argument("--agent", default=None, help="Agent id override")
    p_lock.set_defaults(func=cmd_agent_lock)

    p_unlock = asub.add_parser("unlock", help="Release a file lock")
    p_unlock.add_argument("path", help="File path to unlock")
    p_unlock.add_argument("--agent", default=None, help="Agent id override")
    p_unlock.set_defaults(func=cmd_agent_unlock)

    p_locks = asub.add_parser("locks", help="List all active locks")
    p_locks.add_argument("--agent", default=None, help="Filter by agent id")
    p_locks.set_defaults(func=cmd_agent_locks)


# ── Replay commands ───────────────────────────────────────────────────────────

def _replay_store_path() -> str:
    """Return the default replay store path (honouring XDG convention)."""
    return str(Path.home() / ".tokenpak" / "replay.db")


def _get_replay_store():
    from .agent.telemetry.replay import get_replay_store
    return get_replay_store(_replay_store_path())


def cmd_replay_list(args):
    """List recent replay entries."""
    store = _get_replay_store()
    entries = store.list(limit=args.limit, provider=args.provider or None)
    if not entries:
        print("No replay entries found.  Run tokenpak via the proxy to capture sessions.")
        return
    print(f"{'':2} {'ID':<10} {'TIMESTAMP':<20} {'PROVIDER/MODEL':<30} {'TOKENS':>12} {'SAVED':>7}")
    print("-" * 88)
    for e in entries:
        has_content = "📦" if e.messages is not None else "  "
        ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        pm = f"{e.provider}/{e.model}"
        if len(pm) > 29:
            pm = pm[:26] + "..."
        tokens_str = f"{e.input_tokens_raw}→{e.input_tokens_sent}"
        print(f"{has_content} {e.replay_id:<10} {ts:<20} {pm:<30} {tokens_str:>12} {e.savings_pct:>6.1f}%")
    print(f"\n{len(entries)} entr{'y' if len(entries)==1 else 'ies'}  (📦 = content captured, eligible for replay)")


def cmd_replay_show(args):
    """Show details of a single replay entry."""
    store = _get_replay_store()
    e = store.get(args.id)
    if e is None:
        print(f"No entry found with id: {args.id}")
        raise SystemExit(1)
    ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    savings_tok = e.tokens_saved
    print(f"Replay Entry: {e.replay_id}")
    print(f"  Timestamp : {ts}")
    print(f"  Provider  : {e.provider}")
    print(f"  Model     : {e.model}")
    print(f"  Tokens raw: {e.input_tokens_raw:,}")
    print(f"  Tokens sent:{e.input_tokens_sent:,}")
    print(f"  Saved     : {savings_tok:,} ({e.savings_pct}%)")
    print(f"  Cost      : ${e.cost_usd:.6f}")
    if e.metadata:
        print(f"  Metadata  : {json.dumps(e.metadata)}")
    if e.messages is not None:
        print(f"\n  Messages  : {len(e.messages)} message(s) captured")
        if getattr(args, 'show_messages', False):
            print(json.dumps(e.messages, indent=2))
    else:
        print(f"\n  Messages  : not captured (content capture was disabled)")
    if e.response is not None and getattr(args, 'show_messages', False):
        print(f"\n  Response:\n{json.dumps(e.response, indent=2)}")


def _compress_messages(messages: list, aggressive: bool = False) -> tuple[str, int]:
    """Compress message content and return (compressed_text, token_count)."""
    from .tokens import count_tokens
    from .processors.text import TextProcessor

    proc = TextProcessor(aggressive=aggressive)
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            # multi-part content (vision etc.)
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content = "\n".join(text_parts)
        compressed = proc.process(content) if content else ""
        parts.append({"role": role, "content": compressed})

    combined = json.dumps(parts)
    return combined, count_tokens(combined)


def cmd_replay_run(args):
    """Re-run a captured session with different settings (zero API cost)."""
    from .tokens import count_tokens

    store = _get_replay_store()
    e = store.get(args.id)
    if e is None:
        print(f"No entry found with id: {args.id}")
        raise SystemExit(1)

    if e.messages is None:
        print(f"Entry {args.id} has no captured messages — cannot replay.")
        print("Enable content capture (proxy content-capture=true) to record messages.")
        raise SystemExit(1)

    model_label = args.model or e.model
    aggressive = getattr(args, 'aggressive', False)
    no_compress = getattr(args, 'no_compress', False)
    show_diff = getattr(args, 'diff', False)

    raw_combined = json.dumps(e.messages)
    raw_tokens = count_tokens(raw_combined)

    print(f"Replaying [{e.replay_id}] — original: {e.provider}/{e.model}")
    print(f"  Re-running as: {model_label}")
    print()

    if no_compress:
        result_tokens = raw_tokens
        mode_label = "no compression"
        compressed_messages = e.messages
    else:
        _compressed, result_tokens = _compress_messages(e.messages, aggressive=aggressive)
        try:
            compressed_messages = json.loads(_compressed)
        except Exception:
            compressed_messages = e.messages
        mode_label = "aggressive compression" if aggressive else "standard compression"

    saved = raw_tokens - result_tokens
    pct = round(saved / max(raw_tokens, 1) * 100, 1)
    orig_saved_pct = e.savings_pct

    print(f"  Mode          : {mode_label}")
    print(f"  Raw tokens    : {raw_tokens:,}")
    print(f"  Result tokens : {result_tokens:,}")
    print(f"  Saved         : {saved:,} ({pct}%)")
    print()
    print(f"  Original run  : {e.input_tokens_raw:,} → {e.input_tokens_sent:,} (-{orig_saved_pct}%)")

    delta = e.input_tokens_sent - result_tokens
    if delta > 0:
        print(f"  Improvement   : -{delta:,} tokens vs original run ✓")
    elif delta < 0:
        print(f"  Delta vs orig : +{abs(delta):,} tokens (original was more compressed)")
    else:
        print(f"  Delta vs orig : no change")

    if show_diff and not no_compress:
        print()
        print("─── Diff (first message) ───")
        orig_first = e.messages[0].get("content", "") if e.messages else ""
        comp_first = compressed_messages[0].get("content", "") if compressed_messages else ""
        if isinstance(orig_first, list):
            orig_first = " ".join(c.get("text","") for c in orig_first if isinstance(c, dict))
        if isinstance(comp_first, list):
            comp_first = " ".join(c.get("text","") for c in comp_first if isinstance(c, dict))
        orig_lines = orig_first.splitlines()
        comp_lines = comp_first.splitlines()
        import difflib
        diff = list(difflib.unified_diff(orig_lines, comp_lines, fromfile="original", tofile="compressed", lineterm=""))
        if diff:
            for line in diff[:60]:
                print(line)
            if len(diff) > 60:
                print(f"... ({len(diff)-60} more diff lines)")
        else:
            print("(no textual diff — content identical)")


def _build_replay_parser(sub):
    p_replay = sub.add_parser("replay", help="List, inspect, and re-run captured sessions")
    rsub = p_replay.add_subparsers(dest="replay_cmd")

    # list
    p_list = rsub.add_parser("list", help="List recent captured sessions")
    p_list.add_argument("--limit", type=int, default=20, help="Max entries to show (default 20)")
    p_list.add_argument("--provider", default=None, help="Filter by provider")
    p_list.set_defaults(func=cmd_replay_list)

    # show
    p_show = rsub.add_parser("show", help="Show full details of a captured session")
    p_show.add_argument("id", help="Replay entry ID")
    p_show.add_argument("--messages", dest="show_messages", action="store_true",
                        help="Print captured message content")
    p_show.set_defaults(func=cmd_replay_show)

    # run (default when an id is passed directly to 'replay')
    p_run = rsub.add_parser("run", help="Re-run a session with different settings (zero API cost)")
    p_run.add_argument("id", help="Replay entry ID")
    p_run.add_argument("--model", default=None, help="Label as a different model")
    p_run.add_argument("--no-compress", dest="no_compress", action="store_true",
                       help="Simulate sending uncompressed")
    p_run.add_argument("--aggressive", action="store_true",
                       help="Apply aggressive compression mode")
    p_run.add_argument("--diff", action="store_true",
                       help="Show unified diff of original vs compressed messages")
    p_run.set_defaults(func=cmd_replay_run)

    def _replay_dispatch(args):
        # Default action when no subcommand given: show list
        args.limit = 20
        args.provider = None
        cmd_replay_list(args)

    p_replay.set_defaults(func=_replay_dispatch)
