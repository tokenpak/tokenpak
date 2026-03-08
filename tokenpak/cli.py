"""TokenPak CLI with parallel processing and optimized batch operations."""

import argparse
import difflib
import hashlib
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple

# ── Progressive Disclosure ────────────────────────────────────────────────────

_FIRST_RUN_FLAG = Path.home() / ".tokenpak" / ".seen_intro"

# Commands shown in quick --help (beginner view)
_QUICK_COMMANDS = ["start", "demo", "cost", "status"]

# All commands grouped for `tokenpak help`
_COMMAND_GROUPS = {
    "Getting Started": [
        ("start", "Start the proxy (localhost:8766)"),
        ("stop", "Stop the running proxy"),
        ("restart", "Restart the proxy"),
        ("demo", "See compression in action"),
        ("cost", "View your API spend"),
        ("status", "Check proxy health"),
        ("logs", "Show recent proxy logs"),
    ],
    "Indexing": [
        ("index", "Index a directory for context retrieval"),
        ("search", "Search indexed content"),
    ],
    "Configuration": [
        ("route", "Manage model routing rules"),
        ("recipe", "Manage compression recipes"),
        ("template", "Manage prompt templates"),
        ("budget", "Set API budget limits"),
        ("config", "Config sync, pull, validate (version control)"),
    ],
    "Versioning": [
        ("version", "Show current versions (proxy, config, cli)"),
        ("update", "Update TokenPak to latest from git/pypi"),
    ],
    "Operations": [
        ("benchmark", "Run compression benchmarks"),
        ("calibrate", "Calibrate worker count for this host"),
        ("doctor", "Run diagnostics"),
        ("debug", "Toggle verbose debug logging"),
        ("learn", "View/reset learned patterns"),
    ],
    "Advanced": [
        ("trigger", "Manage event triggers"),
        ("macro", "Manage and run macros"),
        ("fingerprint", "Fingerprint sync and cache management"),
        ("agent", "Agent coordination (locks, registry)"),
        ("lock", "File lock management"),
        ("run", "Schedule and manage macro runs"),
        ("replay", "Inspect and re-run captured sessions"),
        ("audit", "Enterprise audit log management"),
        ("compliance", "Generate compliance reports"),
        ("validate", "Validate a TokenPak JSON file"),
        ("stats", "Show registry stats"),
        ("serve", "Start proxy/telemetry server (low-level)"),
    ],
}

# All known command names (for typo detection)
_ALL_COMMANDS = [cmd for group in _COMMAND_GROUPS.values() for cmd, _ in group]


def _suggest_command(unknown: str) -> Optional[str]:
    """Return the closest known command name, or None if no good match."""
    matches = difflib.get_close_matches(unknown, _ALL_COMMANDS, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _mark_intro_seen():
    """Write the first-run flag so the welcome message shows only once."""
    try:
        _FIRST_RUN_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _FIRST_RUN_FLAG.touch()
    except Exception:
        pass


def _is_first_run() -> bool:
    return not _FIRST_RUN_FLAG.exists()


def _print_quick_help():
    """Print the beginner-friendly --help output."""
    print(
        "TokenPak — LLM Proxy with Context Compression\n"
        "\n"
        "Quick Start:\n"
        "  start     Start the proxy (localhost:8766)\n"
        "  demo      See compression in action\n"
        "  cost      View your API spend\n"
        "  status    Check proxy health\n"
        "\n"
        "Run `tokenpak help` for all commands.\n"
        "Run `tokenpak <command> --help` for command details."
    )


def _print_full_help():
    """Print the power-user grouped help output."""
    print("TokenPak — LLM Proxy with Context Compression\n")
    print("All Commands:\n")
    for group_name, commands in _COMMAND_GROUPS.items():
        print(f"  {group_name}:")
        for cmd, desc in commands:
            print(f"    {cmd:<14} {desc}")
        print()
    print("Run `tokenpak <command> --help` for command details.")


def cmd_help(args):
    """Show all commands grouped by category (power-user view)."""
    _print_full_help()


# ── Alias commands ────────────────────────────────────────────────────────────


def cmd_start(args):
    """Start the proxy on localhost:8766 (alias for `serve --port 8766`)."""
    import types

    serve_args = types.SimpleNamespace(port=8766, telemetry=False, ingest=False, workers=1)
    cmd_serve(serve_args)


def cmd_stop(args):
    """Stop the running proxy."""
    import signal as _signal

    pid_path = Path.home() / ".tokenpak" / "proxy.pid"
    if not pid_path.exists():
        print("No proxy PID file found. Is the proxy running?")
        print("Tip: run `tokenpak status` to check.")
        return
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, _signal.SIGTERM)
        pid_path.unlink(missing_ok=True)
        print(f"Proxy stopped (PID {pid}).")
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        print("Proxy was not running (stale PID removed).")
    except Exception as e:
        print(f"Error stopping proxy: {e}")


def cmd_restart(args):
    """Restart the proxy (stop + start)."""
    cmd_stop(args)
    time.sleep(1)
    cmd_start(args)


def cmd_logs(args):
    """Show recent proxy logs."""
    log_candidates = [
        Path.home() / ".tokenpak" / "proxy.log",
        Path("/tmp/tokenpak-proxy.log"),
    ]
    lines = getattr(args, "lines", 50)
    for log_path in log_candidates:
        if log_path.exists():
            try:
                all_lines = log_path.read_text(errors="replace").splitlines()
                for line in all_lines[-lines:]:
                    print(line)
                return
            except Exception as e:
                print(f"Could not read {log_path}: {e}")
                return
    print("No proxy log file found.")
    print("The proxy writes logs to ~/.tokenpak/proxy.log when running.")


# ── End Progressive Disclosure ────────────────────────────────────────────────

from .budget import BudgetBlock, quadratic_allocate
from .calibration import calibrate_workers, get_recommended_workers
from .miss_detector import DEFAULT_GAPS_PATH, should_expand_retrieval
from .processors import get_processor
from .registry import Block, BlockRegistry
from .security import secure_write_config
from .tokens import cache_info, count_tokens, truncate_to_tokens
from .walker import walk_directory
from .wire import pack

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
    # --status mode: show stats from BlockRegistry
    if getattr(args, "status", False):
        import os

        from tokenpak.registry import BlockRegistry

        db_path = getattr(args, "db", os.path.join(os.getcwd(), ".tokenpak", "registry.db"))
        if not os.path.exists(db_path):
            print(f"No index found at {db_path}. Run `tokenpak index <directory>` first.")
            return
        registry = BlockRegistry(db_path)
        stats = registry.get_stats()
        total = stats.get("total_files", 0)
        sep = "────────────────────────────────────────"
        print("Vault Index Status")
        print(sep)
        print(f"  Database:            {db_path}")
        print(f"  Total indexed files: {total}")
        if total == 0:
            print("  (no files indexed yet — run: tokenpak index <path>)")
        else:
            by_type = stats.get("by_type", {})
            if by_type:
                print()
                print("  By type:")
                for ftype, info in sorted(by_type.items()):
                    if isinstance(info, dict):
                        print(f"    {ftype:<10} {info.get('files', 0):>6} files")
                    else:
                        print(f"    {ftype:<10} {info:>6} files")
            raw = stats.get("total_raw_tokens", 0)
            compressed = stats.get("total_compressed_tokens", 0)
            ratio = stats.get("compression_ratio", 0)
            print()
            print(f"  Tokens raw:          {raw:,}")
            print(f"  Tokens compressed:   {compressed:,}")
            if ratio:
                print(f"  Compression ratio:   {ratio:.2f}x")
        return

    if not args.directory:
        import argparse

        raise argparse.ArgumentError(None, "directory is required when --status is not set")

    # --watch mode: initial index then watch for changes
    if getattr(args, "watch", False):
        from tokenpak.agent.vault.watcher import VaultWatcher, WatcherConfig

        # Run initial full index first
        _do_index(args)
        # Then start watcher
        config = WatcherConfig(
            watch_paths=[args.directory],
            debounce_ms=getattr(args, "debounce", 500),
        )
        watcher = VaultWatcher(config)
        watcher.start(blocking=True)
        return
    if not args.directory:
        print("error: directory is required when --status is not set")
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

    workers = getattr(args, "workers", 1) or 1

    if getattr(args, "recalibrate", False):
        result = calibrate_workers(
            args.directory,
            max_workers=getattr(args, "max_workers", 8),
            rounds=getattr(args, "calibration_rounds", 2),
        )
        if "error" in result:
            print(f"Calibration skipped: {result['error']}")
        else:
            print(
                f"Calibration complete: best_workers={result['best_workers']} on {result['sample_files']} files"
            )

    if getattr(args, "auto_workers", False):
        workers = get_recommended_workers(
            default_workers=max(1, workers), max_workers=getattr(args, "max_workers", 8)
        )
        print(f"Auto workers selected: {workers}")

    no_treesitter = getattr(args, "no_treesitter", False)

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

    print(
        f"Indexed: {processed} files in {elapsed:.2f}s ({processed/max(elapsed,0.001):.1f} files/sec)"
    )
    print(f"Skipped: {skipped} | Unchanged: {unchanged}")
    print(f"Token cache: {cache_info()}")
    print(json.dumps(stats, indent=2))


def cmd_search(args):
    """Search indexed content."""
    registry = BlockRegistry(args.db)

    # Retrieval expansion: if query overlaps with a prior miss, double top_k
    top_k = args.top_k
    gaps_path = getattr(args, "gaps", DEFAULT_GAPS_PATH)
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
        budget_blocks.append(
            BudgetBlock(
                ref=f"{m.path}#v{m.version}",
                relevance_score=0.8,
                recency_score=0.6,
                quality_score=m.quality_score,
                type_weight=type_weights.get(m.file_type, 0.5),
            )
        )

    alloc = quadratic_allocate(budget_blocks, args.budget)

    wire_blocks = []
    for m in matches:
        ref = f"{m.path}#v{m.version}"
        max_tokens = alloc.get(ref, 200)
        content, token_count = truncate_to_tokens(m.compressed_content, max_tokens)
        wire_blocks.append(
            {
                "ref": ref,
                "type": m.file_type,
                "quality": m.quality_score,
                "tokens": token_count,
                "content": content,
            }
        )

    if getattr(args, "inject_refs", False):
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
        print(
            json.dumps(
                {
                    "requests_total": requests_total,
                    "requests_errors": requests_errors,
                    "avg_ratio": avg_ratio,
                    "avg_latency_ms": avg_latency,
                    "uptime": uptime_str,
                },
                indent=2,
            )
        )
        return

    print("TokenPak Compression Stats (last 100 requests)")
    print(SEP)
    print(f"{'Requests:':<17}{requests_total} total, {requests_errors} errors")
    print(f"{'Avg ratio:':<17}{avg_ratio} ({pct_reduction}% token reduction)")
    print(f"{'Avg latency:':<17}{avg_latency}ms")
    print(f"{'Uptime:':<17}{uptime_str}")


def cmd_serve(args):
    """Start monitoring proxy or telemetry server (if available)."""
    if getattr(args, "telemetry", False):
        import uvicorn

        from .telemetry.server import create_app

        str(Path.home() / ".openclaw" / "workspace" / "session.jsonl")
        app = create_app()
        # Phase 5A: register ingest router
        try:
            from .agent.ingest.api import router as ingest_router

            app.include_router(ingest_router)
        except Exception as _ingest_err:
            print(f"[warn] Ingest router not loaded: {_ingest_err}")
        workers = getattr(args, "workers", 1)
        print(f"Starting TokenPak telemetry server on port {args.port} (workers={workers})")
        uvicorn.run(app, host="0.0.0.0", port=args.port, workers=workers)
        return
    if getattr(args, "ingest", False):
        import uvicorn

        from .agent.ingest.api import create_ingest_app

        app = create_ingest_app()
        port = args.port
        print(f"TokenPak Ingest API — http://127.0.0.1:{port}")
        print("  POST /ingest")
        print("  POST /ingest/batch")
        print("  GET  /health")
        uvicorn.run(app, host="127.0.0.1", port=port)
        return
    # Multi-worker mode: route to ingest API via uvicorn (proxy doesn't support workers)
    workers = getattr(args, "workers", 1) or 1
    if workers > 1:
        import uvicorn
        from .agent.ingest.api import create_ingest_app
        port = args.port
        print(f"TokenPak Ingest API — http://127.0.0.1:{port}")
        print(f"  Workers: {workers}")
        print("  POST /ingest")
        print("  POST /ingest/batch")
        print("  GET  /health")
        uvicorn.run(
            "tokenpak.agent.ingest.api:create_ingest_app",
            host="127.0.0.1",
            port=port,
            workers=workers,
            factory=True,
        )
        return
    # Default (single-worker): start the TokenPak proxy server
    shutdown_timeout = getattr(args, "shutdown_timeout", None)
    try:
        from .agent.proxy.server import start_proxy

        start_proxy(
            host="127.0.0.1",
            port=args.port,
            blocking=True,
            shutdown_timeout=shutdown_timeout,
        )
    except Exception as e:
        # Fallback for legacy proxy.py deployments
        try:
            import sys

            proxy_path = str(Path.home() / ".openclaw" / "workspace" / ".ocp")
            if proxy_path not in sys.path:
                sys.path.insert(0, proxy_path)
            import proxy

            proxy.run_proxy(args.port)
        except Exception:
            print(f"Serve mode unavailable: {e}")
            print("Run the existing proxy directly if needed.")


def cmd_benchmark(args):
    """Run compression benchmark (default) or latency benchmark (--latency)."""
    file_arg = getattr(args, "file", None)
    use_samples = getattr(args, "samples", False)
    as_json = getattr(args, "json", False)
    latency_mode = getattr(args, "latency", False)

    if latency_mode:
        # Legacy latency benchmark — requires a directory
        directory = getattr(args, "directory", None) or "."
        from .benchmark import run_benchmark

        run_benchmark(directory, args.iterations, compare=args.compare)
    else:
        # Compression benchmark (new default)
        from .benchmark import run_compression_benchmark

        run_compression_benchmark(file=file_arg, use_samples=use_samples, as_json=as_json)


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
                print(
                    Colors.warn(
                        f"Vault index         {index_path} — 0 blocks (run: tokenpak index)"
                    )
                )
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
            print(
                Colors.warn(
                    f"Proxy reachable     port {proxy_port} — connection refused (run: tokenpak start)"
                )
            )
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
        print(Colors.warn("Disk usage          could not measure"))
        results["warn"] += 1

    # Check 6: Log file
    log_path = Path.home() / ".tokenpak" / "debug.log"
    if log_path.exists():
        log_size_mb = log_path.stat().st_size / (1024 * 1024)
        print(Colors.ok(f"Debug log           {log_path} — {log_size_mb:.2f} MB"))
        results["pass"] += 1
    else:
        print(Colors.ok("Debug log           (not present)"))
        results["pass"] += 1

    # Check 7: Required directories exist
    required_dirs = [
        Path.home() / ".tokenpak",
        Path.home() / ".tokenpak" / "cache",
    ]
    missing_dirs = [d for d in required_dirs if not d.exists()]
    if not missing_dirs:
        print(Colors.ok(f"Required dirs       all present ({len(required_dirs)} checked)"))
        results["pass"] += 1
    else:
        missing_list = ", ".join(str(d) for d in missing_dirs)
        print(Colors.warn(f"Required dirs       missing: {missing_list}"))
        results["warn"] += 1
        fixes_needed.append(("create dirs", missing_dirs))

    # Check 8: Python dependencies installed
    missing_deps = []
    optional_deps = []
    required_packages = [
        ("pathlib", True),
        ("json", True),
        ("sqlite3", True),
        ("aiohttp", False),
        ("fastapi", False),
        ("uvicorn", False),
    ]
    import importlib

    for pkg, is_required in required_packages:
        spec = importlib.util.find_spec(pkg)
        if spec is None:
            if is_required:
                missing_deps.append(pkg)
            else:
                optional_deps.append(pkg)

    if not missing_deps and not optional_deps:
        print(Colors.ok("Dependencies        all packages present"))
        results["pass"] += 1
    elif not missing_deps and optional_deps:
        opt_list = ", ".join(optional_deps)
        print(
            Colors.warn(
                f"Dependencies        optional missing: {opt_list} (run: pip install tokenpak[full])"
            )
        )
        results["warn"] += 1
    else:
        dep_list = ", ".join(missing_deps)
        print(
            Colors.fail(
                f"Dependencies        required missing: {dep_list} (run: pip install tokenpak)"
            )
        )
        results["fail"] += 1

    # Summary
    print("\n──────────────────────────────")
    summary = f"{results['fail']} error{'s' if results['fail'] != 1 else ''}, {results['warn']} warning{'s' if results['warn'] != 1 else ''}."
    print(summary)

    if hasattr(args, "fix") and args.fix and fixes_needed:
        print("\nAuto-fix requested. Fixing issues...")
        for fix_type, fix_path in fixes_needed:
            if fix_type == "create config":
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_config = {"version": "1.0", "port": 8765, "compress": True}
                secure_write_config(fix_path, default_config)
                print(f"  ✓ Created {fix_path} (mode 600)")
            elif fix_type == "reset config":
                # Backup before overwriting
                backup_path = Path(str(fix_path) + ".backup")
                if fix_path.exists():
                    fix_path.rename(backup_path)
                    print(f"  ✓ Backed up invalid config to {backup_path}")
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_config = {"version": "1.0", "port": 8765, "compress": True}
                secure_write_config(fix_path, default_config)
                print(f"  ✓ Recreated {fix_path} (mode 600)")
            elif fix_type == "create dirs":
                for d in fix_path:
                    d.mkdir(parents=True, exist_ok=True)
                    print(f"  ✓ Created {d}")

    if results["fail"] > 0:
        sys.exit(1)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tokenpak",
        description="TokenPak — LLM Proxy with Context Compression",
        add_help=False,  # we handle --help ourselves for progressive disclosure
    )
    parser.add_argument(
        "--help", "-h", action="store_true", default=False, help="Show quick-start help"
    )
    parser.add_argument("--db", default=".tokenpak/registry.db", help="Registry SQLite path")

    sub = parser.add_subparsers(dest="command", required=False)

    # ── Progressive disclosure: help + aliases ────────────────────────────────
    p_help = sub.add_parser("help", help="Show all commands grouped by category")
    p_help.set_defaults(func=cmd_help)

    p_start = sub.add_parser("start", help="Start the proxy (localhost:8766)")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the running proxy")
    p_stop.set_defaults(func=cmd_stop)

    p_restart = sub.add_parser("restart", help="Restart the proxy")
    p_restart.set_defaults(func=cmd_restart)

    p_logs = sub.add_parser("logs", help="Show recent proxy logs")
    p_logs.add_argument(
        "--lines", "-n", type=int, default=50, help="Number of log lines to show (default: 50)"
    )
    p_logs.set_defaults(func=cmd_logs)
    # ── End aliases ───────────────────────────────────────────────────────────

    _build_route_parser(sub)
    _build_validate_parser(sub)

    p_index = sub.add_parser("index", help="Index a directory")
    p_index.add_argument("directory", nargs="?", default=None, help="Directory to index")
    p_index.add_argument("--status", action="store_true", help="Show indexed file count by type")
    p_index.add_argument("--budget", type=int, default=8000)
    p_index.add_argument(
        "--workers", "-w", type=int, default=4, help="Parallel workers (default: 4)"
    )
    p_index.add_argument(
        "--auto-workers",
        action="store_true",
        help="Use hybrid calibration (static baseline + dynamic adjustment)",
    )
    p_index.add_argument(
        "--recalibrate", action="store_true", help="Run static calibration before indexing"
    )
    p_index.add_argument(
        "--calibration-rounds",
        type=int,
        default=2,
        help="Calibration rounds per candidate worker count",
    )
    p_index.add_argument(
        "--max-workers", type=int, default=8, help="Upper worker cap for auto/recalibration"
    )
    p_index.add_argument(
        "--watch", action="store_true", help="Watch directory and auto-reindex on file changes"
    )
    p_index.add_argument(
        "--debounce",
        type=int,
        default=500,
        help="Debounce delay in ms for watch mode (default: 500)",
    )
    p_index.add_argument(
        "--no-treesitter",
        action="store_true",
        help="Force regex-based code processing (skip tree-sitter)",
    )
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="Search indexed content")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--budget", type=int, default=8000)
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument(
        "--gaps",
        default=DEFAULT_GAPS_PATH,
        help="Path to gaps.json for miss-based retrieval expansion",
    )
    p_search.add_argument(
        "--inject-refs",
        action="store_true",
        help="Enable compile-time reference injection (GitHub, URLs)",
    )
    p_search.set_defaults(func=cmd_search)

    p_stats = sub.add_parser("stats", help="Show registry stats")
    p_stats.set_defaults(func=cmd_stats)

    p_serve = sub.add_parser("serve", help="Start monitoring proxy or telemetry server")
    p_serve.add_argument("--port", type=int, default=8766)
    p_serve.add_argument("--telemetry", action="store_true", help="Start telemetry ingest server")
    p_serve.add_argument("--ingest", action="store_true", help="Start Phase 5A ingest API server")
    p_serve.add_argument("--workers", type=int, default=1, help="Number of uvicorn workers")
    p_serve.add_argument(
        "--shutdown-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Seconds to wait for in-flight requests to complete before forcing shutdown "
            "(default: 30, or TOKENPAK_SHUTDOWN_TIMEOUT env var)"
        ),
    )
    p_serve.set_defaults(func=cmd_serve)

    p_bench = sub.add_parser(
        "benchmark", help="Benchmark compression performance on sample or real data"
    )
    p_bench.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="Directory to benchmark (used with --latency mode)",
    )
    p_bench.add_argument("--file", default=None, metavar="PATH", help="Benchmark a specific file")
    p_bench.add_argument(
        "--samples",
        action="store_true",
        help="Use built-in sample data (default when no file/directory given)",
    )
    p_bench.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output results as JSON"
    )
    p_bench.add_argument(
        "--latency",
        action="store_true",
        help="Run latency/indexing benchmark instead of compression benchmark",
    )
    p_bench.add_argument(
        "--iterations", type=int, default=3, help="Iterations for latency benchmark (default: 3)"
    )
    p_bench.add_argument(
        "--compare", action="store_true", help="Compare baseline vs optimized (latency mode only)"
    )
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
    _build_lock_parser(sub)
    _build_agent_parser(sub)
    _build_replay_parser(sub)
    _build_status_parser(sub)
    _build_debug_parser(sub)
    _build_demo_parser(sub)
    _build_run_parser(sub)
    _build_macro_parser(sub)
    _build_fingerprint_parser(sub)
    _build_learn_parser(sub)
    _build_user_template_parser(sub)
    _build_audit_parser(sub)
    _build_compliance_parser(sub)
    _build_version_parser(sub)
    _build_update_parser(sub)
    _build_config_mgmt_parser(sub)

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

    # ── Failover events ──
    try:
        from .agent.proxy.failover_engine import get_event_log

        flog = get_event_log()
        failover_events = flog.get_recent(limit=n)
        if not failover_events:
            print("  Failover events: none\n")
        else:
            print(f"  Recent failover events (last {len(failover_events)}):\n")
            for ev in failover_events:
                ts = ev.get("timestamp", "")[:19].replace("T", " ")
                orig = ev.get("original_provider", "?")
                fail = ev.get("failover_provider", "?")
                etype = ev.get("error_type", "?")
                http_s = ev.get("http_status") or ""
                http_tag = f" [{http_s}]" if http_s else ""
                status_icon = "✅" if ev.get("succeeded") else "❌"
                print(f"    {ts}  {status_icon} {orig} → {fail}{http_tag} ({etype})")
            print()
    except Exception:
        pass

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


def _build_debug_parser(sub):
    """Build debug mode subcommand parser."""
    p_debug = sub.add_parser("debug", help="Toggle verbose debug logging")
    dsub = p_debug.add_subparsers(dest="debug_cmd", required=True)

    dsub.add_parser("on", help="Enable debug mode").set_defaults(func=cmd_debug_on)
    dsub.add_parser("off", help="Disable debug mode").set_defaults(func=cmd_debug_off)
    dsub.add_parser("status", help="Show debug mode state").set_defaults(func=cmd_debug_status)


def cmd_debug_on(args):
    """Enable debug mode."""
    from .agent.config import set_debug_enabled

    set_debug_enabled(True)
    print("✅ Debug mode enabled")
    print("   Debug logs will appear on stderr during proxy requests.")
    print("   Disable with: tokenpak debug off")


def cmd_debug_off(args):
    """Disable debug mode."""
    from .agent.config import set_debug_enabled

    set_debug_enabled(False)
    print("✅ Debug mode disabled")


def cmd_debug_status(args):
    """Show debug mode state."""
    import os

    from .agent.config import CONFIG_PATH, get_debug_enabled

    enabled = get_debug_enabled()
    env_override = os.environ.get("TOKENPAK_DEBUG")

    status = "🟢 ON" if enabled else "⚪ OFF"
    print(f"Debug mode: {status}")

    if env_override is not None:
        print(f"  Source: TOKENPAK_DEBUG env var = {env_override}")
    else:
        print(f"  Source: {CONFIG_PATH}")


def _build_learn_parser(sub):
    """Build `tokenpak learn` subcommand parser."""
    p_learn = sub.add_parser("learn", help="Show or reset learned patterns from telemetry")
    lsub = p_learn.add_subparsers(dest="learn_cmd", required=True)
    lsub.add_parser("status", help="Show learned patterns summary").set_defaults(
        func=cmd_learn_status
    )
    lsub.add_parser("reset", help="Clear all learned data").set_defaults(func=cmd_learn_reset)


def cmd_learn_status(args):
    """Show learned patterns from routing, compression, and context data."""
    from .agent.agentic.learning import cmd_learn_status as _learn_status
    from .agent.agentic.learning import learn

    learn()
    _learn_status()


def cmd_learn_reset(args):
    """Clear all learned data."""
    from .agent.agentic.learning import reset

    reset()
    print("✓ Learning store cleared.")


def _build_user_template_parser(sub):
    """Build `tokenpak template` subcommand parser for local user templates."""
    from .user_templates import (
        cmd_template_add,
        cmd_template_list,
        cmd_template_remove,
        cmd_template_show,
        cmd_template_use,
    )

    p_tmpl = sub.add_parser("template", help="Manage local user prompt templates")
    tsub = p_tmpl.add_subparsers(dest="template_cmd", required=True)

    # list
    tsub.add_parser("list", help="List all saved templates").set_defaults(func=cmd_template_list)

    # add
    p_add = tsub.add_parser("add", help="Add or update a template")
    p_add.add_argument("name", help="Template name")
    p_add.add_argument(
        "--content", default=None, help="Template content (use {{var}} for variables)"
    )
    p_add.set_defaults(func=cmd_template_add)

    # show
    p_show = tsub.add_parser("show", help="Display a template")
    p_show.add_argument("name", help="Template name")
    p_show.set_defaults(func=cmd_template_show)

    # remove
    p_rm = tsub.add_parser("remove", help="Delete a template")
    p_rm.add_argument("name", help="Template name")
    p_rm.set_defaults(func=cmd_template_remove)

    # use
    p_use = tsub.add_parser("use", help="Expand a template with variables")
    p_use.add_argument("name", help="Template name")
    p_use.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Variable substitution (repeatable)",
    )
    p_use.set_defaults(func=cmd_template_use)


# ---------------------------------------------------------------------------
# Enterprise Audit commands
# ---------------------------------------------------------------------------


def _build_audit_parser(sub):
    p_audit = sub.add_parser("audit", help="Enterprise audit log management")
    asub = p_audit.add_subparsers(dest="audit_cmd", required=True)

    p_list = asub.add_parser("list", help="List audit log entries")
    p_list.add_argument(
        "--since",
        default=None,
        metavar="DATE",
        help="Filter entries since date (ISO format, e.g. 2026-01-01)",
    )
    p_list.add_argument("--until", default=None, metavar="DATE", help="Filter entries until date")
    p_list.add_argument("--user", dest="user_id", default=None, help="Filter by user ID")
    p_list.add_argument("--action", default=None, help="Filter by action type")
    p_list.add_argument("--model", default=None, help="Filter by model name")
    p_list.add_argument("--outcome", default=None, help="Filter by outcome (ok/auth_failure/...)")
    p_list.add_argument("--limit", type=int, default=50, help="Max results (default: 50)")
    p_list.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    p_list.add_argument("--db", dest="audit_db", default=None, help="Audit DB path")
    p_list.set_defaults(func=cmd_audit_list)

    p_export = asub.add_parser("export", help="Export audit log to file")
    p_export.add_argument("output", help="Output file path")
    p_export.add_argument(
        "--format",
        dest="fmt",
        choices=["json", "csv"],
        default="json",
        help="Export format (default: json)",
    )
    p_export.add_argument("--since", default=None, metavar="DATE")
    p_export.add_argument("--until", default=None, metavar="DATE")
    p_export.add_argument("--user", dest="user_id", default=None)
    p_export.add_argument("--db", dest="audit_db", default=None, help="Audit DB path")
    p_export.set_defaults(func=cmd_audit_export)

    p_verify = asub.add_parser("verify", help="Verify hash chain integrity")
    p_verify.add_argument("--db", dest="audit_db", default=None, help="Audit DB path")
    p_verify.set_defaults(func=cmd_audit_verify)

    p_prune = asub.add_parser("prune", help="Remove entries older than retention window")
    p_prune.add_argument(
        "--days", type=int, default=90, help="Retention window in days (default: 90)"
    )
    p_prune.add_argument("--db", dest="audit_db", default=None, help="Audit DB path")
    p_prune.set_defaults(func=cmd_audit_prune)

    p_summary = asub.add_parser("summary", help="Show audit log summary stats")
    p_summary.add_argument("--since", default=None, metavar="DATE")
    p_summary.add_argument("--db", dest="audit_db", default=None)
    p_summary.set_defaults(func=cmd_audit_summary)


def _get_audit_db(args) -> str:
    import os
    from pathlib import Path

    if hasattr(args, "audit_db") and args.audit_db:
        return args.audit_db
    home = Path(os.environ.get("TOKENPAK_HOME", Path.home() / ".tokenpak"))
    return str(home / "audit.db")


def cmd_audit_list(args):
    import json as _json

    from tokenpak.enterprise.audit import AuditLog

    db_path = _get_audit_db(args)
    with AuditLog(db_path) as log:
        rows = log.list(
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            user_id=getattr(args, "user_id", None),
            action=getattr(args, "action", None),
            model=getattr(args, "model", None),
            outcome=getattr(args, "outcome", None),
            limit=getattr(args, "limit", 50),
        )
    if getattr(args, "as_json", False):
        print(_json.dumps(rows, indent=2, default=str))
    else:
        if not rows:
            print("No audit entries found.")
            return
        # Pretty table
        header = f"{'Time':<26} {'User':<16} {'Action':<24} {'Model':<28} {'Outcome':<10}"
        print(header)
        print("-" * len(header))
        for r in rows:
            print(
                f"{r.get('ts_iso','')[:25]:<26} "
                f"{(r.get('user_id','') or '-'):<16} "
                f"{r.get('action',''):<24} "
                f"{(r.get('model','') or '-'):<28} "
                f"{r.get('outcome',''):<10}"
            )
        print(f"\n{len(rows)} entries")


def cmd_audit_export(args):
    from tokenpak.enterprise.audit import AuditLog

    db_path = _get_audit_db(args)
    with AuditLog(db_path) as log:
        n = log.export(
            args.output,
            fmt=args.fmt,
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            user_id=getattr(args, "user_id", None),
        )
    print(f"Exported {n} entries → {args.output} ({args.fmt})")


def cmd_audit_verify(args):
    from tokenpak.enterprise.audit import AuditLog

    db_path = _get_audit_db(args)
    with AuditLog(db_path) as log:
        ok, errors = log.verify_chain()
    if ok:
        print("✅ Audit chain integrity: OK")
    else:
        print(f"❌ Audit chain integrity: FAILED ({len(errors)} violation(s))")
        for e in errors:
            print(f"  • {e}")
        sys.exit(1)


def cmd_audit_prune(args):
    from tokenpak.enterprise.audit import AuditLog

    db_path = _get_audit_db(args)
    with AuditLog(db_path) as log:
        n = log.prune(retention_days=args.days)
    print(f"Pruned {n} entries older than {args.days} days from {db_path}")


def cmd_audit_summary(args):
    from tokenpak.enterprise.audit import AuditLog

    db_path = _get_audit_db(args)
    with AuditLog(db_path) as log:
        stats = log.summary(since=getattr(args, "since", None))
    total = stats.get("total", 0)
    print(f"Total audit entries: {total}")
    if stats.get("by_action"):
        print("\nBy action:")
        for r in stats["by_action"][:10]:
            action = r.get("action", "?") if isinstance(r, dict) else r[0]
            n = r.get("n", 0) if isinstance(r, dict) else r[1]
            print(f"  {action:<30} {n}")
    if stats.get("by_outcome"):
        print("\nBy outcome:")
        for r in stats["by_outcome"]:
            outcome = r.get("outcome", "?") if isinstance(r, dict) else r[0]
            n = r.get("n", 0) if isinstance(r, dict) else r[1]
            print(f"  {outcome:<20} {n}")


# ---------------------------------------------------------------------------
# Enterprise Compliance commands
# ---------------------------------------------------------------------------


def _build_compliance_parser(sub):
    p_comp = sub.add_parser("compliance", help="Generate compliance reports (SOC2, GDPR, CCPA)")
    csub = p_comp.add_subparsers(dest="compliance_cmd", required=True)

    p_report = csub.add_parser("report", help="Generate a compliance report")
    p_report.add_argument(
        "--standard",
        choices=["soc2", "gdpr", "ccpa"],
        required=True,
        help="Compliance standard to report against",
    )
    p_report.add_argument(
        "--since", default=None, metavar="DATE", help="Report period start date (ISO)"
    )
    p_report.add_argument(
        "--until", default=None, metavar="DATE", help="Report period end date (ISO)"
    )
    p_report.add_argument(
        "--org", dest="organization", default=None, help="Organization name for the report"
    )
    p_report.add_argument(
        "--output", default=None, metavar="FILE", help="Save report to file (.json or .txt)"
    )
    p_report.add_argument("--format", dest="fmt", choices=["json", "text"], default="text")
    p_report.add_argument("--db", dest="audit_db", default=None, help="Audit DB path")
    p_report.set_defaults(func=cmd_compliance_report)


def cmd_compliance_report(args):
    import os

    from tokenpak.enterprise.compliance import ComplianceReporter

    org = getattr(args, "organization", None) or os.environ.get("TOKENPAK_ORG", "Your Organization")
    db_path = _get_audit_db(args)
    reporter = ComplianceReporter(audit_db=db_path, organization=org)
    report = reporter.generate(
        standard=args.standard,
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
    )
    if getattr(args, "output", None):
        report.save(args.output, fmt=args.fmt)
        print(f"Report saved → {args.output}")
    else:
        if args.fmt == "json":
            print(report.as_json())
        else:
            print(report.as_text())


# ── Version Control Commands ──────────────────────────────────────────────────

PROXY_VERSION = "0.4.0"
_LOCK_FILE = Path.home() / "vault" / "System" / "tokenpak.lock.json"
_OPENCLAW_CFG = Path.home() / ".openclaw" / "openclaw.json"
_PROXY_URL = "http://localhost:8766"


def _compute_config_hash(cfg: dict) -> str:
    import hashlib as _hl

    normalized = {k: v for k, v in sorted(cfg.items()) if k != "meta"}
    raw = json.dumps(normalized, sort_keys=True).encode()
    return "sha256:" + _hl.sha256(raw).hexdigest()[:12]


def _get_proxy_version() -> dict:
    """Query proxy /version endpoint. Returns dict or raises."""
    import urllib.request as _ur

    try:
        with _ur.urlopen(f"{_PROXY_URL}/version", timeout=3) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _load_lock() -> dict:
    if _LOCK_FILE.exists():
        try:
            return json.loads(_LOCK_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_lock(lock: dict):
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE.write_text(json.dumps(lock, indent=2) + "\n")


def cmd_version(args):
    """Show current versions of proxy, config, and CLI."""
    from tokenpak import __version__ as cli_ver

    # CLI version
    print(f"TokenPak CLI     : {cli_ver}")
    print(f"Proxy (expected) : {PROXY_VERSION}")

    # Proxy version (live)
    proxy_info = _get_proxy_version()
    if "error" in proxy_info:
        print(f"Proxy (running)  : ✗ not reachable ({proxy_info['error']})")
    else:
        uptime = proxy_info.get("uptime", 0)
        h, m = divmod(uptime // 60, 60)
        print(
            f"Proxy (running)  : {proxy_info.get('version', '?')}  uptime={h}h{m:02d}m  python={proxy_info.get('pythonVersion', '?')}"
        )
        print(f"Proxy config hash: {proxy_info.get('configHash', '?')}")

    # openclaw.json meta
    try:
        cfg = json.loads(_OPENCLAW_CFG.read_text())
        meta = cfg.get("meta", {})
        print(f"Config version   : {meta.get('configVersion', 'unknown')}")
        print(f"Config hash      : {meta.get('configHash', 'unknown')}")
        print(f"Last updated     : {meta.get('lastUpdated', 'unknown')}")
    except Exception as e:
        print(f"Config           : ✗ could not read ({e})")

    # Lock file drift check
    lock = _load_lock()
    if lock:
        print(f"\nLock file        : {_LOCK_FILE}")
        print(f"  Locked version : {lock.get('proxyVersion', '?')}")
        print(f"  Locked hash    : {lock.get('configHash', '?')}")
        print(f"  Locked by      : {lock.get('lockedBy', '?')} at {lock.get('lockedAt', '?')}")
        # Drift check
        try:
            cfg = json.loads(_OPENCLAW_CFG.read_text())
            current_hash = _compute_config_hash(cfg)
            if lock.get("configHash") and lock["configHash"] != current_hash:
                print("\n  ⚠️  Config drift detected!")
                print(f"  Lock hash    : {lock['configHash']}")
                print(f"  Current hash : {current_hash}")
                print("  Run `tokenpak config sync` to reconcile.")
            else:
                print("  ✓ Config matches lock file")
        except Exception:
            pass
    else:
        print(f"\n  Lock file not found at {_LOCK_FILE}")


def cmd_update(args):
    """Update TokenPak proxy and CLI to latest."""
    import subprocess as _sp

    check_only = getattr(args, "check", False)
    force = getattr(args, "force", False)
    core_only = getattr(args, "core_only", False)
    dry_run = getattr(args, "dry_run", False)

    if dry_run:
        print("🔍 Dry run — showing what would change (no changes applied)\n")

    # Check latest version from PyPI
    print("Checking for updates...")
    try:
        import urllib.request as _ur

        with _ur.urlopen("https://pypi.org/pypi/tokenpak/json", timeout=5) as resp:
            data = json.loads(resp.read())
            latest = data["info"]["version"]
    except Exception as e:
        print(f"  ✗ Could not reach PyPI: {e}")
        latest = None

    from tokenpak import __version__ as current_ver

    print(f"  Current : {current_ver}")
    if latest:
        print(f"  Latest  : {latest}")
        if latest == current_ver:
            print("  ✓ Already up to date!")
            if not force:
                return
        else:
            print(f"  → Upgrade available: {current_ver} → {latest}")

    if check_only:
        return

    if dry_run:
        print("\nWould run: pip install --upgrade tokenpak")
        print("Would restart proxy if running.")
        return

    # Check if proxy is running first
    proxy_info = _get_proxy_version()
    proxy_running = "error" not in proxy_info

    print("\nUpdating TokenPak...")
    result = _sp.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "tokenpak"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("  ✓ tokenpak updated")
    else:
        print(f"  ✗ pip install failed:\n{result.stderr[:400]}")
        return

    # Restart proxy if it was running
    if proxy_running and not core_only:
        print("\nRestarting proxy...")
        try:
            _sp.Popen(
                [sys.executable, "-m", "tokenpak", "restart"],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
            )
            print("  ✓ Proxy restart initiated")
        except Exception as e:
            print(f"  ⚠ Could not restart proxy: {e}")

    # Update lock file
    try:
        cfg = json.loads(_OPENCLAW_CFG.read_text())
        import datetime as _dt

        lock = {
            "proxyVersion": latest or current_ver,
            "configVersion": cfg.get("meta", {}).get("configVersion", "unknown"),
            "configHash": _compute_config_hash(cfg),
            "lockedAt": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lockedBy": "tokenpak-update",
        }
        _save_lock(lock)
        print(f"  ✓ Lock file updated at {_LOCK_FILE}")
    except Exception as e:
        print(f"  ⚠ Could not update lock file: {e}")

    print("\n✓ Update complete.")


def cmd_config_sync(args):
    """Pull latest config from canonical source (git/vault)."""
    import subprocess as _sp

    source = getattr(args, "source", "git")
    dry_run = getattr(args, "dry_run", False)

    print(f"Syncing config from source: {source}")

    if source == "git":
        vault_dir = Path.home() / "vault"
        if not vault_dir.exists():
            print(f"  ✗ Vault not found at {vault_dir}")
            return
        # Pull latest vault
        result = _sp.run(
            ["bash", str(vault_dir / "scripts" / "vault-sync.sh")],
            capture_output=True,
            text=True,
            cwd=str(vault_dir),
        )
        if result.returncode == 0:
            print("  ✓ Vault synced")
        else:
            print(f"  ⚠ Vault sync output: {result.stdout[-200:]}")

        # Compare lock file with current config
        lock = _load_lock()
        try:
            cfg = json.loads(_OPENCLAW_CFG.read_text())
            current_hash = _compute_config_hash(cfg)
            lock_hash = lock.get("configHash", "")
            if lock_hash and lock_hash != current_hash:
                print("\n  Config drift detected:")
                print(f"    Lock hash    : {lock_hash}")
                print(f"    Current hash : {current_hash}")
                if dry_run:
                    print("  (dry-run: no changes applied)")
                else:
                    print("  Config is in sync after vault pull.")
            else:
                print("  ✓ Config matches lock — no drift")
        except Exception as e:
            print(f"  ⚠ Could not compare hashes: {e}")

    elif source == "url":
        url = getattr(args, "url", None)
        if not url:
            print("  ✗ --url required for source=url")
            return
        try:
            import urllib.request as _ur

            with _ur.urlopen(url, timeout=10) as resp:
                remote_cfg = json.loads(resp.read())
            print(f"  ✓ Fetched config from {url}")
            if dry_run:
                print("  (dry-run: not applying)")
            else:
                # Merge: remote wins on conflicts, local additions preserved
                cfg = json.loads(_OPENCLAW_CFG.read_text())
                merged = {**remote_cfg, **cfg}  # local wins (conservative)
                merged["meta"] = remote_cfg.get("meta", {})
                _OPENCLAW_CFG.write_text(json.dumps(merged, indent=2))
                print("  ✓ Config merged (local additions preserved)")
        except Exception as e:
            print(f"  ✗ Failed to fetch config: {e}")
    else:
        print(f"  ✗ Unknown source: {source}. Use --source=git or --source=url")


def cmd_config_validate(args):
    """Validate openclaw.json config against expected schema."""
    required_meta_fields = ["configVersion", "tokenpakVersion", "lastUpdated", "configHash"]

    try:
        cfg = json.loads(_OPENCLAW_CFG.read_text())
    except Exception as e:
        print(f"✗ Could not read config: {e}")
        return

    errors = []
    warnings = []

    # Check meta fields
    meta = cfg.get("meta", {})
    for field in required_meta_fields:
        if field not in meta:
            warnings.append(f"meta.{field} missing")

    # Check configHash integrity
    if "configHash" in meta:
        computed = _compute_config_hash(cfg)
        stored = meta["configHash"]
        if stored != computed:
            warnings.append(f"configHash mismatch: stored={stored}, computed={computed}")
        else:
            print(f"  ✓ configHash valid ({stored})")

    # Check lock file consistency
    lock = _load_lock()
    if lock:
        if lock.get("configHash") and lock["configHash"] != _compute_config_hash(cfg):
            warnings.append("Config hash doesn't match lock file")
        else:
            print("  ✓ Lock file consistent")

    if errors:
        print("\n❌ Errors:")
        for e in errors:
            print(f"   {e}")
    if warnings:
        print("\n⚠️  Warnings:")
        for w in warnings:
            print(f"   {w}")
    if not errors and not warnings:
        print("✓ Config valid — all checks passed")


def cmd_config_pull(args):
    """Pull config from git or URL (alias for sync with explicit source)."""
    cmd_config_sync(args)


# ── Parser builders for new commands ─────────────────────────────────────────


def _build_version_parser(sub):
    p = sub.add_parser("version", help="Show current versions (proxy, config, cli)")
    p.set_defaults(func=cmd_version)


def _build_update_parser(sub):
    p = sub.add_parser("update", help="Update TokenPak to latest from git/pypi")
    p.add_argument("--check", action="store_true", help="Check for updates without installing")
    p.add_argument("--force", action="store_true", help="Force update even if already up to date")
    p.add_argument(
        "--core-only",
        action="store_true",
        dest="core_only",
        help="Update core only, skip config merge",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would change without applying",
    )
    p.set_defaults(func=cmd_update)


def _build_config_mgmt_parser(sub):
    p = sub.add_parser("config", help="Config management (sync, pull, validate)")
    csub = p.add_subparsers(dest="config_cmd", required=True)

    # sync
    p_sync = csub.add_parser("sync", help="Sync config from canonical source")
    p_sync.add_argument(
        "--source", choices=["git", "url"], default="git", help="Config source: git (vault) or url"
    )
    p_sync.add_argument("--url", help="URL for source=url")
    p_sync.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_sync.set_defaults(func=cmd_config_sync)

    # pull
    p_pull = csub.add_parser("pull", help="Pull config from git or URL")
    p_pull.add_argument("--source", choices=["git", "url"], default="git")
    p_pull.add_argument("--url", help="URL for source=url")
    p_pull.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_pull.add_argument(
        "--merge", choices=["replace", "merge", "diff"], default="merge", help="Merge strategy"
    )
    p_pull.set_defaults(func=cmd_config_pull)

    # validate
    p_val = csub.add_parser("validate", help="Validate config against schema")
    p_val.set_defaults(func=cmd_config_validate)


# ── End Version Control Commands ──────────────────────────────────────────────


def main():
    parser = build_parser()

    # ── Intercept bare --help / -h for progressive disclosure ─────────────────
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h")):
        _print_quick_help()
        sys.exit(0)

    # ── Intercept unknown commands for typo suggestions ───────────────────────
    raw_cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    known_cmds = set(_ALL_COMMANDS) | {
        # also include argparse-registered commands not in groups
        "help",
        "start",
        "stop",
        "restart",
        "logs",
        "version",
        "update",
        "config",
    }
    if raw_cmd and not raw_cmd.startswith("-") and raw_cmd not in known_cmds:
        suggestion = _suggest_command(raw_cmd)
        print(f"Unknown command '{raw_cmd}'.")
        if suggestion:
            print(f"Did you mean: tokenpak {suggestion}?")
        else:
            # Check for a semantically confusing command
            _COMMAND_HINTS = {
                "compress": "Compression happens automatically through the proxy.\nRun `tokenpak demo` to see it in action.",
                "run": "Use `tokenpak serve` to start the proxy, or `tokenpak start` for a quick alias.",
                "proxy": "Use `tokenpak start` to start the proxy on localhost:8766.",
                "kill": "Use `tokenpak stop` to stop the running proxy.",
            }
            hint = _COMMAND_HINTS.get(raw_cmd)
            if hint:
                print(hint)
            else:
                print("Run `tokenpak help` to see all available commands.")
        sys.exit(1)

    args = parser.parse_args()

    # No subcommand given (shouldn't normally happen after guards above)
    if not args.command:
        _print_quick_help()
        sys.exit(0)

    # ── First-run welcome ──────────────────────────────────────────────────────
    if _is_first_run() and args.command not in ("help",):
        print(
            "👋 Welcome to TokenPak! It looks like this is your first time.\n"
            "   Run `tokenpak demo` to see compression in action.\n"
            "   Run `tokenpak help` to see all available commands.\n"
        )
        _mark_intro_seen()

    # ── Smart defaults ─────────────────────────────────────────────────────────
    # `tokenpak cost` with no period flags → default to today
    if args.command == "cost":
        if not getattr(args, "week", False) and not getattr(args, "month", False):
            pass  # cmd_cost already defaults to "daily" when neither flag set

    args.func(args)


# ── Route commands ────────────────────────────────────────────────────────────


def _get_route_store(args=None):
    from .routing.rules import DEFAULT_ROUTES_PATH, RouteStore

    path = getattr(args, "routes", None) or DEFAULT_ROUTES_PATH
    return RouteStore(path=path)


def cmd_route_list(args):
    """List all routing rules."""
    store = _get_route_store(args)
    rules = store.list()
    if not rules:
        print("No routing rules defined.")
        print(
            "Add one with: tokenpak route add --model 'gpt-4*' --target anthropic/claude-3-haiku-20240307"
        )
        return
    print(f"{'ID':<10} {'PRI':>4} {'EN':<4} {'PATTERN':<45} TARGET")
    print("-" * 90)
    for r in rules:
        pat_parts = []
        if r.pattern.model:
            pat_parts.append(f"model={r.pattern.model}")
        if r.pattern.prefix:
            pat_parts.append(f"prefix={r.pattern.prefix!r}")
        if r.pattern.min_tokens is not None:
            pat_parts.append(f"min_tokens={r.pattern.min_tokens}")
        if r.pattern.max_tokens is not None:
            pat_parts.append(f"max_tokens={r.pattern.max_tokens}")
        pat_str = ", ".join(pat_parts) or "(any)"
        enabled = "yes" if r.enabled else "no"
        desc = f"  # {r.description}" if r.description else ""
        print(f"{r.id:<10} {r.priority:>4} {enabled:<4} {pat_str:<45} {r.target}{desc}")


def cmd_route_add(args):
    """Add a routing rule."""
    from .routing.rules import parse_pattern_args

    store = _get_route_store(args)
    try:
        pattern = parse_pattern_args(
            model=getattr(args, "model", None),
            prefix=getattr(args, "prefix", None),
            min_tokens=getattr(args, "min_tokens", None),
            max_tokens=getattr(args, "max_tokens", None),
        )
    except ValueError as e:
        print(f"❌ {e}")
        raise SystemExit(1)

    rule = store.add(
        pattern=pattern,
        target=args.target,
        priority=getattr(args, "priority", 100),
        description=getattr(args, "description", "") or "",
    )
    print(f"✅ Rule added: id={rule.id}  priority={rule.priority}  target={rule.target}")
    _print_rule_pattern(rule)


def _print_rule_pattern(rule):
    pat = rule.pattern
    if pat.model:
        print(f"   Pattern: model glob = {pat.model!r}")
    if pat.prefix:
        print(f"   Pattern: prefix = {pat.prefix!r}")
    if pat.min_tokens is not None:
        print(f"   Pattern: min_tokens = {pat.min_tokens}")
    if pat.max_tokens is not None:
        print(f"   Pattern: max_tokens = {pat.max_tokens}")


def cmd_route_remove(args):
    """Remove a routing rule by id."""
    store = _get_route_store(args)
    removed = store.remove(args.id)
    if removed:
        print(f"✅ Rule {args.id} removed.")
    else:
        print(f"⚠️  No rule found with id={args.id}")
        raise SystemExit(1)


def cmd_route_test(args):
    """Show which rule would match a given prompt."""
    from .routing.rules import RouteEngine, _count_tokens_approx

    store = _get_route_store(args)
    engine = RouteEngine(store=store)

    prompt = args.prompt or ""
    model = getattr(args, "model", "") or ""
    token_count = getattr(args, "tokens", None)

    if token_count is None and prompt:
        token_count = _count_tokens_approx(prompt)

    print(
        f"Testing: model={model!r}  prompt={prompt[:60]!r}{'...' if len(prompt) > 60 else ''}  tokens≈{token_count}"
    )
    print()

    match = engine.match(model=model, prompt=prompt, token_count=token_count)
    if match:
        print(f"✅ Matched rule: id={match.id}  priority={match.priority}")
        print(f"   Target: {match.target}")
        _print_rule_pattern(match)
        if match.description:
            print(f"   Note: {match.description}")
    else:
        print("❌ No rule matched — request would use original model.")

    # Show all rules and their match status
    rules = store.list()
    if rules and getattr(args, "verbose", False):
        print()
        print("All rules evaluated:")
        for r in rules:
            from .routing.rules import RouteEngine as _RE

            did_match = _RE._matches(r.pattern, model=model, prompt=prompt, token_count=token_count)
            tag = "✓" if (did_match and r.enabled) else ("skip" if not r.enabled else "✗")
            print(f"  [{tag}] {r.id}  {r.target}")


def cmd_route_enable(args):
    """Enable a routing rule."""
    store = _get_route_store(args)
    ok = store.set_enabled(args.id, True)
    print(f"✅ Rule {args.id} enabled." if ok else f"⚠️  Rule {args.id} not found.")


def cmd_route_disable(args):
    """Disable a routing rule."""
    store = _get_route_store(args)
    ok = store.set_enabled(args.id, False)
    print(f"✅ Rule {args.id} disabled." if ok else f"⚠️  Rule {args.id} not found.")


def _build_route_parser(sub):
    p_route = sub.add_parser("route", help="Manage manual model routing rules")
    rsub = p_route.add_subparsers(dest="route_cmd", required=True)

    # Common --routes flag
    _routes_flag = dict(
        flag="--routes",
        kwargs=dict(default=None, help="Path to routes.yaml (default: ~/.tokenpak/routes.yaml)"),
    )

    # route list
    p_list = rsub.add_parser("list", help="Show all routing rules")
    p_list.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_list.set_defaults(func=cmd_route_list)

    # route add
    p_add = rsub.add_parser("add", help="Add a routing rule")
    p_add.add_argument(
        "--model", default=None, help="Model glob pattern (e.g. 'gpt-4*', 'openai/*')"
    )
    p_add.add_argument("--prefix", default=None, help="Prompt prefix match (case-insensitive)")
    p_add.add_argument(
        "--min-tokens",
        dest="min_tokens",
        type=int,
        default=None,
        help="Minimum token count (inclusive)",
    )
    p_add.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=None,
        help="Maximum token count (inclusive)",
    )
    p_add.add_argument(
        "--target",
        required=True,
        help="Target model/provider (e.g. 'anthropic/claude-3-haiku-20240307')",
    )
    p_add.add_argument(
        "--priority",
        type=int,
        default=100,
        help="Rule priority (lower = higher priority, default 100)",
    )
    p_add.add_argument("--description", default="", help="Optional description")
    p_add.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_add.set_defaults(func=cmd_route_add)

    # route remove
    p_rm = rsub.add_parser("remove", help="Remove a routing rule by id")
    p_rm.add_argument("id", help="Rule ID to remove")
    p_rm.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_rm.set_defaults(func=cmd_route_remove)

    # route test
    p_test = rsub.add_parser("test", help="Show which rule matches a prompt")
    p_test.add_argument("prompt", nargs="?", default="", help="Prompt text to test")
    p_test.add_argument("--model", default="", help="Model name to test against")
    p_test.add_argument(
        "--tokens", type=int, default=None, help="Token count override (default: auto-estimated)"
    )
    p_test.add_argument(
        "--verbose", "-v", action="store_true", help="Show all rules and their match status"
    )
    p_test.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_test.set_defaults(func=cmd_route_test)

    # route enable / disable
    p_en = rsub.add_parser("enable", help="Enable a routing rule")
    p_en.add_argument("id", help="Rule ID")
    p_en.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_en.set_defaults(func=cmd_route_enable)

    p_dis = rsub.add_parser("disable", help="Disable a routing rule")
    p_dis.add_argument("id", help="Rule ID")
    p_dis.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_dis.set_defaults(func=cmd_route_disable)


# ── Trigger commands ──────────────────────────────────────────────────────────


def _trigger_store():
    from .agent.triggers.store import TriggerStore

    return TriggerStore()


def cmd_trigger_list(args):
    import json as _json

    store = _trigger_store()
    triggers = store.list()
    if getattr(args, "json", False):
        print(
            _json.dumps(
                [
                    dict(
                        id=t.id,
                        event=t.event,
                        action=t.action,
                        enabled=t.enabled,
                        created_at=t.created_at,
                    )
                    for t in triggers
                ],
                indent=2,
            )
        )
        return
    if not triggers:
        print("No triggers registered.")
        return
    print(f"{'ID':<10} {'ENABLED':<8} {'EVENT':<35} ACTION")
    print("-" * 75)
    for t in triggers:
        enabled = "yes" if t.enabled else "no"
        print(f"{t.id:<10} {enabled:<8} {t.event:<35} {t.action}")


def cmd_trigger_add(args):
    import json as _json

    store = _trigger_store()
    t = store.add(event=args.event, action=args.action)
    if getattr(args, "json", False):
        print(
            _json.dumps(
                dict(
                    id=t.id,
                    event=t.event,
                    action=t.action,
                    enabled=t.enabled,
                    created_at=t.created_at,
                ),
                indent=2,
            )
        )
        return
    print(f"Trigger added: id={t.id}  event={t.event}  action={t.action}")


def cmd_trigger_remove(args):
    import json as _json

    store = _trigger_store()
    removed = store.remove(args.id)
    if getattr(args, "json", False):
        print(_json.dumps({"removed": removed, "id": args.id}, indent=2))
        return
    if removed:
        print(f"Trigger {args.id} removed.")
    else:
        print(f"No trigger with id={args.id}")


def cmd_trigger_test(args):
    """Dry-run: show which registered triggers would fire for a given event."""
    import json as _json

    from .agent.triggers.matcher import match_event

    store = _trigger_store()
    event = args.event
    matched = [t for t in store.list() if t.enabled and match_event(t.event, event)]
    if getattr(args, "json", False):
        print(
            _json.dumps(
                [dict(id=t.id, event=t.event, action=t.action, would_fire=True) for t in matched],
                indent=2,
            )
        )
        return
    print(f"Testing event: {event}")
    if not matched:
        print("  No triggers would fire.")
    for t in matched:
        print(f"  ✓ {t.id}  {t.event}  →  {t.action}")


def cmd_trigger_log(args):
    import json as _json

    store = _trigger_store()
    logs = store.list_logs(limit=args.limit)
    if getattr(args, "json", False):
        print(
            _json.dumps(
                [
                    dict(
                        trigger_id=lg.trigger_id,
                        event=lg.event,
                        action=lg.action,
                        fired_at=lg.fired_at,
                        exit_code=lg.exit_code,
                        output=lg.output,
                    )
                    for lg in logs
                ],
                indent=2,
            )
        )
        return
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
                hook_path.chmod(
                    hook_path.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH
                )
                print(f"  {name}: installed")
        print("Git hooks installed. Events: git:commit, git:push")

    elif subcmd == "uninstall":
        for name in hooks:
            hook_path = hooks_dir / name
            if not hook_path.exists():
                continue
            body = hook_path.read_text()
            lines = body.splitlines(keepends=True)
            filtered = [
                l
                for l in lines
                if "tokenpak trigger fire" not in l and "Installed by: tokenpak" not in l
            ]
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

    p_list = tsub.add_parser("list", help="List all triggers")
    p_list.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_list.set_defaults(func=cmd_trigger_list)

    p_add = tsub.add_parser("add", help="Register a new trigger")
    p_add.add_argument(
        "--event",
        required=True,
        help="Event pattern (e.g. file:changed:*.py, git:commit, cost:daily>5)",
    )
    p_add.add_argument(
        "--action", required=True, help="Action: tokenpak sub-command or shell script path"
    )
    p_add.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_add.set_defaults(func=cmd_trigger_add)

    p_rm = tsub.add_parser("remove", help="Remove a trigger by id")
    p_rm.add_argument("id", help="Trigger ID")
    p_rm.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_rm.set_defaults(func=cmd_trigger_remove)

    p_test = tsub.add_parser("test", help="Dry-run: show which triggers match an event")
    p_test.add_argument("--event", required=True, help="Event string to test")
    p_test.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_test.set_defaults(func=cmd_trigger_test)

    p_log = tsub.add_parser("log", help="Show recent trigger fire log")
    p_log.add_argument("--limit", type=int, default=20)
    p_log.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_log.set_defaults(func=cmd_trigger_log)

    tsub.add_parser("daemon", help="Start background trigger daemon").set_defaults(
        func=cmd_trigger_daemon
    )

    p_fire = tsub.add_parser("fire", help="Fire an event string and execute matching triggers")
    p_fire.add_argument("event", help="Event string to fire (e.g. git:push, agent:finished:cali)")
    p_fire.set_defaults(func=cmd_trigger_fire)

    p_hook = tsub.add_parser("hook", help="Install/uninstall git hooks for trigger events")
    hsub = p_hook.add_subparsers(dest="hook_cmd", required=True)
    hsub.add_parser("install", help="Install post-commit and post-push git hooks").set_defaults(
        func=cmd_trigger_hook
    )
    hsub.add_parser("uninstall", help="Remove tokenpak git hooks").set_defaults(
        func=cmd_trigger_hook
    )

    p_watch = tsub.add_parser("watch", help="Start file watcher for file:changed events")
    p_watch.add_argument("paths", nargs="*", help="Paths to watch (default: .)")
    p_watch.set_defaults(func=cmd_trigger_watch)


def cmd_trigger_watch(args):
    """Start file watcher for file:changed events."""
    import signal

    from .agent.macros.hooks import is_file_watcher_running, start_file_watcher, stop_file_watcher

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
        total = sum(r["cost_usd"] for r in rows)
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
    print(
        f"  Monthly limit: {f'${cfg.monthly_limit_usd:.2f}' if cfg.monthly_limit_usd else 'not set'}"
    )
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
            print(
                f"  ${status.spent_usd:.4f} / ${status.limit_usd:.2f} (${status.remaining_usd:.4f} remaining)"
            )
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
    p_set.add_argument(
        "--alert-at", type=float, metavar="PCT", help="Alert threshold %% (default 80)"
    )
    p_set.add_argument(
        "--hard-stop", action="store_true", default=None, help="Block requests when limit exceeded"
    )
    p_set.set_defaults(func=cmd_budget_set)

    bsub.add_parser("status", help="Show current budget status").set_defaults(
        func=cmd_budget_status
    )
    bsub.add_parser("show", help="Alias for status — show current budget status").set_defaults(
        func=cmd_budget_status
    )

    p_hist = bsub.add_parser("history", help="Show recent spend records")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--month", action="store_true", help="Show this month")
    p_hist.set_defaults(func=cmd_budget_history)


# ── top-level lock subcommand ─────────────────────────────────────────────────


def cmd_lock_claim(args):
    import time as _time

    from .agent.agentic.locks import FileLockManager, LockConflictError

    mgr = FileLockManager(agent_id=args.agent or None, timeout_s=args.timeout)
    try:
        record = mgr.claim(args.path, timeout_s=args.timeout)
        print(f"✅ Lock claimed: {record['path']}")
        print(f"   Agent:      {record['agent']}")
        exp = record["expires"]
        print(f"   Expires in: {exp - _time.time():.0f}s  (at epoch {exp:.0f})")
    except LockConflictError as e:
        print(f"❌ {e}")
        raise SystemExit(1)


def cmd_lock_release(args):
    from .agent.agentic.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    released = mgr.release(args.path)
    if released:
        print(f"✅ Released: {args.path}")
    else:
        print(f"⚠️  No lock held by this agent on: {args.path}")


def cmd_lock_query(args):
    import time as _time

    from .agent.agentic.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    record = mgr.query(args.path)
    if record is None:
        print(f"🔓 Unlocked: {args.path}")
    else:
        remaining = max(0, record.get("expires", 0) - _time.time())
        print(f"🔒 Locked:   {record['path']}")
        print(f"   Agent:      {record['agent']}")
        print(f"   PID:        {record.get('pid', '?')}")
        print(f"   Expires in: {remaining:.0f}s")


def cmd_lock_list(args):
    import time as _time

    from .agent.agentic.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    mgr.prune_expired()
    locks = mgr.locks()
    if not locks:
        print("No active locks.")
        return
    now = _time.time()
    print(f"{'Path':<50} {'Agent':<15} {'Expires In':>12}")
    print("-" * 80)
    for lock in locks:
        remaining = max(0, lock.get("expires", 0) - now)
        path = lock.get("path", "?")
        if len(path) > 49:
            path = "…" + path[-48:]
        print(f"{path:<50} {lock.get('agent', '?'):<15} {remaining:>10.0f}s")


def cmd_lock_renew(args):
    import time as _time

    from .agent.agentic.locks import FileLockManager, LockConflictError, LockExpiredError

    mgr = FileLockManager(agent_id=args.agent or None, timeout_s=args.timeout)
    try:
        record = mgr.renew(args.path, timeout_s=args.timeout)
        exp = record["expires"]
        print(f"🔄 Renewed: {record['path']}")
        print(f"   Agent:      {record['agent']}")
        print(f"   Expires in: {exp - _time.time():.0f}s")
    except LockExpiredError as e:
        print(f"⚠️  {e}")
        raise SystemExit(1)
    except LockConflictError as e:
        print(f"❌ {e}")
        raise SystemExit(1)


def _build_lock_parser(sub):
    p_lock = sub.add_parser("lock", help="File lock management for multi-agent coordination")
    lsub = p_lock.add_subparsers(dest="lock_cmd", required=True)

    # claim
    p_claim = lsub.add_parser("claim", help="Claim a lock on a file or directory")
    p_claim.add_argument("path", help="File or directory path to lock")
    p_claim.add_argument(
        "--timeout",
        type=int,
        default=1800,
        metavar="SECONDS",
        help="Lock TTL in seconds (default 1800 = 30 min)",
    )
    p_claim.add_argument("--agent", default=None, help="Agent id override")
    p_claim.set_defaults(func=cmd_lock_claim)

    # release
    p_release = lsub.add_parser("release", help="Release a held lock")
    p_release.add_argument("path", help="File or directory path to release")
    p_release.add_argument("--agent", default=None, help="Agent id override")
    p_release.set_defaults(func=cmd_lock_release)

    # query
    p_query = lsub.add_parser("query", help="Query who holds a lock on a path")
    p_query.add_argument("path", help="File or directory path to query")
    p_query.add_argument("--agent", default=None, help="Agent id override (for manager context)")
    p_query.set_defaults(func=cmd_lock_query)

    # list
    p_list = lsub.add_parser("list", help="List all active locks")
    p_list.add_argument("--agent", default=None, help="Filter by agent id (display context only)")
    p_list.set_defaults(func=cmd_lock_list)

    # renew (heartbeat)
    p_renew = lsub.add_parser("renew", help="Renew (heartbeat) a held lock to extend its TTL")
    p_renew.add_argument("path", help="File or directory path to renew")
    p_renew.add_argument(
        "--timeout",
        type=int,
        default=1800,
        metavar="SECONDS",
        help="New TTL in seconds (default 1800 = 30 min)",
    )
    p_renew.add_argument("--agent", default=None, help="Agent id override")
    p_renew.set_defaults(func=cmd_lock_renew)


# ── agent lock/unlock/locks commands ─────────────────────────────────────────


def cmd_agent_lock(args):
    from .agent.agentic.locks import FileLockManager, LockConflictError

    mgr = FileLockManager(agent_id=args.agent or None)
    try:
        record = mgr.claim(args.path, timeout_s=args.timeout)
        print(f"✅ Lock acquired: {record['path']}")
        print(f"   Agent:   {record['agent']}")
        print(
            f"   Expires: {record['expires']:.0f} (in {record['expires'] - __import__('time').time():.0f}s)"
        )
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
    import time

    from .agent.agentic.locks import FileLockManager

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


def cmd_agent_list(args):
    """List registered agents."""
    import json as json_mod

    from .agent.agentic.registry import AgentRegistry

    registry = AgentRegistry()
    if args.all:
        agents = registry.list_all()
    else:
        agents = registry.list_active()

    if args.json:
        print(json_mod.dumps([a.to_dict() for a in agents], indent=2))
        return

    if not agents:
        print("No registered agents.")
        return

    print(f"{'ID':<10} {'Name':<12} {'Hostname':<15} {'Status':<10} {'Heartbeat':<12}")
    print("-" * 65)
    for a in agents:
        age = a.heartbeat_age_seconds()
        if age < 60:
            hb = f"{age:.0f}s ago"
        elif age < 3600:
            hb = f"{age/60:.0f}m ago"
        else:
            hb = f"{age/3600:.1f}h ago"
        stale = " (stale)" if a.is_stale() else ""
        print(f"{a.agent_id:<10} {a.name:<12} {a.hostname:<15} {a.status:<10} {hb}{stale}")


def cmd_agent_register(args):
    """Register this agent."""
    import json as json_mod
    import socket

    from .agent.agentic.registry import AgentRegistry

    hostname = args.hostname or socket.gethostname()
    capabilities = {
        "gpu": args.gpu,
        "memory_gb": args.memory,
        "specialties": args.specialties,
        "provider_access": args.providers,
        "max_concurrent": 1,
    }

    registry = AgentRegistry()
    agent_id = registry.register(args.name, hostname, capabilities)

    if args.json:
        agent = registry.get(agent_id)
        print(json_mod.dumps(agent.to_dict(), indent=2))
    else:
        print(f"✅ Registered: {args.name} @ {hostname} (id: {agent_id})")


def cmd_agent_deregister(args):
    """Remove agent from registry."""
    from .agent.agentic.registry import AgentRegistry

    registry = AgentRegistry()
    if registry.deregister(args.agent_id):
        print(f"✅ Deregistered: {args.agent_id}")
    else:
        print(f"⚠️  Agent not found: {args.agent_id}")


def cmd_agent_heartbeat(args):
    """Send heartbeat for agent."""
    from .agent.agentic.registry import AgentRegistry

    registry = AgentRegistry()
    if registry.heartbeat(args.agent_id, status=args.status, current_task=args.task):
        print(f"✅ Heartbeat: {args.agent_id}")
    else:
        print(f"⚠️  Agent not found: {args.agent_id}")


def cmd_agent_match(args):
    """Find agents matching requirements."""
    import json as json_mod

    from .agent.agentic.capabilities import CapabilityMatcher, TaskRequirements

    requirements = TaskRequirements(
        requires_gpu=True if args.gpu else None,
        min_memory_gb=args.memory,
        required_specialties=args.specialty or [],
        required_providers=args.provider or [],
    )

    matcher = CapabilityMatcher()
    matches = matcher.match(requirements)

    if args.json:
        print(json_mod.dumps([m.to_dict() for m in matches], indent=2))
        return

    if not matches:
        print("No matching agents found.")
        return

    print(f"{'Score':<8} {'ID':<10} {'Name':<12} {'Reasons'}")
    print("-" * 60)
    for m in matches:
        reasons = ", ".join(m.reasons[:3]) if m.reasons else "-"
        print(f"{m.score:<8.1f} {m.agent.agent_id:<10} {m.agent.name:<12} {reasons}")


def cmd_agent_prune(args):
    """Remove stale agents."""
    from .agent.agentic.registry import AgentRegistry

    registry = AgentRegistry()
    count = registry.prune_stale()
    if count:
        print(f"✅ Pruned {count} stale agent(s)")
    else:
        print("No stale agents to prune.")


def cmd_agent_handoff(args):
    """Dispatch to handoff command handler."""
    from .agent.cli.commands.handoff import handoff_cmd

    handoff_cmd(args)


def _build_agent_parser(sub):
    p_agent = sub.add_parser("agent", help="Agent coordination (locks, registry, capabilities)")
    asub = p_agent.add_subparsers(dest="agent_cmd", required=True)

    # --- Lock commands ---
    p_lock = asub.add_parser("lock", help="Claim a file lock")
    p_lock.add_argument("path", help="File path to lock")
    p_lock.add_argument(
        "--timeout",
        type=int,
        default=600,
        metavar="SECONDS",
        help="Lock TTL in seconds (default 600)",
    )
    p_lock.add_argument("--agent", default=None, help="Agent id override")
    p_lock.set_defaults(func=cmd_agent_lock)

    p_unlock = asub.add_parser("unlock", help="Release a file lock")
    p_unlock.add_argument("path", help="File path to unlock")
    p_unlock.add_argument("--agent", default=None, help="Agent id override")
    p_unlock.set_defaults(func=cmd_agent_unlock)

    p_locks = asub.add_parser("locks", help="List all active locks")
    p_locks.add_argument("--agent", default=None, help="Filter by agent id")
    p_locks.set_defaults(func=cmd_agent_locks)

    # --- Registry commands ---
    p_list = asub.add_parser("list", help="List registered agents")
    p_list.add_argument("--all", action="store_true", help="Include stale agents")
    p_list.add_argument("--json", action="store_true", help="JSON output")
    p_list.set_defaults(func=cmd_agent_list)

    p_register = asub.add_parser("register", help="Register this agent")
    p_register.add_argument("name", help="Agent name (e.g., trix, sue, cali)")
    p_register.add_argument("--hostname", default=None, help="Hostname (default: auto-detect)")
    p_register.add_argument("--gpu", action="store_true", help="Has GPU")
    p_register.add_argument("--memory", type=float, default=4.0, help="Memory in GB")
    p_register.add_argument(
        "--specialties", nargs="*", default=[], help="Specialties (e.g., code research)"
    )
    p_register.add_argument("--providers", nargs="*", default=["anthropic"], help="Provider access")
    p_register.add_argument("--json", action="store_true", help="JSON output")
    p_register.set_defaults(func=cmd_agent_register)

    p_deregister = asub.add_parser("deregister", help="Remove an agent from registry")
    p_deregister.add_argument("agent_id", help="Agent ID to remove")
    p_deregister.set_defaults(func=cmd_agent_deregister)

    p_heartbeat = asub.add_parser("heartbeat", help="Send heartbeat for an agent")
    p_heartbeat.add_argument("agent_id", help="Agent ID")
    p_heartbeat.add_argument(
        "--status", choices=["active", "busy", "draining"], help="Update status"
    )
    p_heartbeat.add_argument("--task", default=None, help="Current task name")
    p_heartbeat.set_defaults(func=cmd_agent_heartbeat)

    p_match = asub.add_parser("match", help="Find agents matching requirements")
    p_match.add_argument("--gpu", action="store_true", help="Require GPU")
    p_match.add_argument("--memory", type=float, default=None, help="Minimum memory GB")
    p_match.add_argument("--specialty", nargs="*", default=[], help="Required specialties")
    p_match.add_argument("--provider", nargs="*", default=[], help="Required providers")
    p_match.add_argument("--json", action="store_true", help="JSON output")
    p_match.set_defaults(func=cmd_agent_match)

    p_prune = asub.add_parser("prune", help="Remove stale agents")
    p_prune.set_defaults(func=cmd_agent_prune)

    # handoff subcommand
    p_handoff = asub.add_parser("handoff", help="Context handoff between agents")
    hsub = p_handoff.add_subparsers(dest="handoff_cmd", required=True)

    hc = hsub.add_parser("create", help="Create a context handoff")
    hc.add_argument("--from", dest="handoff_from", required=True, help="Sending agent")
    hc.add_argument("--to", dest="handoff_to", required=True, help="Receiving agent")
    hc.add_argument(
        "--ref", action="append", metavar="TYPE:PATH[:DESC]", help="Context ref (repeatable)"
    )
    hc.add_argument("--done", metavar="TEXT", help="What was done")
    hc.add_argument("--next", dest="whats_next", metavar="TEXT", help="What comes next")
    hc.add_argument("--file", action="append", metavar="PATH", help="Relevant file (repeatable)")
    hc.add_argument(
        "--ttl", type=float, default=24.0, metavar="HOURS", help="TTL in hours (default 24)"
    )
    hc.set_defaults(func=cmd_agent_handoff)

    hr = hsub.add_parser("receive", help="Receive and validate a handoff")
    hr.add_argument("handoff_id", help="Handoff ID")
    hr.set_defaults(func=cmd_agent_handoff)

    ha = hsub.add_parser("apply", help="Apply a handoff (load context)")
    ha.add_argument("handoff_id", help="Handoff ID")
    ha.set_defaults(func=cmd_agent_handoff)

    hl = hsub.add_parser("list", help="List handoffs")
    hl.add_argument("--to", dest="handoff_to", metavar="AGENT", help="Filter by recipient")
    hl.add_argument("--from", dest="handoff_from", metavar="AGENT", help="Filter by sender")
    hl.add_argument("--status", metavar="STATUS", help="Filter by status")
    hl.set_defaults(func=cmd_agent_handoff)

    hs = hsub.add_parser("show", help="Show handoff details")
    hs.add_argument("handoff_id", help="Handoff ID")
    hs.set_defaults(func=cmd_agent_handoff)

    he = hsub.add_parser("expire", help="Expire stale handoffs")
    he.set_defaults(func=cmd_agent_handoff)


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
        print(
            f"{has_content} {e.replay_id:<10} {ts:<20} {pm:<30} {tokens_str:>12} {e.savings_pct:>6.1f}%"
        )
    print(
        f"\n{len(entries)} entr{'y' if len(entries)==1 else 'ies'}  (📦 = content captured, eligible for replay)"
    )


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
        if getattr(args, "show_messages", False):
            print(json.dumps(e.messages, indent=2))
    else:
        print("\n  Messages  : not captured (content capture was disabled)")
    if e.response is not None and getattr(args, "show_messages", False):
        print(f"\n  Response:\n{json.dumps(e.response, indent=2)}")


def _compress_messages(messages: list, aggressive: bool = False) -> tuple[str, int]:
    """Compress message content and return (compressed_text, token_count)."""
    from .processors.text import TextProcessor
    from .tokens import count_tokens

    proc = TextProcessor(aggressive=aggressive)
    parts = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            # multi-part content (vision etc.)
            text_parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
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
    aggressive = getattr(args, "aggressive", False)
    no_compress = getattr(args, "no_compress", False)
    show_diff = getattr(args, "diff", False)

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
    print(
        f"  Original run  : {e.input_tokens_raw:,} → {e.input_tokens_sent:,} (-{orig_saved_pct}%)"
    )

    delta = e.input_tokens_sent - result_tokens
    if delta > 0:
        print(f"  Improvement   : -{delta:,} tokens vs original run ✓")
    elif delta < 0:
        print(f"  Delta vs orig : +{abs(delta):,} tokens (original was more compressed)")
    else:
        print("  Delta vs orig : no change")

    if show_diff and not no_compress:
        print()
        print("─── Diff (first message) ───")
        orig_first = e.messages[0].get("content", "") if e.messages else ""
        comp_first = compressed_messages[0].get("content", "") if compressed_messages else ""
        if isinstance(orig_first, list):
            orig_first = " ".join(c.get("text", "") for c in orig_first if isinstance(c, dict))
        if isinstance(comp_first, list):
            comp_first = " ".join(c.get("text", "") for c in comp_first if isinstance(c, dict))
        orig_lines = orig_first.splitlines()
        comp_lines = comp_first.splitlines()
        import difflib

        diff = list(
            difflib.unified_diff(
                orig_lines, comp_lines, fromfile="original", tofile="compressed", lineterm=""
            )
        )
        if diff:
            for line in diff[:60]:
                print(line)
            if len(diff) > 60:
                print(f"... ({len(diff)-60} more diff lines)")
        else:
            print("(no textual diff — content identical)")


def cmd_replay_clear(args):
    """Clear all entries from the replay store."""
    store = _get_replay_store()
    n = store.clear()
    print(f"Cleared {n} replay entr{'y' if n == 1 else 'ies'} from store.")


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
    p_show.add_argument(
        "--messages",
        dest="show_messages",
        action="store_true",
        help="Print captured message content",
    )
    p_show.set_defaults(func=cmd_replay_show)

    # run (default when an id is passed directly to 'replay')
    p_run = rsub.add_parser("run", help="Re-run a session with different settings (zero API cost)")
    p_run.add_argument("id", help="Replay entry ID")
    p_run.add_argument("--model", default=None, help="Label as a different model")
    p_run.add_argument(
        "--no-compress",
        dest="no_compress",
        action="store_true",
        help="Simulate sending uncompressed",
    )
    p_run.add_argument(
        "--aggressive", action="store_true", help="Apply aggressive compression mode"
    )
    p_run.add_argument(
        "--diff", action="store_true", help="Show unified diff of original vs compressed messages"
    )
    p_run.set_defaults(func=cmd_replay_run)

    # clear
    p_clear = rsub.add_parser("clear", help="Remove all entries from the replay store")
    p_clear.set_defaults(func=cmd_replay_clear)

    def _replay_dispatch(args):
        # Default action when no subcommand given: show list
        args.limit = 20
        args.provider = None
        cmd_replay_list(args)

    p_replay.set_defaults(func=_replay_dispatch)


# ── Demo command ──────────────────────────────────────────────────────────────


def _build_demo_parser(sub):
    # ── Recipe SDK ─────────────────────────────────────────────────────────────
    p_recipe = sub.add_parser(
        "recipe", help="Custom recipe development tooling (create/test/validate/benchmark)"
    )
    rsub2 = p_recipe.add_subparsers(dest="recipe_cmd", required=True)

    # recipe create
    p_rcreate = rsub2.add_parser("create", help="Scaffold a new custom recipe YAML file")
    p_rcreate.add_argument("name", help="Recipe name (e.g. my-legal-cleanup)")
    p_rcreate.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory to write the recipe file (default: current dir)",
    )
    p_rcreate.add_argument(
        "--category",
        default="general",
        help="Recipe category: python, markdown, legal, medical, etc.",
    )
    p_rcreate.add_argument("--description", default="", help="Short description")
    p_rcreate.add_argument(
        "--match-mode",
        default="extension",
        help="Pattern match mode: any|extension|filename|content|path_pattern",
    )
    p_rcreate.add_argument(
        "--ext", default="txt", help="File extension hint (for extension match mode)"
    )
    p_rcreate.add_argument(
        "--domain-example",
        default=None,
        metavar="DOMAIN",
        help="Use a domain-specific template: legal | medical",
    )
    p_rcreate.set_defaults(func=cmd_recipe_create)

    # recipe validate
    p_rvalidate = rsub2.add_parser("validate", help="Validate a recipe YAML against the schema")
    p_rvalidate.add_argument("file", help="Path to recipe YAML file")
    p_rvalidate.set_defaults(func=cmd_recipe_validate)

    # recipe test
    p_rtest = rsub2.add_parser("test", help="Test a recipe against sample input")
    p_rtest.add_argument("file", help="Path to recipe YAML file")
    p_rtest.add_argument("--input-text", default=None, help="Raw text to test against")
    p_rtest.add_argument(
        "--input-file", default=None, metavar="FILE", help="Path to a file to use as test input"
    )
    p_rtest.add_argument(
        "--filename-hint",
        default="",
        metavar="FILENAME",
        help="Filename to check pattern matching against (e.g. script.py)",
    )
    p_rtest.set_defaults(func=cmd_recipe_test)

    # recipe benchmark
    p_rbench = rsub2.add_parser(
        "benchmark", help="Benchmark compression ratio and speed for a recipe"
    )
    p_rbench.add_argument("file", help="Path to recipe YAML file")
    p_rbench.add_argument(
        "--samples-file",
        default=None,
        metavar="FILE",
        help="JSON file with list of sample strings (default: auto-generated)",
    )
    p_rbench.add_argument(
        "--runs", type=int, default=5, help="Repetitions per sample for timing (default: 5)"
    )
    p_rbench.set_defaults(func=cmd_recipe_benchmark)

    # ── Demo ───────────────────────────────────────────────────────────────────
    p_demo = sub.add_parser("demo", help="Show OSS compression recipes and apply to sample input")
    p_demo.add_argument("--list", action="store_true", help="List all 50 baked-in recipes")
    p_demo.add_argument(
        "--category",
        default=None,
        help="Filter by category (general, python, javascript, markdown, config, common_patterns)",
    )
    p_demo.add_argument("--recipe", default=None, help="Show details for a specific recipe by name")
    p_demo.add_argument("--file", default=None, help="Show which recipes match a given file path")
    p_demo.set_defaults(func=cmd_demo)


def cmd_demo(args):
    """Show OSS compression recipes and demonstrate recipe selection."""
    from .agent.compression.recipes import get_oss_engine

    engine = get_oss_engine()

    # ── Single recipe detail
    if args.recipe:
        recipe = engine.get_recipe(args.recipe)
        if recipe is None:
            print(f"Recipe '{args.recipe}' not found.")
            print(f"Available: {', '.join(engine.list_recipes()[:5])} ...")
            return
        print(f"┌─ Recipe: {recipe.name}")
        print(f"│  Category   : {recipe.category}")
        print(f"│  Description: {recipe.description}")
        print(f"│  Match mode : {recipe.match_mode}")
        print(f"│  Compression: ~{int(recipe.compression_hint * 100)}% reduction expected")
        print("│  Operations :")
        for op in recipe.operations:
            op_type = op.get("type", "?")
            params = {k: v for k, v in op.items() if k != "type"}
            param_str = ", ".join(f"{k}={v!r}" for k, v in list(params.items())[:3])
            print(f"│    [{op_type}]  {param_str}")
        print("└──")
        return

    # ── File matching
    if args.file:
        print(f"Recipes applicable to: {args.file}")
        matches = engine.recipes_for_file(args.file)
        if not matches:
            print("  (none)")
        for r in matches:
            print(f"  {r.name:<45} [{r.category}]  ~{int(r.compression_hint*100)}% savings")
        return

    # ── List all (optionally filtered by category)
    summary = engine.summary()
    print("TokenPak OSS — Baked-in Compression Recipes")
    print("=" * 50)
    print(f"Total recipes: {summary['total']}")
    print()

    categories = [args.category] if args.category else engine.categories()

    for cat in categories:
        recipes = engine.by_category(cat)
        if not recipes:
            print(f"  [no recipes in category '{cat}']")
            continue
        print(f"  ── {cat} ({len(recipes)}) ──")
        for r in recipes:
            hint = f"~{int(r.compression_hint*100)}%" if r.compression_hint > 0 else "   "
            print(f"    {r.name:<45}  {hint}  {r.description[:60]}")
        print()

    print("Use --recipe <name> for details, --file <path> to see applicable recipes.")


# ── Recipe SDK CLI commands ────────────────────────────────────────────────────


def cmd_recipe_create(args):
    """Scaffold a new custom recipe file."""
    from .agent.recipe_sdk import RecipeSDK

    sdk = RecipeSDK()
    out = sdk.create(
        args.name,
        output_dir=args.output_dir,
        category=args.category or "general",
        description=args.description or "",
        match_mode=args.match_mode or "extension",
        ext=args.ext or "txt",
        domain_example=args.domain_example,
    )
    print(f"✅ Recipe scaffolded: {out}")
    print(f"   Next: tokenpak recipe validate {out}")
    print(f"         tokenpak recipe test {out}")


def cmd_recipe_validate(args):
    """Validate a recipe YAML file against the schema."""
    from .agent.recipe_sdk import RecipeSDK, RecipeValidationError

    sdk = RecipeSDK()
    try:
        warnings = sdk.validate(args.file)
    except RecipeValidationError as exc:
        print(f"❌ Validation FAILED: {exc}")
        raise SystemExit(1)
    if warnings:
        print(f"⚠️  Validation passed with {len(warnings)} warning(s):")
        for w in warnings:
            print(f"   • {w}")
    else:
        print(f"✅ Recipe '{args.file}' is valid — no issues found.")


def cmd_recipe_test(args):
    """Test a recipe against sample input and show compression result."""
    from .agent.recipe_sdk import RecipeSDK, RecipeValidationError

    sdk = RecipeSDK()
    try:
        result = sdk.test(
            args.file,
            input_text=args.input_text,
            input_file=args.input_file,
            filename_hint=args.filename_hint or "",
        )
    except RecipeValidationError as exc:
        print(f"❌ Recipe validation error: {exc}")
        raise SystemExit(1)

    print(f"Recipe test: {args.file}")
    print("─" * 50)
    if result["warnings"]:
        for w in result["warnings"]:
            print(f"  ⚠️  {w}")
    print(
        f"  Pattern match  : {'✅ yes' if result['pattern_match'] else '❌ no (check pattern settings)'}"
    )
    print(f"  Filename hint  : {result['filename_hint']}")
    print(f"  Ops applied    : {', '.join(result['ops_applied']) or '(none)'}")
    print(f"  Input chars    : {result['input_chars']}")
    print(f"  Output chars   : {result['output_chars']}")
    ratio_pct = round(result["compression_ratio"] * 100, 1)
    print(f"  Compression    : {ratio_pct}% removed")
    if result.get("compression_hint") is not None:
        hint_pct = round(result["compression_hint"] * 100, 1)
        print(f"  Hint vs actual : {hint_pct}% expected → {ratio_pct}% actual")
    print()
    print("Output preview:")
    print("─" * 50)
    print(result["output_preview"])


def cmd_recipe_benchmark(args):
    """Benchmark a recipe's compression ratio and throughput."""
    from .agent.recipe_sdk import RecipeSDK, RecipeValidationError

    sdk = RecipeSDK()

    samples = None
    if args.samples_file:
        import json as _json

        raw = open(args.samples_file).read()
        try:
            loaded = _json.loads(raw)
            if isinstance(loaded, list):
                samples = [str(s) for s in loaded]
            else:
                samples = [raw]
        except Exception:
            samples = [raw]

    try:
        result = sdk.benchmark(args.file, samples=samples, runs=args.runs)
    except RecipeValidationError as exc:
        print(f"❌ Recipe validation error: {exc}")
        raise SystemExit(1)

    print(f"Benchmark: {result['recipe']}  [{result['category']}]")
    print("─" * 50)
    print(f"  Samples tested        : {result['samples_tested']}")
    print(f"  Runs per sample       : {result['runs_per_sample']}")
    print(f"  Total chars processed : {result['total_chars_processed']:,}")
    print()
    c = result["compression"]
    print(
        f"  Compression (mean)    : {round(c['mean']*100, 1)}%  "
        f"[min {round(c['min']*100, 1)}% – max {round(c['max']*100, 1)}%]"
    )
    if result["hint_vs_actual"]["hint"] is not None:
        hint_pct = round(result["hint_vs_actual"]["hint"] * 100, 1)
        actual_pct = round(result["hint_vs_actual"]["actual_mean"] * 100, 1)
        delta = actual_pct - hint_pct
        sign = "+" if delta >= 0 else ""
        print(f"  Hint vs actual        : {hint_pct}% → {actual_pct}%  ({sign}{delta:.1f}% delta)")
    t = result["timing_ms"]
    print(
        f"  Timing ms (mean)      : {t['mean']:.3f} ms  "
        f"[min {t['min']:.3f} – max {t['max']:.3f}]"
    )


# ── run: Macro scheduler CLI ──────────────────────────────────────────────────


def cmd_run_cron(args):
    """Schedule a macro to run on a cron expression."""
    from .agent.macros.scheduler import schedule_cron

    scheduled = schedule_cron(
        name=args.name,
        cron_expr=args.cron,
        description=getattr(args, "description", ""),
    )
    print(f"✅ Scheduled '{args.name}' [id: {scheduled.id}]")
    print(f"   Cron:    {scheduled.schedule}")
    print(f"   Command: {scheduled.command}")


def cmd_run_at(args):
    """Schedule a macro to run once at a given time."""
    from .agent.macros.scheduler import schedule_at

    scheduled = schedule_at(
        name=args.name,
        run_at=args.at,
        description=getattr(args, "description", ""),
    )
    print(f"✅ Scheduled '{args.name}' [id: {scheduled.id}]")
    print(f"   At:      {scheduled.schedule}")
    print(f"   Command: {scheduled.command}")


def cmd_run_list_scheduled(args):
    """List all scheduled macro runs."""
    from .agent.macros.scheduler import list_scheduled

    schedules = list_scheduled()
    if not schedules:
        print("No scheduled macros.")
        return
    print(f"{'ID':<10} {'NAME':<25} {'TYPE':<6} {'SCHEDULE':<25} {'COMMAND'}")
    print("-" * 90)
    for s in schedules:
        print(f"{s.id:<10} {s.name:<25} {s.schedule_type:<6} {s.schedule:<25} {s.command}")


def cmd_run_cancel(args):
    """Cancel a scheduled macro run."""
    from .agent.macros.scheduler import cancel_schedule

    ok = cancel_schedule(args.id)
    if ok:
        print(f"✅ Cancelled scheduled run: {args.id}")
    else:
        print(f"❌ No scheduled run found with id: {args.id}")


def _build_run_parser(sub):
    p_run = sub.add_parser("run", help="Schedule and manage macro runs")
    rsub = p_run.add_subparsers(dest="run_cmd", required=True)

    # run <name> --cron "<expr>"
    p_cron = rsub.add_parser("cron", help="Schedule a macro on a cron expression")
    p_cron.add_argument("name", help="Macro name")
    p_cron.add_argument(
        "--cron", required=True, metavar="EXPR", help='Cron expression e.g. "0 9 * * 1-5"'
    )
    p_cron.add_argument("--description", default="", help="Optional description")
    p_cron.set_defaults(func=cmd_run_cron)

    # run <name> --at "<time>"
    p_at = rsub.add_parser("at", help="Schedule a one-shot macro run at a specific time")
    p_at.add_argument("name", help="Macro name")
    p_at.add_argument(
        "--at",
        required=True,
        metavar="TIME",
        help='Time string e.g. "2026-03-06 09:00" or "now + 1 hour"',
    )
    p_at.add_argument("--description", default="", help="Optional description")
    p_at.set_defaults(func=cmd_run_at)

    # run list --scheduled
    p_list = rsub.add_parser("list", help="List all scheduled macro runs")
    p_list.set_defaults(func=cmd_run_list_scheduled)

    # run cancel <id>
    p_cancel = rsub.add_parser("cancel", help="Cancel a scheduled macro run")
    p_cancel.add_argument("id", help="Schedule ID to cancel")
    p_cancel.set_defaults(func=cmd_run_cancel)


# ── macro: Premade macro CLI ──────────────────────────────────────────────────


def cmd_macro_install(args):
    """Install a premade macro."""
    from .agent.macros.premade_macros import install_macro

    try:
        path = install_macro(args.name)
        print(f"✅ Installed macro '{args.name}' → {path}")
    except ValueError as e:
        print(f"❌ {e}")


def cmd_macro_run(args):
    """Run a user-defined YAML macro or a premade macro."""
    import json as _json

    from .agent.macros.engine import MacroEngine
    from .agent.macros.premade_macros import PREMADE_MACROS, format_macro_output, run_macro

    name = args.name
    dry_run = getattr(args, "dry_run", False)
    continue_on_error = getattr(args, "continue_on_error", False)
    raw_vars = getattr(args, "var", []) or []

    # Parse --var KEY=VALUE overrides
    runtime_vars: dict = {}
    for kv in raw_vars:
        if "=" in kv:
            k, v = kv.split("=", 1)
            runtime_vars[k.strip()] = v.strip()
        else:
            print(f"⚠️  Ignoring malformed --var (expected KEY=VALUE): {kv}")

    # Try user-defined YAML macro first
    engine = MacroEngine()
    if engine.exists(name):
        result = engine.run(
            name,
            variables=runtime_vars or None,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
        )
        if getattr(args, "json", False):
            print(_json.dumps(result.to_dict(), indent=2))
        else:
            print(result.format())
        return

    # Fall back to premade macros
    if name in PREMADE_MACROS:
        if dry_run:
            print(
                f"[DRY RUN] Would run premade macro '{name}' ({len(PREMADE_MACROS[name]['steps'])} steps)"
            )
            for step in PREMADE_MACROS[name]["steps"]:
                print(f"  🔍 {step['label']}: {step['cmd']}")
            return
        result_dict = run_macro(name)
        if getattr(args, "json", False):
            print(_json.dumps(result_dict, indent=2))
        else:
            print(format_macro_output(result_dict))
        return

    # Nothing found
    engine_macros = [m.name for m in engine.list()]
    premade = list(PREMADE_MACROS.keys())
    all_names = sorted(set(engine_macros + premade))
    print(f"❌ Unknown macro: '{name}'.")
    if all_names:
        print(f"   Available: {', '.join(all_names)}")


def cmd_macro_list(args):
    """List all available macros (premade + user-defined YAML)."""
    from .agent.macros.engine import MacroEngine
    from .agent.macros.premade_macros import list_macros

    print(f"{'NAME':<25} {'TYPE':<10} DESCRIPTION")
    print("-" * 75)

    # Premade macros
    for m in list_macros():
        print(f"{m['name']:<25} {'premade':<10} {m['description']}")

    # User-defined YAML macros
    engine = MacroEngine()
    user_macros = engine.list()
    for m in user_macros:
        print(f"{m.name:<25} {'yaml':<10} {m.description}")

    if not user_macros:
        print("  (no user macros — use `tokenpak macro create` to add one)")


def cmd_macro_create(args):
    """Create a user-defined YAML macro."""
    from pathlib import Path as _Path

    from .agent.macros.engine import MacroEngine

    engine = MacroEngine()

    # If --file provided, load YAML from file
    if getattr(args, "file", None):
        yaml_text = _Path(args.file).read_text()
        try:
            path = engine.create_from_yaml(yaml_text, overwrite=getattr(args, "overwrite", False))
            print(f"✅ Created macro from file → {path}")
        except Exception as e:
            print(f"❌ {e}")
        return

    # Build from CLI args
    name = getattr(args, "name", None)
    if not name:
        print("❌ --name is required (or use --file to load from YAML)")
        return

    # Parse --step "label:cmd" pairs
    raw_steps = getattr(args, "step", []) or []
    steps = []
    for i, s in enumerate(raw_steps, 1):
        if ":" in s:
            label, cmd = s.split(":", 1)
            steps.append({"name": f"step{i}", "label": label.strip(), "cmd": cmd.strip()})
        else:
            steps.append({"name": f"step{i}", "label": f"Step {i}", "cmd": s.strip()})

    if not steps:
        print("❌ At least one --step is required (e.g., --step 'Check status:tokenpak status')")
        return

    # Parse --var KEY=VALUE defaults
    raw_vars = getattr(args, "var", []) or []
    variables: dict = {}
    for kv in raw_vars:
        if "=" in kv:
            k, v = kv.split("=", 1)
            variables[k.strip()] = v.strip()

    try:
        path = engine.create(
            name=name,
            steps=steps,
            description=getattr(args, "description", "") or "",
            variables=variables or None,
            continue_on_error=getattr(args, "continue_on_error", False),
            overwrite=getattr(args, "overwrite", False),
        )
        print(f"✅ Created macro '{name}' → {path}")
        print(f"   Run it with: tokenpak macro run {name}")
    except Exception as e:
        print(f"❌ {e}")


def cmd_macro_show(args):
    """Show a macro definition."""
    import json as _json

    from .agent.macros.engine import MacroEngine
    from .agent.macros.premade_macros import PREMADE_MACROS

    name = args.name
    engine = MacroEngine()

    if engine.exists(name):
        macro = engine.show(name)
        if getattr(args, "json", False):
            print(_json.dumps(macro.to_dict(), indent=2))
        else:
            print(f"Name:         {macro.name}")
            print(f"Description:  {macro.description or '(none)'}")
            print(
                f"Fail mode:    {'continue-on-error' if macro.continue_on_error else 'fail-fast'}"
            )
            if macro.variables:
                print("Variables:")
                for k, v in macro.variables.items():
                    print(f"  {k} = {v}")
            print(f"Steps ({len(macro.steps)}):")
            for i, step in enumerate(macro.steps, 1):
                print(f"  {i}. [{step.name}] {step.label}")
                print(f"       $ {step.cmd}")
        return

    if name in PREMADE_MACROS:
        macro_data = PREMADE_MACROS[name]
        if getattr(args, "json", False):
            print(_json.dumps({"name": name, **macro_data}, indent=2))
        else:
            print(f"Name:        {name}  (premade)")
            print(f"Description: {macro_data['description']}")
            print(f"Steps ({len(macro_data['steps'])}):")
            for i, step in enumerate(macro_data["steps"], 1):
                print(f"  {i}. [{step['name']}] {step['label']}")
                print(f"       $ {step['cmd']}")
        return

    print(f"❌ Macro '{name}' not found.")


def cmd_macro_delete(args):
    """Delete a user-defined YAML macro."""
    from .agent.macros.engine import MacroEngine

    name = args.name
    engine = MacroEngine()

    if not engine.exists(name):
        print(f"❌ Macro '{name}' not found.")
        return

    if not getattr(args, "yes", False):
        confirm = input(f"Delete macro '{name}'? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    if engine.delete(name):
        print(f"✅ Deleted macro '{name}'.")
    else:
        print(f"❌ Failed to delete macro '{name}'.")


def cmd_macro_hooks(args):
    """List, install, or check hook scripts."""
    from .agent.macros.script_hooks import install_hook, list_hooks

    if args.hook_action == "list":
        hooks = list_hooks()
        print(f"{'HOOK':<20} {'EXISTS':<8} {'EXEC':<8} PATH")
        print("-" * 80)
        for name, info in hooks.items():
            exists = "✅" if info["exists"] else "—"
            executable = "✅" if info["executable"] else "—"
            print(f"{name:<20} {exists:<8} {executable:<8} {info['path']}")
    elif args.hook_action == "install":
        try:
            path = install_hook(args.hook_name)
            print(f"✅ Installed hook stub: {path}")
            print("   Edit this file to customize the hook behavior.")
        except ValueError as e:
            print(f"❌ {e}")


def _build_macro_parser(sub):
    p_macro = sub.add_parser(
        "macro", help="Premade macros, user-defined YAML macros, and script hooks"
    )
    msub = p_macro.add_subparsers(dest="macro_cmd", required=True)

    # macro list
    msub.add_parser("list", help="List all macros (premade + user-defined)").set_defaults(
        func=cmd_macro_list
    )

    # macro create
    p_create = msub.add_parser("create", help="Create a user-defined YAML macro")
    p_create.add_argument("--name", help="Macro name (e.g., my-deploy)")
    p_create.add_argument("--description", default="", help="Short description")
    p_create.add_argument(
        "--step",
        action="append",
        metavar="LABEL:CMD",
        help="Add a step (repeatable). Format: 'Label:command'",
    )
    p_create.add_argument(
        "--var",
        action="append",
        metavar="KEY=VALUE",
        help="Default variable (repeatable). Format: KEY=VALUE",
    )
    p_create.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Keep running if a step fails (default: fail-fast)",
    )
    p_create.add_argument("--file", help="Load macro definition from a YAML file")
    p_create.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite an existing macro with the same name",
    )
    p_create.set_defaults(func=cmd_macro_create)

    # macro run <name>
    p_run = msub.add_parser("run", help="Run a macro (YAML or premade)")
    p_run.add_argument("name", help="Macro name")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print commands without executing them",
    )
    p_run.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Keep running if a step fails",
    )
    p_run.add_argument(
        "--var", action="append", metavar="KEY=VALUE", help="Runtime variable override (repeatable)"
    )
    p_run.add_argument("--json", action="store_true", help="Output raw JSON")
    p_run.set_defaults(func=cmd_macro_run)

    # macro show <name>
    p_show = msub.add_parser("show", help="Show a macro definition")
    p_show.add_argument("name", help="Macro name")
    p_show.add_argument("--json", action="store_true", help="Output raw JSON")
    p_show.set_defaults(func=cmd_macro_show)

    # macro delete <name>
    p_delete = msub.add_parser("delete", help="Delete a user-defined YAML macro")
    p_delete.add_argument("name", help="Macro name")
    p_delete.add_argument(
        "--yes", "-y", action="store_true", default=False, help="Skip confirmation prompt"
    )
    p_delete.set_defaults(func=cmd_macro_delete)

    # macro install <name>  (premade shortcut)
    p_install = msub.add_parser("install", help="Install a premade macro as a local file")
    p_install.add_argument("name", help="Macro name (morning-standup, pre-deploy, weekly-report)")
    p_install.set_defaults(func=cmd_macro_install)

    # macro hooks list / install <name>
    p_hooks = msub.add_parser("hooks", help="Manage proxy lifecycle script hooks")
    hsub = p_hooks.add_subparsers(dest="hook_action", required=True)
    hsub.add_parser("list", help="List all hook scripts and their status").set_defaults(
        func=cmd_macro_hooks
    )
    p_hook_install = hsub.add_parser("install", help="Install a hook stub script")
    p_hook_install.add_argument(
        "hook_name", help="Hook name (on_request, on_response, on_error, on_budget_alert)"
    )
    p_hook_install.set_defaults(func=cmd_macro_hooks)


# ── Fingerprint commands ──────────────────────────────────────────────────────


def _build_fingerprint_parser(sub):
    p_fp = sub.add_parser("fingerprint", help="Fingerprint sync and cache management (Pro+)")
    fpsub = p_fp.add_subparsers(dest="fingerprint_cmd", required=True)

    # fingerprint sync
    p_sync = fpsub.add_parser("sync", help="Generate and sync a fingerprint, receive directives")
    p_sync.add_argument("text", nargs="?", help="Prompt text (or omit to read from stdin)")
    p_sync.add_argument("--file", "-f", dest="input_file", help="Read prompt from file")
    p_sync.add_argument("--messages", dest="messages_file", help="OpenAI messages JSON file")
    p_sync.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be sent without transmitting",
    )
    p_sync.add_argument("--privacy", choices=["minimal", "standard", "full"], default="standard")
    p_sync.add_argument("--ttl", type=int, default=3600, help="Cache TTL in seconds (default 3600)")
    p_sync.add_argument("--skip-cache", action="store_true", default=False)
    p_sync.add_argument("--json", dest="output_json", action="store_true", default=False)
    p_sync.set_defaults(func=cmd_fingerprint_sync)

    # fingerprint cache
    p_cache = fpsub.add_parser("cache", help="Show local directive cache status")
    p_cache.add_argument("--json", dest="output_json", action="store_true", default=False)
    p_cache.set_defaults(func=cmd_fingerprint_cache)

    # fingerprint clear-cache
    p_clear = fpsub.add_parser("clear-cache", help="Clear cached directives")
    p_clear.add_argument(
        "--id", dest="fp_id", default=None, help="Clear only this fingerprint ID (default: all)"
    )
    p_clear.add_argument(
        "--yes", "-y", action="store_true", default=False, help="Skip confirmation prompt"
    )
    p_clear.set_defaults(func=cmd_fingerprint_clear_cache)


def cmd_fingerprint_sync(args):
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    from tokenpak.agent.fingerprint.generator import FingerprintGenerator
    from tokenpak.agent.fingerprint.privacy import PrivacyLevel, apply_privacy
    from tokenpak.agent.fingerprint.sync import FingerprintSync

    gen = FingerprintGenerator()

    if getattr(args, "messages_file", None):
        with open(args.messages_file) as f:
            messages = _json.load(f)
        fingerprint = gen.generate_from_messages(messages)
    elif getattr(args, "input_file", None):
        content = _Path(args.input_file).read_text()
        fingerprint = gen.generate(content)
    elif getattr(args, "text", None):
        fingerprint = gen.generate(args.text)
    elif not _sys.stdin.isatty():
        content = _sys.stdin.read()
        fingerprint = gen.generate(content)
    else:
        print("Error: provide TEXT, --file, --messages, or pipe stdin.", file=_sys.stderr)
        _sys.exit(1)

    privacy_level = PrivacyLevel(args.privacy)
    client = FingerprintSync(ttl=args.ttl, privacy_level=privacy_level)

    if args.dry_run:
        payload = apply_privacy(fingerprint.to_dict(), privacy_level)
        if args.output_json:
            print(
                _json.dumps(
                    {
                        "dry_run": True,
                        "fingerprint_id": fingerprint.fingerprint_id,
                        "payload_preview": payload,
                    },
                    indent=2,
                )
            )
        else:
            print("── Dry Run ─────────────────────────────────")
            print(f"  Fingerprint ID : {fingerprint.fingerprint_id}")
            print(f"  Total tokens   : {fingerprint.total_tokens:,}")
            print(f"  Segments       : {fingerprint.segment_count}")
            print(f"  Privacy level  : {args.privacy}")
            print()
            print("  Payload that would be sent:")
            print(_json.dumps(payload, indent=4))
        return

    try:
        result = client.sync(fingerprint, dry_run=False, skip_cache=args.skip_cache)
    except PermissionError as e:
        print(f"✗ {e}", file=_sys.stderr)
        _sys.exit(1)

    if args.output_json:
        print(
            _json.dumps(
                {
                    "success": result.success,
                    "source": result.source,
                    "fingerprint_id": fingerprint.fingerprint_id,
                    "directives": [d.to_dict() for d in result.directives],
                    "cached_at": result.cached_at,
                    "expires_at": result.expires_at,
                    "error": result.error,
                },
                indent=2,
            )
        )
        return

    status_icon = "✓" if result.success else "⚠"
    source_label = {
        "server": "intelligence server",
        "cache": "local cache",
        "oss_fallback": "OSS fallback",
    }.get(result.source, result.source)

    print(f"{status_icon} Fingerprint synced  [{source_label}]")
    print(f"  ID         : {fingerprint.fingerprint_id}")
    print(f"  Tokens     : {fingerprint.total_tokens:,}")
    print(f"  Directives : {len(result.directives)}")

    if result.error:
        print(f"  Warning    : {result.error}", file=_sys.stderr)

    if result.directives:
        print()
        print("  Directives received:")
        for d in result.directives:
            print(f"    [{d.priority}] {d.action}  — {d.description or d.directive_id}")


def cmd_fingerprint_cache(args):
    import json as _json

    from tokenpak.agent.fingerprint.sync import FingerprintSync

    client = FingerprintSync()
    status = client.cache_status()

    if getattr(args, "output_json", False):
        print(_json.dumps(status, indent=2))
        return

    print("── Fingerprint Cache ────────────────────────")
    print(f"  Cache dir  : {status['cache_dir']}")
    print(f"  TTL        : {status['ttl_seconds']}s")
    print(f"  Entries    : {status['entries']}")
    print(f"  Valid      : {status.get('valid', 0)}")
    print(f"  Expired    : {status.get('expired', 0)}")


def cmd_fingerprint_clear_cache(args):
    import sys as _sys

    from tokenpak.agent.fingerprint.sync import FingerprintSync

    client = FingerprintSync()

    fp_id = getattr(args, "fp_id", None)
    yes = getattr(args, "yes", False)
    scope = f"fingerprint {fp_id}" if fp_id else "ALL cached directives"

    if not yes:
        confirm = input(f"Clear {scope}? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            _sys.exit(0)

    deleted = client.clear_cache(fingerprint_id=fp_id)
    print(f"✓ Cleared {deleted} cache file(s).")


# ── Validate command ──────────────────────────────────────────────────────────


def cmd_validate(args):
    """Validate a TokenPak JSON file against the v1.0 protocol schema."""
    import json as _json
    import sys as _sys

    from tokenpak.validator import TokenPakValidator

    validator = TokenPakValidator()
    result = validator.validate_file(args.file, verbose=getattr(args, "verbose", False))

    if getattr(args, "json_output", False):
        print(_json.dumps(result.to_dict(), indent=2))
        _sys.exit(0 if result.valid else 1)

    # Human-readable output
    print("\nTokenPak Validator v1.0")
    print(f"File : {args.file}")
    print("─" * 50)

    if not result.issues:
        print("  ✓ No issues found.")
    else:
        for issue in result.issues:
            print(str(issue))

    print("─" * 50)
    print(f"{result.summary()}\n")

    if not result.valid:
        _sys.exit(1)


def _build_validate_parser(sub):
    p = sub.add_parser("validate", help="Validate a TokenPak JSON file against the v1.0 schema")
    p.add_argument("file", help="Path to the .json TokenPak file")
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show quality hints in addition to errors/warnings",
    )
    p.add_argument(
        "--json", dest="json_output", action="store_true", help="Output validation result as JSON"
    )
    p.set_defaults(func=cmd_validate)
    return p


if __name__ == "__main__":
    main()
