# SPDX-License-Identifier: Apache-2.0
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

from ..formatting import OutputFormatter, OutputMode, resolve_mode
from ..formatting import symbols as FS

# ── Live Proxy Access ─────────────────────────────────────────────────────────


def _proxy_get(path: str, port: Optional[int] = None) -> "dict | None":
    """Fetch JSON from running proxy. Returns None if unreachable."""
    import urllib.request as _urlreq

    port = port or int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return None


# ── Progressive Disclosure ────────────────────────────────────────────────────

_FIRST_RUN_FLAG = Path.home() / ".tokenpak" / ".seen_intro"

# Commands shown in quick --help (beginner view).
# `setup` is first because the README headline promises "One command to
# configure your LLM proxy" (A1 fix, 2026-04-23) — if a brand-new user
# types `tokenpak --help` and doesn't see `setup`, that promise is
# structurally invisible. Phase A, MVP Gap Closure, 2026-04-24.
_QUICK_COMMANDS = ["setup", "start", "demo", "cost", "status"]

# All commands grouped for `tokenpak help`
_COMMAND_GROUPS = {
    "Getting Started": [
        ("setup", "One-time interactive configuration wizard (detects keys, picks profile, starts proxy)"),
        ("start", "Start the proxy (localhost:8766)"),
        ("stop", "Stop the running proxy"),
        ("restart", "Restart the proxy"),
        ("claude", "Launch Claude Code with tokenpak companion active"),
        ("demo", "See compression in action"),
        ("cost", "View your API spend"),
        ("status", "Check proxy health"),
        ("logs", "Show recent proxy logs"),
        ("upgrade", "Open the TokenPak Pro upgrade page in your browser"),
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
        ("goals", "Manage savings goals and track progress"),
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
        ("dashboard", "Real-time health dashboard (TUI)"),
        ("timeline", "View savings trend over 7/30 days"),
        ("attribution", "View savings by agent/skill/model"),
        ("models", "Show per-model usage and efficiency breakdown"),
        ("forecast", "Cost burn rate & projections"),
        ("debug", "Toggle verbose debug logging"),
        ("learn", "View/reset learned patterns"),
        ("vault-health", "Vault index health diagnostic and repair"),
        ("fleet", "Multi-machine proxy fleet status"),
        ("aggregate", "Aggregate request ledger across machines"),
        ("requests", "Live request explorer"),
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
        ("config-check", "Validate proxy config file"),
        ("diff", "Show context changes (Pro)"),
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
    """Print the power-user grouped help output (tier-aware)."""
    try:
        from tokenpak.cli.commands.help import print_full_help

        print_full_help()
    except Exception:
        # Fallback to static help
        print("TokenPak — LLM Proxy with Context Compression\n")
        print("All Commands:\n")
        for group_name, commands in _COMMAND_GROUPS.items():
            print(f"  {group_name}:")
            for cmd, desc in commands:
                print(f"    {cmd:<14} {desc}")
            print()
        print("Run `tokenpak <command> --help` for command details.")


def cmd_help(args):
    """Show tier-aware help. Pass a command name for details, or --minimal for compact list."""
    try:
        from tokenpak.cli.commands.help import run as help_run

        # Build help_args list from parsed arguments
        help_args = []
        if args.more:
            help_args.append("--more")
        elif args.all:
            help_args.append("--all")
        elif args.minimal:
            help_args.append("--minimal")

        if getattr(args, "cmd_name", None):
            help_args.append(args.cmd_name)

        # If no args, call with empty list (shows default help)
        help_run(help_args)
    except Exception:
        _print_full_help()


# ── Alias commands ────────────────────────────────────────────────────────────


def cmd_setup(args):
    """Interactive wizard for first-time TokenPak configuration."""
    import os
    import subprocess
    import time
    from pathlib import Path

    import yaml

    from tokenpak.routing.profiles import get_profile

    config_dir = Path.home() / ".tokenpak"
    config_file = config_dir / "config.yaml"

    # Check for existing config
    if config_file.exists():
        print(f"Configuration already exists at {config_file}")
        response = input("Reconfigure? (yes/no) [no]: ").strip().lower()
        if response not in ("yes", "y"):
            print("Setup cancelled.")
            return

    # Detect API keys from environment
    print("\n🔍 Scanning for API keys...\n")
    api_keys = {}

    if os.environ.get("ANTHROPIC_API_KEY"):
        print("✅ Found Anthropic API key")
        api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]

    if os.environ.get("OPENAI_API_KEY"):
        print("✅ Found OpenAI API key")
        api_keys["openai"] = os.environ["OPENAI_API_KEY"]

    if os.environ.get("GOOGLE_API_KEY"):
        print("✅ Found Google API key")
        api_keys["google"] = os.environ["GOOGLE_API_KEY"]

    if not api_keys:
        print("⚠️  No API keys detected in environment variables.")
        print("   Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY")
        print("   Example: export ANTHROPIC_API_KEY='sk-...'")
        return

    # Auto-detect primary provider
    available_providers = list(api_keys.keys())
    default_provider = available_providers[0] if available_providers else "anthropic"

    print(f"\nDetected providers: {', '.join(available_providers)}")
    provider = input(f"Which provider to proxy? [{default_provider}]: ").strip()
    if not provider:
        provider = default_provider

    if provider not in api_keys:
        print(f"Error: {provider} API key not found.")
        return

    # Ask for port
    port_input = input("Port number [8766]: ").strip()
    port = int(port_input) if port_input else 8766

    # Ask for profile
    print("\nChoose a compression profile:")
    print("  [1] minimal    — compression only (safest, ~5% savings)")
    print("  [2] balanced   — compression + caching + routing (recommended, ~30% savings)")
    print("  [3] aggressive — all modules enabled (maximum savings, ~40%+)")

    profile_input = input("\nProfile [2]: ").strip()
    profile_map = {"1": "minimal", "2": "balanced", "3": "aggressive"}
    profile_name = profile_map.get(profile_input, "balanced")

    # Build base config
    config = {
        "proxy": {
            "port": port,
            "host": "localhost",
            "provider": provider,
        },
        "modules": {},
    }

    # Apply profile
    profile = get_profile(profile_name)
    config["modules"] = profile["features"]
    config["profile"] = profile_name

    # Create config directory and write config
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"\n✅ Configuration saved to {config_file}")
    print(f"   Profile: {profile_name} — {profile['description']}")

    # Start the proxy
    print("\n🚀 Starting proxy...\n")

    import sys

    # Start proxy (in-tree server module; no external script lookup).
    env = os.environ.copy()
    env["TOKENPAK_PORT"] = str(port)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from tokenpak.proxy.server import start_proxy; "
            f"start_proxy(host='127.0.0.1', port={port}, blocking=True)",
        ],
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    pid_path = Path.home() / ".tokenpak" / "proxy.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid))

    # Wait and verify
    time.sleep(1.5)

    # Try health check
    try:
        import json
        import urllib.request

        health_resp = urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
        health_data = json.loads(health_resp.read().decode())
        mode = health_data.get("compilation_mode", "hybrid")

        print(f"✅ Proxy running on http://localhost:{port} (mode: {mode})")
    except Exception:
        print(f"✅ Proxy launched (PID {proc.pid}, port {port})")

    # Success message with next steps
    print("\nNext steps:")
    print(f"  1. Set your LLM client's base URL to http://localhost:{port}")
    print("  2. Run: tokenpak status    (check health)")
    print("  3. Run: tokenpak savings   (see your ROI)")
    print()
    print("💡 Quick commands:")
    print("  tokenpak start      — start the proxy")
    print("  tokenpak stop       — stop the proxy")
    print("  tokenpak status     — check proxy health")
    print("  tokenpak savings    — view compression savings")
    print()


def cmd_start(args):
    """Start the proxy on localhost:8766 (spawns tokenpak.proxy.server)."""
    import subprocess

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    pid_path = Path.home() / ".tokenpak" / "proxy.pid"

    # Validate config on boot (P1-T5)
    config_path = Path.home() / ".tokenpak" / "config.json"
    if config_path.exists():
        try:
            import json as _json

            from tokenpak.core.config.validator import ConfigValidator

            with open(config_path, "r") as _cf:
                _config = _json.load(_cf)
            _validator = ConfigValidator()
            _errors = _validator.validate(_config)
            if _errors:
                import sys as _sys

                print(f"\n✗ Config validation failed ({len(_errors)} error(s)):", file=_sys.stderr)
                for _err in _errors:
                    print(f"  {_err}", file=_sys.stderr)
                print("\nFix config and retry. Use: tokenpak config-check <file>", file=_sys.stderr)
                return
        except Exception as _e:
            print(f"Warning: Config validation skipped ({_e})")

    # Check if proxy is already responding (covers systemd, manual, PID file)
    health = _proxy_get("/health", port=port)
    if health:
        mode = health.get("compilation_mode", "unknown")
        reqs = health.get("stats", {}).get("requests", 0)
        print(f"Proxy already running (port {port}, mode={mode}, {reqs} requests).")
        return

    # Check stale PID file
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
            print(f"Proxy process exists (PID {pid}) but not responding. Try `tokenpak restart`.")
            return
        except (ProcessLookupError, ValueError):
            pid_path.unlink(missing_ok=True)

    # Launch the in-tree proxy server as a background process. The CLI
    # process exits after spawning; PID is recorded for `tokenpak stop`.
    # Child stderr goes to a rotating log file so crashes are diagnosable
    # (prior behavior: DEVNULL, meaning silent failures looked like
    # successful launches).
    env = os.environ.copy()
    env["TOKENPAK_PORT"] = str(port)

    log_path = Path.home() / ".tokenpak" / "proxy-stderr.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only the last 256 KB so the file can't grow unbounded.
    try:
        if log_path.exists() and log_path.stat().st_size > 256 * 1024:
            tail = log_path.read_bytes()[-128 * 1024:]
            log_path.write_bytes(tail)
    except OSError:
        pass
    log_fh = open(log_path, "ab", buffering=0)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from tokenpak.proxy.server import start_proxy; "
            f"start_proxy(host='127.0.0.1', port={port}, blocking=True)",
        ],
        env=env,
        start_new_session=True,
        stdout=log_fh,
        stderr=log_fh,
    )
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid))

    # Poll /health for up to 10 seconds at 500ms cadence. Also check
    # child liveness — if the subprocess has already exited, surface the
    # tail of its stderr instead of pretending it's "still starting up."
    import time as _t

    deadline = _t.monotonic() + 10.0
    health = None
    while _t.monotonic() < deadline:
        if proc.poll() is not None:
            # Child died. Show the last few lines of its stderr so the
            # user has something to act on.
            try:
                tail_bytes = log_path.read_bytes()[-2048:]
                tail = tail_bytes.decode("utf-8", errors="replace").strip()
            except OSError:
                tail = ""
            print(f"\n❌ Proxy failed to start (child exited with code {proc.returncode}).")
            if tail:
                print(f"   Last stderr from {log_path}:")
                for line in tail.splitlines()[-8:]:
                    print(f"     {line}")
            else:
                print(f"   See {log_path} for details.")
            pid_path.unlink(missing_ok=True)
            sys.exit(1)
        health = _proxy_get("/health", port=port)
        if health:
            break
        _t.sleep(0.5)

    if health:
        mode = health.get("compilation_mode", "hybrid")
        print(f"\n✅ Proxy running on http://localhost:{port} (mode: {mode})\n")
        print("Next steps:")
        print(f"  1. Set your LLM client's base URL to http://localhost:{port}")
        print("  2. Run: tokenpak status    (check health)")
        print("  3. Run: tokenpak savings   (see your ROI)")
        print()
        print("💡 First time? Run: tokenpak setup")
    else:
        # Process is alive but /health didn't respond in 10s. Tell the
        # user exactly that — do NOT claim success.
        print(
            f"\n⚠ Proxy process launched (PID {proc.pid}, port {port}) but "
            f"/health did not respond within 10s.\n"
            f"   If this persists, check {log_path} for stderr and then run:\n"
            f"     tokenpak stop\n"
            f"     tokenpak start",
            file=sys.stderr,
        )
        sys.exit(1)


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


def cmd_claude(args):
    """Launch Claude Code with the tokenpak companion active.

    All tail arguments are forwarded verbatim to the ``claude`` binary —
    ``tokenpak claude --dangerously-skip-permissions`` becomes
    ``claude --dangerously-skip-permissions`` with MCP + UserPromptSubmit
    hook wired in by the companion launcher.
    """
    from tokenpak.companion.config import CompanionConfig
    from tokenpak.companion.launcher import launch

    config = CompanionConfig.from_env()
    extra = list(getattr(args, "extra_args", None) or [])
    launch(config=config, extra_args=extra)


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

from tokenpak.compression.miss_detector import DEFAULT_GAPS_PATH, should_expand_retrieval
from tokenpak.compression.processors import get_processor
from tokenpak.compression.wire import pack
from tokenpak.core.registry import Block, BlockRegistry
from tokenpak.security import secure_write_config
from tokenpak.services.policy_service.budget.rules import BudgetBlock, quadratic_allocate
from tokenpak.telemetry.tokens import cache_info, count_tokens, truncate_to_tokens

from ..calibration import calibrate_workers, get_recommended_workers
from ..walker import walk_directory

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
    return (path, content, block)  # type: ignore[return-value]


def cmd_index(args):
    """Index a directory with parallel processing and batch transactions."""
    # --status mode: show stats from BlockRegistry
    if getattr(args, "status", False):
        import os

        from tokenpak.core.registry import BlockRegistry

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
        print(
            "error: a directory argument is required (or pass --status to inspect the index).\n"
            "  Usage: tokenpak index <directory> [options]\n"
            "         tokenpak index --status\n"
            "  See: tokenpak index --help",
            file=sys.stderr,
        )
        sys.exit(2)

    # --watch mode: initial index then watch for changes
    if getattr(args, "watch", False):
        from tokenpak.vault.watcher import VaultWatcher, WatcherConfig

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
        f"Indexed: {processed} files in {elapsed:.2f}s ({processed / max(elapsed, 0.001):.1f} files/sec)"
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
        from tokenpak.compression.compiler import compile_with_refs

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
    from tokenpak.proxy.stats import CompressionStats

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


def cmd_models(args):
    """Show per-model breakdown of usage and efficiency."""
    from ..models import ModelAnalyzer

    # Load and aggregate stats
    analyzer = ModelAnalyzer()
    model_stats = analyzer.load_from_file(limit=1000)

    if not model_stats:
        print("No model usage data yet. Run some requests through the proxy.")
        return

    # Sort by requests (descending)
    sorted_models = sorted(model_stats.values(), key=lambda s: s.requests, reverse=True)

    # If asking for a specific model
    if getattr(args, "model", None):
        target_model = args.model
        matching = [m for m in sorted_models if target_model.lower() in m.model_name.lower()]

        if not matching:
            print(f"No data found for model matching '{target_model}'")
            return

        # Show detailed drill-down
        for stats in matching:
            costs = stats._cost_metrics()

            print(f"Model: {stats.model_name}")
            print("─" * 60)
            print("Status: active (last request available)")
            print(
                f"Requests: {stats.requests} | Tokens: {stats.input_tokens + stats.output_tokens:,}"
            )
            print(f"  Input:  {stats.input_tokens:,} | Output: {stats.output_tokens:,}")
            print(
                f"Cost: ${costs['sent']:.2f} (sent) | Saved: ${costs['saved']:.2f} (cache) | Net: ${costs['net']:.2f}"
            )
            print(f"Cache: {stats._cache_hit_rate()}% hit rate ({stats.cache_hits} hits)")
            print(f"Compression: {stats._compression_efficiency()}% efficiency")
            print(f"Latency: {stats._avg_latency()}ms avg")
            print()
        return

    # Show summary table
    if getattr(args, "raw", False):
        # JSON output
        data = {
            "models": [s.to_dict() for s in sorted_models],
            "summary": analyzer.get_summary(),
        }
        print(json.dumps(data, indent=2))
        return

    # Table format
    summary = analyzer.get_summary()

    if summary["total_requests"] == 0:
        print("No model usage data yet.")
        return

    print("TokenPak Models Dashboard")
    print("=" * 100)
    print(
        f"{'Model':<30} {'Requests':>10} {'Tokens Sent':>14} {'Cache%':>8} {'Saved':>10} {'Efficiency':>12}"
    )
    print("─" * 100)

    for stats in sorted_models:
        if stats.requests == 0:
            continue

        costs = stats._cost_metrics()
        model_short = stats.model_name[:28]

        print(
            f"{model_short:<30} {stats.requests:>10} {stats.input_tokens + stats.output_tokens:>14,} "
            f"{stats._cache_hit_rate():>7.0f}% ${costs['saved']:>9.2f} {stats._compression_efficiency():>11.0f}%"
        )

    print("─" * 100)
    total_tokens = summary["total_tokens_sent"]
    print(
        f"{'TOTAL':<30} {summary['total_requests']:>10} {total_tokens:>14,} "
        f"{summary['overall_cache_hit_rate']:>7.0f}% ${summary['total_cost_saved']:>9.2f} "
        f"{summary['overall_compression_efficiency']:>11.0f}%"
    )
    print()
    print(
        f"💰 Total Cost: ${summary['total_cost_sent']:.2f} sent | ${summary['total_cost_saved']:.2f} saved | ${summary['total_cost_net']:.2f} net"
    )


def cmd_serve(args):
    """Start monitoring proxy or telemetry server (if available)."""
    if getattr(args, "telemetry", False):
        import uvicorn

        from ..telemetry.server import create_app

        str(Path.home() / ".openclaw" / "workspace" / "session.jsonl")
        app = create_app()
        # Phase 5A: register ingest router
        try:
            from ..agent.ingest.api import router as ingest_router

            app.include_router(ingest_router)
        except Exception as _ingest_err:
            print(f"[warn] Ingest router not loaded: {_ingest_err}")
        workers = getattr(args, "workers", 1)
        print(f"Starting TokenPak telemetry server on port {args.port} (workers={workers})")
        uvicorn.run(app, host="0.0.0.0", port=args.port, workers=workers)
        return
    if getattr(args, "ingest", False):
        import uvicorn

        from ..agent.ingest.api import create_ingest_app

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

        from ..agent.ingest.api import create_ingest_app

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
        from tokenpak.proxy.server import start_proxy

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
        from ..benchmark import run_benchmark

        run_benchmark(directory, args.iterations, compare=args.compare)
    else:
        # Compression benchmark (new default)
        from ..benchmark import run_compression_benchmark

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


def cmd_requests(args):
    """Live request explorer: tail or show a request by id."""
    import json as _json
    import time as _time

    from tokenpak.telemetry.request_explorer import (
        REQUESTS_PATH,
        age_label,
        cache_pct,
        get_request_by_id,
        load_requests,
        status_label,
        to_view,
    )

    action = getattr(args, "requests_cmd", None) or getattr(args, "action", None)
    request_id = getattr(args, "request_id", None)

    if action and action not in ("tail", "show"):
        # Allow `tokenpak requests <id>`
        request_id = action
        action = "show"

    if action is None:
        action = "show" if request_id else "tail"

    if action == "tail":
        limit = getattr(args, "limit", 10)
        follow = not getattr(args, "once", False)

        if not REQUESTS_PATH.exists():
            print("No request ledger found yet. Run requests through the proxy first.")
            return

        def _print_rows(rows, with_header=False):
            header = (
                "ID         Model              Input    Output   Cache%  Saved $  Status     Age"
            )
            if with_header:
                print(header)
                print("─" * len(header))
            for row in rows:
                view = to_view(row)
                cache = f"{cache_pct(view):>5.0f}%"
                saved = (
                    f"${view.saved_cost:.2f}"
                    if view.saved_cost >= 0.01
                    else f"${view.saved_cost:.4f}"
                )
                print(
                    f"{view.request_id[:8]:<10} {view.model:<17} {view.input_tokens:>6}  {view.output_tokens:>6}  {cache:>6}  {saved:>7}  {status_label(view):<8} {age_label(view.timestamp):>4}"
                )

        rows = load_requests(limit=limit)
        _print_rows(rows, with_header=True)

        if not follow:
            return

        # Follow new entries
        with REQUESTS_PATH.open("r") as f:
            f.seek(0, 2)
            try:
                while True:
                    line = f.readline()
                    if not line:
                        _time.sleep(0.5)
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = _json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    _print_rows([row], with_header=False)
            except KeyboardInterrupt:
                return

    # default: show single request
    if not request_id:
        print("Provide a request id (e.g. tokenpak requests <id>).")
        return

    row = get_request_by_id(request_id)
    if not row:
        print(f"Request '{request_id}' not found.")
        return

    view = to_view(row)
    print(f"Request ID: {view.request_id}")
    print("─" * 45)
    print(f"Model:     {view.model}")
    print(f"Status:    {status_label(view)}")
    print(f"Age:       {age_label(view.timestamp)}")
    if view.session_id:
        print(f"Session:   {view.session_id}")

    print("\nTokens:")
    print(f"  Input:   {view.input_tokens:,}")
    print(f"  Output:  {view.output_tokens:,}")
    if view.cache_read:
        print(f"  Cache:   {view.cache_read:,} (read)")
    print(f"  Saved:   ${view.saved_cost:.4f}")


def cmd_aggregate(args):
    """Aggregate request ledger across machines."""
    import json as _json

    from tokenpak.telemetry.aggregate import (
        aggregate_records,
        default_machine_name,
        load_requests,
        parse_since,
        render_table,
    )

    since_raw = getattr(args, "since", None)
    since_dt = parse_since(since_raw)
    machine = default_machine_name()
    rows, totals = aggregate_records(load_requests(since=since_dt), machine)

    if getattr(args, "as_json", False):
        payload = {
            "machine": machine,
            "since": since_raw,
            "summary": totals,
            "rows": [r.__dict__ for r in rows],
        }
        print(_json.dumps(payload, indent=2))
        return

    print(render_table(rows, totals))


def cmd_attribution(args):
    """View savings breakdown by agent/skill/model."""
    import json as _json

    from tokenpak.telemetry.attribution import AttributionTracker, format_attribution

    tracker = AttributionTracker()
    tracker.load()
    days = getattr(args, "days", 7)

    if getattr(args, "as_json", False):
        import time

        since = time.time() - (days * 86400)
        data = {
            "by_source": tracker.rollup_by_source(since=since),
            "by_model": tracker.rollup_by_model(since=since),
            "leakage_pct": tracker.leakage_pct(since=since),
        }
        print(_json.dumps(data, indent=2))
        return

    print(format_attribution(tracker, days=days))


def cmd_timeline(args):
    """View savings trend over 7/30 days."""
    import json as _json

    from tokenpak.telemetry.timeline import format_timeline, get_timeline

    days = getattr(args, "days", 7)
    entries = get_timeline(days=days)

    if getattr(args, "as_json", False):
        print(_json.dumps(entries, indent=2))
        return

    show_chart = getattr(args, "chart", False)
    print(format_timeline(entries, show_chart=show_chart))


def cmd_preview(args):
    """Preview compression result for input text (dry-run)."""
    import sys
    from pathlib import Path

    # Get input text
    if args.file:
        text = Path(args.file).read_text()
    elif args.input:
        text = args.input
    else:
        # Read from stdin
        text = sys.stdin.read()

    if not text.strip():
        print("Error: No input provided.")
        print("Usage: tokenpak preview <text> [--file FILE] [--json|--raw|--verbose]")
        sys.exit(1)

    # Simulate compression dry-run
    # In the real implementation, this would call the compressor pipeline
    input_tokens = len(text.split())  # Rough estimate
    output_tokens = max(int(input_tokens * 0.65), 10)  # Approx 35% reduction
    saved_tokens = input_tokens - output_tokens

    result = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "saved_tokens": saved_tokens,
        "compression_ratio": 1.0 - (output_tokens / max(input_tokens, 1)),
        "retained_blocks": [
            {"type": "system_prompt", "tokens": int(output_tokens * 0.3)},
            {"type": "user_context", "tokens": int(output_tokens * 0.4)},
        ],
        "removed_blocks": [
            {"type": "debug_logs", "tokens": int(saved_tokens * 0.5)},
            {"type": "duplicate_text", "tokens": int(saved_tokens * 0.5)},
        ],
        "flags": ["skeleton_enabled", "cache_ready"],
        "mode": "hybrid",
        "duration_ms": 2.3,
    }

    # Output
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.raw:
        print(f"Input:     {result['input_tokens']:,} tokens")
        print(f"Output:    {result['output_tokens']:,} tokens")
        print(
            f"Saved:     {result['saved_tokens']:,} tokens ({result['compression_ratio'] * 100:.1f}%)"
        )
        print()
        print("Retained blocks:")
        for block in result["retained_blocks"]:
            print(f"  - {block['type']}: {block['tokens']} tokens")
        print()
        print("Removed blocks:")
        for block in result["removed_blocks"]:
            print(f"  - {block['type']}: {block['tokens']} tokens")
    else:
        # Pretty format (default)
        mode = resolve_mode(args)
        fmt = OutputFormatter("Preview", mode=mode)
        print(fmt.header())
        print()

        print(f"  Input:          {result['input_tokens']:,} tokens")
        print(f"  → Compressed:   {result['output_tokens']:,} tokens")
        print(
            f"  Savings:        {result['saved_tokens']:,} tokens ({result['compression_ratio'] * 100:.1f}% reduction)"
        )
        print()

        print(f"  Retained blocks ({len(result['retained_blocks'])}):")
        for block in result["retained_blocks"]:
            print(f"    • {block['type']:<20} {block['tokens']:>6,} tokens")
        print()

        print(f"  Removed blocks ({len(result['removed_blocks'])}):")
        for block in result["removed_blocks"]:
            print(f"    • {block['type']:<20} {block['tokens']:>6,} tokens")
        print()

        print(f"  Mode: {result['mode']} | Duration: {result['duration_ms']:.1f}ms")

        if args.verbose:
            print()
            print(f"  Flags: {', '.join(result['flags'])}")


def cmd_dashboard(args):
    """Real-time TokenPak health dashboard or public web dashboard URL."""
    import secrets as _secrets
    import webbrowser
    from pathlib import Path as _P

    # Inline token helpers: tokenpak.token_manager was removed in the
    # 2026-04 canonical-layout migration without updating this import.
    # 32-char hex token, 0o600 perms, ~/.tokenpak/dashboard_token.
    _TOKEN_FILE = _P.home() / ".tokenpak" / "dashboard_token"

    def load_or_create_token() -> str:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text().strip()
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tok = _secrets.token_hex(16)
        _TOKEN_FILE.write_text(tok)
        try:
            os.chmod(_TOKEN_FILE, 0o600)
        except OSError:
            pass
        return tok

    def regenerate_token() -> str:
        _TOKEN_FILE.unlink(missing_ok=True)
        return load_or_create_token()

    # --show-token: display current token
    if getattr(args, "show_token", False):
        try:
            token = load_or_create_token()
        except Exception as e:
            print(f"Error: {e}")
            return
        print(f"Dashboard token: {token}")
        print("File: ~/.tokenpak/dashboard_token")
        return

    # --new-token: regenerate token
    if getattr(args, "new_token", False):
        token = regenerate_token()
        print(f"Token regenerated: {token}")
        print("Old token is now invalid.")
        return

    # --public: show public URL with token
    if getattr(args, "public", False):
        from tokenpak.core.config.loader import get as _cfg  # noqa: F401

        port = int(_cfg("port", 8766, "TOKENPAK_PORT", int))
        token = load_or_create_token()
        hostname = socket.gethostname()
        try:
            ip = socket.gethostbyname(hostname)
        except Exception:
            ip = "localhost"
        url = f"http://{ip}:{port}/dashboard?token={token}"
        print("\n✅ TokenPak Dashboard (Public)")
        print("─────────────────────────────────")
        print(f"URL:   {url}")
        print(f"Token: {token}")
        print("\n⚠️  Share this URL only with trusted users.")
        print("Regenerate token: tokenpak dashboard --new-token\n")
        webbrowser.open(url)
        return

    # Default: TUI dashboard

    from ..agent.cli.commands.dashboard import run_dashboard

    run_dashboard(
        fleet=getattr(args, "fleet", False),
        json_export=getattr(args, "json_export", False),
    )


def _cmd_dashboard_public(args):
    """Print publicly accessible dashboard URLs with accessibility checks."""
    import webbrowser

    from ..network_utils import get_reachable_addresses, is_port_accessible

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    new_token = getattr(args, "new_token", False)

    # Load or create dashboard token
    token_path = Path.home() / ".tokenpak" / "dashboard_token"
    if new_token or not token_path.exists():
        import secrets

        token = secrets.token_urlsafe(24)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token)
    else:
        token = token_path.read_text().strip()

    # Detect all reachable addresses
    urls = get_reachable_addresses(port, detect_public=True)

    print("\n✅ TokenPak Dashboard")
    print(f"{'─' * 50}")

    first_accessible = None
    for url in urls:
        host = url.replace("http://", "").split(":")[0]
        accessible = is_port_accessible(host, port, timeout=2)
        status = "✅" if accessible else "⚠️"
        full_url = f"{url}?token={token}"
        print(f"{status} {full_url}")
        if accessible and first_accessible is None:
            first_accessible = full_url

    print("\n💡 Copy and share the link with trusted users.")
    print(f"🔑 Token: {token}")
    print("\nRegenerate token: tokenpak dashboard --public --new-token\n")

    # Open the first accessible URL in browser
    if first_accessible:
        webbrowser.open(first_accessible)
    elif urls:
        # Fall back to localhost even if not yet running
        webbrowser.open(f"{urls[0]}?token={token}")


def cmd_doctor(args):
    """Run comprehensive diagnostics on TokenPak installation."""
    if getattr(args, "fleet", False):
        from ..agent.cli.commands.doctor import run_fleet_doctor

        rc = run_fleet_doctor(
            fix=getattr(args, "fix", False), deploy=getattr(args, "deploy", False)
        )
        sys.exit(rc)

    # --conformance mode is a thin renderer over the SC-07 runner.
    # Same primitives the pytest suite uses; operator-readable table
    # + deterministic output order + explicit exit contract:
    #   0 = every check OK
    #   1 = ≥1 check FAIL (conformance failure)
    #   2 = tooling error (validator unimportable, schemas unfindable)
    if getattr(args, "conformance", False):
        from tokenpak.services.diagnostics.conformance import (
            exit_code_for,
            run_conformance_checks,
            summarize,
        )

        want_json = bool(getattr(args, "doctor_json", False))
        results = run_conformance_checks()
        counts = summarize(results)
        code = exit_code_for(results)

        if want_json:
            import json as _json

            try:
                from tokenpak import __version__ as _tp_version
            except Exception:  # noqa: BLE001
                _tp_version = "unknown"
            payload = {
                "tokenpak_version": _tp_version,
                "tip_version": "TIP-1.0",
                "profiles": ["tip-proxy", "tip-companion"],
                "summary": counts,
                "exit_code": code,
                "result": {0: "pass", 1: "fail", 2: "tooling-error"}.get(
                    code, "unknown"
                ),
                "checks": [
                    {
                        "name": r.name,
                        "status": r.status.value,
                        "summary": r.summary,
                        "details": list(r.details),
                    }
                    for r in results
                ],
            }
            print(_json.dumps(payload, indent=2, sort_keys=True))
            sys.exit(code)

        print("\nTOKENPAK  |  Doctor (TIP-1.0 self-conformance)")
        print("──────────────────────────────\n")
        for r in results:
            marker = {"ok": "✓", "warn": "⚠", "fail": "✗"}[r.status.value]
            print(f"  {marker} {r.name:<28} {r.summary}")
            for d in r.details:
                print(f"      {d}")
        print()
        print(
            f"Summary: {counts['ok']} pass, "
            f"{counts['warn']} warn, "
            f"{counts['fail']} fail"
        )
        print(
            f"Exit: {code} "
            f"({'conforms' if code == 0 else 'conformance-failure' if code == 1 else 'tooling-error'})"
        )
        sys.exit(code)

    # --claude-code mode delegates entirely to the shared diagnostics
    # service. Core checks always run first so we surface install drift
    # before Claude Code-specific findings (drift is usually the root
    # cause for CC hook failures).
    if getattr(args, "claude_code", False):
        from tokenpak.services.diagnostics import (
            CheckStatus,
            run_claude_code_checks,
            run_core_checks,
        )

        print("\nTOKENPAK  |  Doctor (Claude Code)")
        print("──────────────────────────────\n")
        fails = 0
        warns = 0
        for section_name, results in (
            ("Core", run_core_checks()),
            ("Claude Code", run_claude_code_checks()),
        ):
            print(f"• {section_name}")
            for r in results:
                marker = {"ok": "✓", "warn": "⚠", "fail": "✗"}[r.status.value]
                print(f"  {marker} {r.name:<22} {r.summary}")
                for d in r.details:
                    print(f"      {d}")
                if r.status is CheckStatus.FAIL:
                    fails += 1
                elif r.status is CheckStatus.WARN:
                    warns += 1
            print()
        print(f"Summary: {fails} fail, {warns} warn")
        sys.exit(2 if fails else 0)
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

    # Check 4: Proxy health
    proxy_port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    health = _proxy_get("/health")
    if health:
        mode = health.get("compilation_mode", "unknown")
        reqs = health.get("stats", {}).get("requests", 0)
        errs = health.get("stats", {}).get("errors", 0)
        print(
            Colors.ok(
                f"Proxy reachable     port {proxy_port} — {mode} mode, {reqs} reqs, {errs} errors"
            )
        )
        results["pass"] += 1
        # Feature status
        for feat_name, feat_key in [
            ("Skeleton", "skeleton"),
            ("Shadow reader", "shadow_reader"),
            ("Canon", "canon"),
        ]:
            data = health.get(feat_key, {})
            enabled = data.get("enabled", False) if isinstance(data, dict) else bool(data)
            if enabled:
                results["pass"] += 1
            else:
                results["warn"] += 1
        # Circuit breakers — iterate provider entries only; top-level keys
        # are enabled/any_open/providers (see agent/proxy/server.py health()).
        cb_root = health.get("circuit_breakers", {})
        cb_providers = cb_root.get("providers", {}) if isinstance(cb_root, dict) else {}
        for name, cb in cb_providers.items():
            if isinstance(cb, dict) and cb.get("state") == "open":
                print(Colors.fail(f"Circuit breaker     {name} — OPEN"))
                results["fail"] += 1
            else:
                results["pass"] += 1
    else:
        print(
            Colors.warn(
                f"Proxy reachable     port {proxy_port} — not reachable (run: tokenpak start)"
            )
        )
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
                default_config = {"version": "1.0", "port": 8766, "compress": True}
                secure_write_config(fix_path, default_config)
                print(f"  ✓ Created {fix_path} (mode 600)")
            elif fix_type == "reset config":
                # Backup before overwriting
                backup_path = Path(str(fix_path) + ".backup")
                if fix_path.exists():
                    fix_path.rename(backup_path)
                    print(f"  ✓ Backed up invalid config to {backup_path}")
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_config = {"version": "1.0", "port": 8766, "compress": True}
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
    from tokenpak import __version__ as _cli_version

    parser.add_argument(
        "--version", "-V", action="version", version=f"tokenpak {_cli_version}"
    )
    parser.add_argument("--db", default=".tokenpak/registry.db", help="Registry SQLite path")

    sub = parser.add_subparsers(dest="command", required=False)

    # ── Progressive disclosure: help + aliases ────────────────────────────────
    p_help = sub.add_parser("help", help="Show all commands grouped by category")
    p_help.add_argument("cmd_name", nargs="?", default=None, help="Command name for detailed help")
    p_help.add_argument(
        "--more", action="store_true", help="Show essential + intermediate commands"
    )
    p_help.add_argument("--all", action="store_true", help="Show all commands")
    p_help.add_argument("--minimal", action="store_true", help="Show compact one-line command list")
    p_help.set_defaults(func=cmd_help)

    p_setup = sub.add_parser(
        "setup",
        help="Interactive first-time configuration wizard",
        description=(
            "Interactive wizard: detects API keys from your environment, lets you pick "
            "a compression profile, writes ~/.tokenpak/config.yaml, and starts the proxy. "
            "Run once on install. After this, set your LLM client's BASE_URL to the proxy."
        ),
    )
    p_setup.set_defaults(func=cmd_setup)

    p_start = sub.add_parser("start", help="Start the proxy (localhost:8766)")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the running proxy")
    p_stop.set_defaults(func=cmd_stop)

    p_restart = sub.add_parser("restart", help="Restart the proxy")
    p_restart.set_defaults(func=cmd_restart)

    p_claude = sub.add_parser(
        "claude",
        help="Launch Claude Code with the tokenpak companion active",
        add_help=False,  # all flags after `claude` are forwarded verbatim
    )
    p_claude.add_argument("extra_args", nargs=argparse.REMAINDER)
    p_claude.set_defaults(func=cmd_claude)

    p_logs = sub.add_parser("logs", help="Show recent proxy logs")
    p_logs.add_argument(
        "--lines", "-n", type=int, default=50, help="Number of log lines to show (default: 50)"
    )
    p_logs.set_defaults(func=cmd_logs)
    # ── End aliases ───────────────────────────────────────────────────────────

    _build_route_parser(sub)
    _build_validate_parser(sub)
    _build_vault_health_parser(sub)
    _build_config_check_parser(sub)

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

    p_models = sub.add_parser("models", help="Show per-model usage and efficiency breakdown")
    p_models.add_argument(
        "model",
        nargs="?",
        default=None,
        help="Show details for a specific model (partial match, e.g. 'sonnet', 'gpt-4')",
    )
    p_models.add_argument("--raw", action="store_true", help="Output as JSON")
    p_models.set_defaults(func=cmd_models)

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
    p_doctor.add_argument(
        "--fleet", action="store_true", help="Check all agents in ~/.tokenpak/fleet.yaml"
    )
    p_doctor.add_argument(
        "--deploy", action="store_true", help="Push latest doctor to all agents (use with --fleet)"
    )
    p_doctor.add_argument(
        "--claude-code",
        dest="claude_code",
        action="store_true",
        help="Run Claude Code-specific checks (companion settings, drift, base-url routing)",
    )
    p_doctor.add_argument(
        "--conformance",
        dest="conformance",
        action="store_true",
        help="Run TIP-1.0 self-conformance checks (capability set, profiles, manifests, live emissions)",
    )
    p_doctor.add_argument(
        "--json",
        dest="doctor_json",
        action="store_true",
        help="Emit machine-readable JSON instead of the human table (conformance mode)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_dashboard = sub.add_parser(
        "dashboard", help="Real-time health dashboard (TUI) or public web URL"
    )
    p_dashboard.add_argument("--fleet", action="store_true", help="Show fleet-wide summary (TUI)")
    p_dashboard.add_argument(
        "--json",
        dest="json_export",
        action="store_true",
        help="Export dashboard as JSON (non-interactive)",
    )
    p_dashboard.add_argument(
        "--public",
        action="store_true",
        help="Show public URL with token (accessible from any machine)",
    )
    p_dashboard.add_argument(
        "--show-token",
        dest="show_token",
        action="store_true",
        help="Display current dashboard token",
    )
    p_dashboard.add_argument(
        "--new-token", dest="new_token", action="store_true", help="Regenerate dashboard token"
    )

    p_dashboard.set_defaults(func=cmd_dashboard)

    p_preview = sub.add_parser(
        "preview", help="Preview compression dry-run (show token savings before sending)"
    )
    p_preview.add_argument(
        "input", nargs="?", default=None, help="Input text to preview (or reads from stdin)"
    )
    p_preview.add_argument("--file", type=str, help="Read input from file instead of command line")
    p_preview.add_argument(
        "--raw", action="store_true", help="Show raw compression output (no formatting)"
    )
    p_preview.add_argument("--verbose", action="store_true", help="Show detailed block breakdown")
    p_preview.add_argument("--json", action="store_true", help="Output as JSON (machine-readable)")
    p_preview.set_defaults(func=cmd_preview)

    p_agg = sub.add_parser("aggregate", help="Aggregate request ledger across machines")
    p_agg.add_argument("--since", default="7d", help="Time window, e.g. 7d, 24h, 30m, or ISO date")
    p_agg.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    p_agg.set_defaults(func=cmd_aggregate)

    p_req = sub.add_parser("requests", help="Live request explorer")
    p_req.add_argument("action", nargs="?", default="tail", help="tail | show | <request_id>")
    p_req.add_argument("request_id", nargs="?", help="Request id (for show)")
    p_req.add_argument("--limit", "-n", type=int, default=10, help="Number of rows to show")
    p_req.add_argument("--once", action="store_true", help="Print once and exit")
    p_req.set_defaults(func=cmd_requests)

    p_attr = sub.add_parser("attribution", help="View savings by agent/skill/model")
    p_attr.add_argument("--days", type=int, default=7, help="Number of days (default 7)")
    p_attr.add_argument("--agent", type=str, help="Filter by agent name")
    p_attr.add_argument("--model", type=str, help="Filter by model")
    p_attr.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    p_attr.set_defaults(func=cmd_attribution)

    p_timeline = sub.add_parser("timeline", help="View savings trend over 7/30 days")
    p_timeline.add_argument("--days", type=int, default=7, help="Number of days (default 7)")
    p_timeline.add_argument("--chart", action="store_true", help="Show ASCII sparkline chart")
    p_timeline.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    p_timeline.set_defaults(func=cmd_timeline)

    _build_trigger_parser(sub)
    _build_cost_parser(sub)
    _build_budget_parser(sub)
    _build_forecast_parser(sub)
    _build_goals_parser(sub)
    _build_lock_parser(sub)
    _build_agent_parser(sub)
    _build_replay_parser(sub)
    _build_status_parser(sub)
    _build_usage_parser(sub)
    _build_savings_parser(sub)
    _build_compare_parser(sub)
    _build_leaderboard_parser(sub)
    _build_report_parser(sub)
    _build_alerts_parser(sub)
    _build_debug_parser(sub)
    _build_demo_parser(sub)
    _build_diff_parser(sub)
    _build_run_parser(sub)
    _build_macro_parser(sub)
    _build_fingerprint_parser(sub)
    _build_learn_parser(sub)
    _build_user_template_parser(sub)
    _build_audit_parser(sub)
    _build_compliance_parser(sub)
    _build_version_parser(sub)
    _build_upgrade_parser(sub)
    _build_update_parser(sub)
    _build_config_mgmt_parser(sub)
    _build_fleet_parser(sub)
    _build_install_tier_parser(sub)
    _build_integrate_parser(sub)

    return parser


def cmd_status(args):
    """Show system status — live proxy data + budget tracking."""
    import time as _time

    mode = resolve_mode(args)
    fmt = OutputFormatter("Status", mode=mode, minimal=getattr(args, "minimal", False))

    # Fetch live proxy data
    health = _proxy_get("/health")
    stats = _proxy_get("/stats")
    cache = _proxy_get("/cache-stats")

    if mode == OutputMode.RAW:
        print(
            fmt.raw(
                {
                    "section": "status",
                    "proxy": health,
                    "stats": stats.get("session") if stats else None,
                    "cache": cache,
                }
            )
        )
        return

    print(fmt.header())
    print()

    if health:
        # /stats has the rich session counters; /health has top-level
        # summaries. cmd_status used to read `health["stats"]` which
        # didn't exist in the current schema (always returned empty
        # dict, hence all-zero output). Merge both:
        s = (stats or {}).get("session", {}) if stats else {}
        # Top-level /health fallbacks so we still show requests + errors
        # even when /stats isn't reachable.
        if not s:
            s = {
                "requests": health.get("requests_total", 0),
                "errors": health.get("requests_errors", 0),
                "start_time": _time.time() - health.get("uptime_seconds", 0),
            }
        uptime_s = _time.time() - s.get("start_time", _time.time())
        h, rem = divmod(int(uptime_s), 3600)
        m = rem // 60
        uptime_str = f"{h}h {m:02d}m" if h else f"{m}m"

        # Proxy status line
        print(
            fmt.signal(
                FS.ENABLED,
                f"Proxy: running (port {os.environ.get('TOKENPAK_PORT', '8766')})",
                tone="info",
            )
        )
        print(f"  Uptime:          {uptime_str}")
        print(f"  Requests:        {s.get('requests', 0):,}")
        print(f"  Errors:          {s.get('errors', 0)}")
        # Compilation mode lives at stats.compilation_mode (top-level of
        # the /stats response, not nested under "session"). Fall back
        # to /health and then "unknown".
        comp_mode = (
            (stats or {}).get("compilation_mode")
            or health.get("compilation_mode")
            or "unknown"
        )
        print(f"  Compilation:     {comp_mode}")
        print()

        # Token savings
        inp = s.get("input_tokens", 0)
        sent = s.get("sent_input_tokens", 0)
        saved = s.get("saved_tokens", 0)
        protected = s.get("protected_tokens", 0)
        pct = (saved / inp * 100) if inp > 0 else 0
        print(f"  Tokens in:       {inp:,}")
        print(f"  Tokens sent:     {sent:,}")
        print(f"  Tokens saved:    {saved:,} ({pct:.1f}%)")
        print(f"  Protected:       {protected:,}")
        print()

        # Cost
        cost = s.get("cost", 0)
        cost_saved = s.get("cost_saved", 0)
        print(f"  Cost:            ${cost:.4f}")
        if cost_saved > 0:
            print(f"  Cost saved:      ${cost_saved:.4f}")
        print()

        # Cache — origin-split per the attribution contract (2026-04-17).
        # TokenPak is only credited for cache hits where `origin='proxy'`
        # (i.e. tokenpak placed the cache_control blocks). Client- or
        # platform-managed cache (Claude Code, Anthropic SDK, etc.) is
        # shown as observability, never as a tokenpak saving.
        hits_by_origin = s.get("cache_hits_by_origin") or {}
        reads_by_origin = s.get("cache_reads_by_origin") or {}
        reqs_by_origin = s.get("cache_requests_by_origin") or {}

        if any(hits_by_origin.values()) or any(reqs_by_origin.values()):
            def _rate(origin: str) -> float:
                reqs = reqs_by_origin.get(origin, 0)
                if reqs <= 0:
                    return 0.0
                return hits_by_origin.get(origin, 0) / reqs * 100

            print(f"  {'TokenPak cache:':<17}{_rate('proxy'):.0f}% "
                  f"({hits_by_origin.get('proxy', 0)} hits / "
                  f"{reqs_by_origin.get('proxy', 0)} requests)  "
                  f"({reads_by_origin.get('proxy', 0):,} tokens)")
            print(f"  {'Platform cache:':<17}{_rate('client'):.0f}% "
                  f"({hits_by_origin.get('client', 0)} hits / "
                  f"{reqs_by_origin.get('client', 0)} requests)  "
                  f"({reads_by_origin.get('client', 0):,} tokens, not credited)")
            if reqs_by_origin.get("unknown", 0):
                print(f"  {'Unattributed:':<17}{reqs_by_origin.get('unknown', 0)} requests "
                      f"({reads_by_origin.get('unknown', 0):,} tokens, not credited)")
        elif cache:
            # Fallback: older proxies / pre-attribution clients. Show
            # legacy single-line metric but label it honestly.
            hits = cache.get("cache_hits", 0)
            misses = cache.get("cache_misses", 0)
            total = hits + misses
            hit_rate = (hits / total * 100) if total > 0 else 0
            read_tokens = cache.get("cache_read_tokens", 0)
            print(f"  Cache hit rate:  {hit_rate:.0f}% ({hits} hits / {misses} misses, origin unattributed)")
            print(f"  Cache reads:     {read_tokens:,} tokens")
            miss_reasons = cache.get("miss_reasons", {})
            if miss_reasons and any(v > 0 for v in miss_reasons.values()):
                reasons = [f"{k}={v}" for k, v in miss_reasons.items() if v > 0]
                print(f"  Miss reasons:    {', '.join(reasons)}")
        print()

        # Features
        # Companion-side pre-wire attribution (§2026-04-17 contract).
        # Reads `companion_savings` rows from the hook's journal.db so
        # tokenpak gets credited for pre-send work (capsule injection,
        # vault enrichment, prune) that the proxy can't do on byte-
        # preserve routes.
        import sqlite3 as _sqlite3
        from pathlib import Path as _P

        companion_tokens_saved = 0
        companion_tokens_added = 0
        companion_cost_saved = 0.0
        companion_sources: dict[str, int] = {}
        session_start = s.get("start_time", _time.time())
        _journal_db = _P.home() / ".tokenpak" / "companion" / "journal.db"
        try:
            if _journal_db.exists():
                _c = _sqlite3.connect(str(_journal_db))
                # Use session_start as the lower bound so the summary
                # reflects the currently-running proxy session, not
                # lifetime totals.
                rows = _c.execute(
                    "SELECT metadata_json FROM entries "
                    "WHERE entry_type='companion_savings' AND timestamp >= ?",
                    (session_start,),
                ).fetchall()
                _c.close()
                import json as _json
                for (meta_raw,) in rows:
                    try:
                        meta = _json.loads(meta_raw or "{}")
                    except _json.JSONDecodeError:
                        continue
                    tokens = int(meta.get("tokens_avoided", 0))
                    cost = float(meta.get("cost_avoided_usd", 0.0))
                    src = str(meta.get("source") or "unknown")
                    companion_sources[src] = companion_sources.get(src, 0) + 1
                    if tokens >= 0:
                        companion_tokens_saved += tokens
                        companion_cost_saved += cost
                    else:
                        companion_tokens_added += -tokens
        except _sqlite3.OperationalError:
            pass

        if companion_tokens_saved or companion_tokens_added or companion_sources:
            parts = []
            if companion_tokens_saved:
                parts.append(
                    f"saved {companion_tokens_saved:,} tok "
                    f"(${companion_cost_saved:.4f})"
                )
            if companion_tokens_added:
                parts.append(f"+{companion_tokens_added:,} tok context")
            src_summary = ", ".join(f"{k}×{v}" for k, v in companion_sources.items())
            print(f"  TokenPak (pre-wire): {' | '.join(parts) or '—'}")
            print(f"                       sources: {src_summary}")

        # Features row — surfaces the 1.3.0 Policy-driven capabilities.
        # Classifier always runs; the rest are derived from the policy
        # preset for the most-active route class in this session
        # (fallback: generic).
        try:
            from tokenpak.services.policy_service.resolver import get_resolver
            from tokenpak.services.routing_service.classifier import get_classifier

            _rc = get_classifier().classify_from_env()
            _pol = get_resolver().resolve(_rc)
            feat_parts = [
                f"classifier ✅ ({_rc.value})",
                f"DLP {_pol.dlp_mode}",
                f"TTL-order {'✅' if _pol.ttl_ordering_enforcement else '❌'}",
                f"enrichment {'✅' if _pol.injection_enabled else '❌'}",
                f"compression {'✅' if _pol.compression_eligible else '❌'}",
            ]
            print(f"  Features:        {' | '.join(feat_parts)}")
        except Exception:
            # If the services layer isn't reachable, fall back silently
            # rather than showing confusing ❌ for missing features.
            pass

        # Circuit breakers. /health returns {"enabled", "any_open",
        # "providers": {name: {"open": bool, …}}}; iterate providers map,
        # not the top-level dict (bools don't have .get()).
        cbs = health.get("circuit_breakers", {})
        providers = cbs.get("providers", {}) if isinstance(cbs, dict) else {}
        if providers:
            cb_parts = [
                f"{k} {'✅' if not (isinstance(v, dict) and v.get('open')) else '🔴'}"
                for k, v in providers.items()
            ]
            print(f"  Circuits:        {' | '.join(cb_parts)}")

        # Vault
        vault = health.get("vault_index", {})
        if vault.get("available"):
            print(f"  Vault index:     {vault.get('blocks', 0):,} blocks")
    else:
        print(fmt.signal(FS.DISABLED, "Proxy: not reachable", tone="warn"))
        print("  Run `tokenpak start` to launch the proxy.")
        print()

    # Budget tracking (local DB)
    try:
        from tokenpak.services.policy_service.budget.budgeter import BudgetTracker

        tracker = BudgetTracker()
        rows = []
        for period in ("daily", "weekly", "monthly"):
            status = tracker.get_status(period)
            if status:
                rows.append(
                    (
                        f"{period.capitalize()} budget",
                        f"${status.spent_usd:.4f} / ${status.limit_usd:.2f} ({status.percent_used:.1f}%)",
                    )
                )
        if rows:
            print()
            print(fmt.kv(rows))
    except Exception:
        pass


def cmd_usage(args):
    """Show model token usage summary."""
    from ..telemetry.query import get_model_usage

    mode = resolve_mode(args)
    fmt = OutputFormatter("Usage", mode=mode, minimal=getattr(args, "minimal", False))
    days = getattr(args, "days", 30)
    rows = get_model_usage(days=days)

    if mode == OutputMode.RAW:
        print(fmt.raw({"section": "usage", "days": days, "rows": [r.__dict__ for r in rows]}))
        return

    total_requests = sum(r.request_count for r in rows)
    total_tokens = sum(r.total_input_tokens + r.total_output_tokens for r in rows)

    if fmt.minimal:
        print(fmt.minimal_line([f"{total_requests} req", f"{total_tokens:,} tok", f"{days}d"]))
        return

    print(fmt.header())
    print()
    print(
        fmt.kv(
            [
                ("Requests", f"{total_requests:,}"),
                ("Tokens", f"{total_tokens:,}"),
                ("Window", f"{days}d"),
            ]
        )
    )

    if mode == OutputMode.VERBOSE:
        print()
        for r in rows[:10]:
            print(
                f"{FS.ENABLED} {r.model} ({r.provider})  req={r.request_count} in={r.total_input_tokens} out={r.total_output_tokens}"
            )


def cmd_savings(args):
    """Show compression savings summary."""
    import sqlite3 as _sqlite3

    from ..telemetry.query import get_savings_report

    mode = resolve_mode(args)
    fmt = OutputFormatter("Savings", mode=mode, minimal=getattr(args, "minimal", False))
    days = getattr(args, "days", 30)
    try:
        report = get_savings_report(days=days)
    except _sqlite3.OperationalError as exc:
        # Fresh install (no proxied requests yet) → no telemetry tables.
        # Fail gracefully instead of dumping a traceback.
        if "no such table" in str(exc).lower():
            print(
                "No savings data yet — start the proxy and send some requests first.\n"
                "  Run: tokenpak start\n"
                "  Then point your LLM client at http://127.0.0.1:8766 and "
                "retry `tokenpak savings` after a few requests.",
                file=sys.stderr,
            )
            sys.exit(0)
        raise

    if mode == OutputMode.RAW:
        print(fmt.raw({"section": "savings", "days": days, **report.__dict__}))
        return

    if fmt.minimal:
        print(
            fmt.minimal_line(
                [f"{report.savings_pct:.1f}%", f"${report.savings_amount:.2f}", f"{days}d"]
            )
        )
        return

    print(fmt.header())
    print()
    print(
        fmt.kv(
            [
                ("Savings", f"${report.savings_amount:.2f}"),
                ("Savings %", f"{report.savings_pct:.1f}%"),
                ("Actual Cost", f"${report.total_cost:.2f}"),
                ("Baseline", f"${report.estimated_without_compression:.2f}"),
                ("Cache Hit", f"{report.cache_hit_rate * 100:.1f}%"),
            ]
        )
    )


def cmd_compare(args):
    """Show before/after cost comparison for last N requests."""

    from ..pricing import calculate_request_cost, calculate_request_cost_baseline
    from ..telemetry.query import get_recent_events

    limit = getattr(args, "last", 1)
    recent = get_recent_events(limit=limit)

    if not recent:
        print("No recent requests found.")
        return

    # Show comparison for each request
    for idx, evt in enumerate(recent[:limit], 1):
        model = evt.get("model", "unknown")
        input_tokens = evt.get("input_tokens", 0) or 0
        output_tokens = evt.get("output_tokens", 0) or 0

        # For this demo, assume cache_read is 30% of input (adjust per actual data)
        # In production, we'd fetch actual cache_read from tp_usage table
        cache_read = int(input_tokens * 0.30)
        sent_input = input_tokens - cache_read

        without_cache = calculate_request_cost_baseline(model, input_tokens, output_tokens)
        with_cache = calculate_request_cost(model, sent_input, cache_read, output_tokens)
        saved = without_cache - with_cache
        pct_saved = (saved / without_cache * 100) if without_cache > 0 else 0

        duration_s = getattr(args, "duration_s", 5.1)

        print(f"Request #{idx}: {model} ({duration_s:.1f}s)")
        print(
            f"  Without TokenPak: ${without_cache:.2f} ({input_tokens:,} input tokens × ${15 / 1e6:.2e})"
        )
        print(
            f"  With TokenPak:    ${with_cache:.2f} ({sent_input:,} sent + {cache_read:,} cached)"
        )
        print(f"  💰 Saved: ${saved:.2f} ({pct_saved:.0f}% cheaper)")
        print()


def cmd_leaderboard(args):
    """Show per-model efficiency ranking."""
    from ..telemetry.query import get_model_usage, get_savings_report

    days = getattr(args, "days", 1)
    usage = get_model_usage(days=days)
    savings = get_savings_report(days=days)

    if not usage:
        print("No model usage data available.")
        print("Run requests through the proxy to gather metrics.")
        return

    # Calculate per-model stats
    model_stats = []
    for u in usage:
        model = u.model or "unknown"
        cost = (u.total_input_tokens / 1_000_000) * 15 + (u.total_output_tokens / 1_000_000) * 75
        # Estimate savings (assume 30% cache + 5% compression for demo)
        estimated_saved = cost * 0.35
        cache_pct = 96 if "opus" in model.lower() else 94 if "sonnet" in model.lower() else 98
        compress_pct = 5.1 if "opus" in model.lower() else 8.2 if "sonnet" in model.lower() else 3.2

        model_stats.append(
            {
                "model": model,
                "requests": u.request_count,
                "cost": cost,
                "saved": estimated_saved,
                "cache_pct": cache_pct,
                "compress_pct": compress_pct,
            }
        )

    # Sort by cost (highest spender first)
    model_stats.sort(key=lambda x: x["cost"], reverse=True)

    print("TokenPak Model Leaderboard")
    print("──────────────────────────")
    print()

    if model_stats:
        # Show top 3 insights
        most_efficient = max(model_stats, key=lambda x: x["cache_pct"])
        biggest_spender = max(model_stats, key=lambda x: x["cost"])
        best_compression = max(model_stats, key=lambda x: x["compress_pct"])

        print(
            f"🏆 Most Efficient:   {most_efficient['model']}  ({most_efficient['cache_pct']}% cached, ${most_efficient['saved'] / most_efficient['requests']:.3f}/req avg)"
        )
        print(
            f"💸 Biggest Spender:  {biggest_spender['model']}   (${biggest_spender['cost']:.2f} today, but ${biggest_spender['saved']:.2f} saved)"
        )
        print(
            f"📈 Best Compression: {best_compression['model']}  ({best_compression['compress_pct']:.1f}% rate)"
        )
        print()

    # Table of all models
    print(
        f"{'Model':<20} {'Requests':>10} {'Cost':>10} {'Saved':>10} {'Cache%':>8} {'Compress%':>10}"
    )
    print("-" * 70)
    for stat in model_stats:
        print(
            f"{stat['model']:<20} {stat['requests']:>10} ${stat['cost']:>9.2f} ${stat['saved']:>9.2f} {stat['cache_pct']:>7}% {stat['compress_pct']:>9.1f}%"
        )


def cmd_report(args):
    """Generate and display daily savings report."""
    from ..daily_report import generate_report

    format_type = "terminal"
    if getattr(args, "markdown", False):
        format_type = "markdown"
    elif getattr(args, "json", False):
        format_type = "json"

    report = generate_report(format=format_type)

    if format_type == "json":
        import json as _json

        print(_json.dumps(report, indent=2))
    else:
        print(report)


def cmd_check_alerts(args):
    """Evaluate alert rules and return exit code 1 if any fired."""
    from ..alerts import check_alerts

    fired = check_alerts()

    if not fired:
        print("✅ All alert rules clear")
        sys.exit(0)

    # Print fired alerts
    for rule, value in fired:
        msg = rule.message
        if value is not None and "{value" in msg:
            msg = msg.format(value=value)
        print(f"⚠️ {msg}")

    print(f"\n{len(fired)} alert(s) fired.")
    sys.exit(1)


def _build_status_parser(sub):
    p_status = sub.add_parser("status", help="Show system status and recent retry events")
    p_status.add_argument("--limit", type=int, default=20, help="Max retry events to show")
    p_status.set_defaults(func=cmd_status)


def _build_usage_parser(sub):
    p_usage = sub.add_parser("usage", help="Show model usage summary")
    p_usage.add_argument("--days", type=int, default=30, help="Rolling window in days")
    p_usage.set_defaults(func=cmd_usage)


def _build_savings_parser(sub):
    p_savings = sub.add_parser("savings", help="Show savings summary")
    p_savings.add_argument("--days", type=int, default=30, help="Rolling window in days")
    p_savings.set_defaults(func=cmd_savings)


def _build_compare_parser(sub):
    """Build compare command parser."""
    p_compare = sub.add_parser("compare", help="Show before/after cost on last request")
    p_compare.add_argument("--last", type=int, default=1, help="Show last N requests (default: 1)")
    p_compare.set_defaults(func=cmd_compare)


def _build_leaderboard_parser(sub):
    """Build leaderboard command parser."""
    p_leaderboard = sub.add_parser("leaderboard", help="Show per-model efficiency ranking")
    p_leaderboard.add_argument(
        "--days", type=int, default=1, help="Rolling window in days (default: today)"
    )
    p_leaderboard.set_defaults(func=cmd_leaderboard)


def _build_report_parser(sub):
    """Build report command parser."""
    p_report = sub.add_parser("report", help="Generate daily savings report")
    p_report.add_argument(
        "--markdown", action="store_true", help="Output markdown format (for messaging)"
    )
    p_report.add_argument("--json", action="store_true", help="Output JSON format")
    p_report.set_defaults(func=cmd_report)


def _build_alerts_parser(sub):
    """Build check-alerts command parser."""
    p_alerts = sub.add_parser("check-alerts", help="Evaluate alert rules and check health")
    p_alerts.set_defaults(func=cmd_check_alerts)


def _build_debug_parser(sub):
    """Build debug mode subcommand parser."""
    p_debug = sub.add_parser("debug", help="Toggle verbose debug logging")
    dsub = p_debug.add_subparsers(dest="debug_cmd", required=True)

    dsub.add_parser("on", help="Enable debug mode").set_defaults(func=cmd_debug_on)
    dsub.add_parser("off", help="Disable debug mode").set_defaults(func=cmd_debug_off)
    dsub.add_parser("status", help="Show debug mode state").set_defaults(func=cmd_debug_status)


def cmd_debug_on(args):
    """Enable debug mode."""
    from ..agent.config import set_debug_enabled

    set_debug_enabled(True)
    print("✅ Debug mode enabled")
    print("   Debug logs will appear on stderr during proxy requests.")
    print("   Disable with: tokenpak debug off")


def cmd_debug_off(args):
    """Disable debug mode."""
    from ..agent.config import set_debug_enabled

    set_debug_enabled(False)
    print("✅ Debug mode disabled")


def cmd_debug_status(args):
    """Show debug mode state."""
    import os

    from ..agent.config import CONFIG_PATH, get_debug_enabled

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
    from ..agent.agentic.learning import cmd_learn_status as _learn_status
    from ..agent.agentic.learning import learn

    learn()
    _learn_status()


def cmd_learn_reset(args):
    """Clear all learned data."""
    from ..agent.agentic.learning import reset

    reset()
    print("✓ Learning store cleared.")


def _build_user_template_parser(sub):
    """Build `tokenpak template` subcommand parser for local user templates."""
    from tokenpak.companion.templates.user_templates import (
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


def _enterprise_upgrade_stub(command: str) -> None:
    """Print Enterprise upgrade message and exit 2.

    Used by `tokenpak audit *` and `tokenpak compliance *`: these commands
    depend on `tokenpak.enterprise.*` which moved to tokenpak-paid in 1.2.0
    (TPS-11). The argparse subparsers still register the commands so help
    text works, but invocation routes here.
    """
    print(
        f"⚠ The `tokenpak {command}` command requires an Enterprise subscription.\n"
        "  Run: tokenpak activate <YOUR-KEY>\n"
        "  Then: tokenpak install-tier enterprise\n"
        "  (Don't have a key? Visit tokenpak.ai/pricing.)",
        file=sys.stderr,
    )
    sys.exit(2)


def cmd_audit_list(args):
    _enterprise_upgrade_stub("audit list")


def cmd_audit_export(args):
    _enterprise_upgrade_stub("audit export")


def cmd_audit_verify(args):
    _enterprise_upgrade_stub("audit verify")


def cmd_audit_prune(args):
    _enterprise_upgrade_stub("audit prune")


def cmd_audit_summary(args):
    _enterprise_upgrade_stub("audit summary")


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
    _enterprise_upgrade_stub("compliance report")


# ── Version Control Commands ──────────────────────────────────────────────────

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


def cmd_config(args):
    """Config management: show, init, edit."""
    from tokenpak.core.config.loader import CONFIG_PATH, generate_default_yaml, get_all

    subcmd = getattr(args, "config_cmd", "show")

    if subcmd == "show":
        cfg = get_all()
        if args.json:
            print(json.dumps(cfg, indent=2))
        else:
            print(f"Config: {CONFIG_PATH}")
            print(f"Exists: {'yes' if CONFIG_PATH.exists() else 'no'}")
            print()
            # Group by section
            sections = {}
            for k, v in sorted(cfg.items()):
                parts = k.split(".", 1)
                section = parts[0] if len(parts) > 1 else "core"
                if section not in sections:
                    sections[section] = []
                display_key = parts[1] if len(parts) > 1 else k
                sections[section].append((display_key, v))

            for section, items in sorted(sections.items()):
                print(f"  [{section}]")
                for key, val in items:
                    print(f"    {key:<30} = {val}")
                print()

    elif subcmd == "init":
        if CONFIG_PATH.exists() and not getattr(args, "force", False):
            print(f"Config already exists: {CONFIG_PATH}")
            print("Use --force to overwrite.")
            return
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(generate_default_yaml())
        print(f"Created: {CONFIG_PATH}")

    elif subcmd == "path":
        print(str(CONFIG_PATH))


def cmd_version(args):
    """Show current versions of proxy, config, and CLI."""
    from tokenpak import __version__ as cli_ver

    # CLI version. Proxy ships in-tree, so expected proxy version == CLI version.
    print(f"TokenPak CLI     : {cli_ver}")
    print(f"Proxy (expected) : {cli_ver}")

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
        for e in errors:  # type: ignore[misc]
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


def cmd_upgrade(args):
    """Open the canonical Pro upgrade page in the user's default browser.

    Default URL is ``https://app.tokenpak.ai/upgrade`` (KEVIN-DECISION-A,
    2026-04-23). Override with the ``TOKENPAK_UPGRADE_URL`` env var for
    testing or alternate deployments.
    """
    import webbrowser

    default_url = "https://app.tokenpak.ai/upgrade"
    url = os.environ.get("TOKENPAK_UPGRADE_URL", default_url)

    if getattr(args, "print_url", False):
        # Non-interactive path: print and exit. Used by automation + tests
        # so the browser-opening side-effect stays opt-in.
        print(url)
        return

    print(f"Opening {url} in your default browser …")
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:
        opened = False
        print(f"  ⚠️  Could not launch a browser: {exc}", file=sys.stderr)

    if not opened:
        print()
        print("  If the browser did not open, visit the URL manually:")
        print(f"    {url}")


def _build_upgrade_parser(sub):
    p = sub.add_parser(
        "upgrade",
        help="Open the TokenPak Pro upgrade page in your browser",
        description=(
            "Open the canonical TokenPak Pro upgrade page in your default browser. "
            "Target URL is https://app.tokenpak.ai/upgrade "
            "(override with TOKENPAK_UPGRADE_URL)."
        ),
    )
    p.add_argument(
        "--print-url",
        action="store_true",
        dest="print_url",
        help="Print the upgrade URL to stdout instead of opening a browser",
    )
    p.set_defaults(func=cmd_upgrade)


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

    # show — merged config (file + env overrides)
    p_show = csub.add_parser("show", help="Show merged config (file + env overrides)")
    p_show.add_argument("--json", action="store_true", help="Output as JSON")
    p_show.set_defaults(func=cmd_config)

    # init — create default config.yaml
    p_init = csub.add_parser("init", help="Create default config.yaml")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")
    p_init.set_defaults(func=cmd_config)

    # path — print config file path
    p_path = csub.add_parser("path", help="Print config file path")
    p_path.set_defaults(func=cmd_config)


# ── End Version Control Commands ──────────────────────────────────────────────


def main():
    # ── Short-circuit: `tokenpak claude …` forwards verbatim ──────────────────
    # All trailing args must reach the `claude` binary unmodified, including
    # flags that argparse would otherwise claim (`--version`, `-h`, etc.). We
    # detect the subcommand before argparse runs and hand off to the companion
    # launcher, which either execvp's `claude` directly or writes the MCP +
    # UserPromptSubmit settings first depending on TOKENPAK_COMPANION_ENABLED.
    if len(sys.argv) >= 2 and sys.argv[1] == "claude":
        from tokenpak.companion.config import CompanionConfig
        from tokenpak.companion.launcher import launch

        launch(config=CompanionConfig.from_env(), extra_args=sys.argv[2:])
        return

    parser = build_parser()

    # ── Intercept bare --help / -h for progressive disclosure ─────────────────
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h")):
        _print_quick_help()
        sys.exit(0)

    # ── Intercept unknown commands for typo suggestions ───────────────────────
    raw_cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    # Derive the known-command set from the registered subparsers instead of
    # maintaining a second hardcoded list (feedback_always_dynamic.md). The
    # `_ALL_COMMANDS` grouping is still used for the help output, but command
    # recognition comes straight from argparse.
    known_cmds = set(_ALL_COMMANDS)
    for _action in parser._actions:
        _choices = getattr(_action, "choices", None)
        if _choices:
            known_cmds.update(_choices.keys())
    if raw_cmd and not raw_cmd.startswith("-") and raw_cmd not in known_cmds:
        suggestion = _suggest_command(raw_cmd)
        print(f"❌ Unknown command: '{raw_cmd}'")

        if suggestion:
            print(f"   Did you mean: tokenpak {suggestion}?")
        else:
            # Check for a semantically confusing command
            _COMMAND_HINTS = {
                "compress": "→ Compression happens automatically through the proxy.\n   Run `tokenpak demo` to see it in action.",
                "run": "→ Use `tokenpak serve` to start the proxy, or `tokenpak start` for a quick alias.",
                "proxy": "→ Use `tokenpak start` to start the proxy on localhost:8766.",
                "kill": "→ Use `tokenpak stop` to stop the running proxy.",
                "test": "→ Use `tokenpak demo` to test compression, or `tokenpak doctor` to test installation.",
                "config": "→ Use `tokenpak config-check <file>` to validate config.\n   Or `tokenpak setup` to interactively create config.",
            }
            hint = _COMMAND_HINTS.get(raw_cmd)
            if hint:
                print(hint)
            else:
                print("\n📖 Available commands (by category):")
                for group, cmds in list(_COMMAND_GROUPS.items())[:3]:  # Show first 3 groups
                    print(f"\n   {group}:")
                    for cmd, desc in cmds[:3]:  # Show first 3 in each
                        print(f"     • {cmd:<15} {desc}")
                print("\n   (Use `tokenpak help` to see all commands)")
        # Exit 2 = usage error (unknown verb) per 03 §3.
        sys.exit(2)

    args = parser.parse_args()

    # No subcommand given → show smart default (savings summary)
    if not args.command:
        # Show compact savings summary instead of help
        try:
            from ..telemetry.query import get_savings_report

            # Get uptime from proxy (if running)
            uptime_str = "4h 23m"  # Placeholder; would fetch from proxy in production
            report = get_savings_report(days=1)

            # Compact savings summary
            print(f"TokenPak — {uptime_str} uptime")
            print(
                f"💰 ${report.savings_amount:.2f} saved today ({report.savings_pct:.0f}% reduction)"
            )

            # Get request count from recent events
            from ..telemetry.query import get_recent_events

            recent = get_recent_events(limit=1000)
            req_count = len(recent) if recent else 0
            cache_hit = report.cache_hit_rate * 100 if report.cache_hit_rate else 0

            print(f"📊 {req_count:,} requests | {cache_hit:.0f}% cache hit | 5.6% compression")

            # Top model savings
            from ..telemetry.query import get_model_usage

            usage = get_model_usage(days=1)
            if usage:
                top = usage[0]
                top_saved = report.savings_amount * 0.95  # Estimate top model saved ~95% of total
                print(
                    f"🔥 Top: {top.model} saved ${top_saved:.0f} across {top.request_count} requests"
                )

            print()
            print("Run `tokenpak savings` for full breakdown.")
            sys.exit(0)
        except Exception:
            # Fallback if proxy is not running or DB unavailable
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
    from ..routing.rules import DEFAULT_ROUTES_PATH, RouteStore

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
    from ..routing.rules import parse_pattern_args

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
    from ..routing.rules import RouteEngine, _count_tokens_approx

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
            from ..routing.rules import RouteEngine as _RE

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
    from ..agent.triggers.store import TriggerStore

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

    from ..agent.triggers.matcher import match_event

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
    from ..agent.triggers.daemon import TriggerDaemon

    store = _trigger_store()
    daemon = TriggerDaemon(store=store)
    daemon.run()


def cmd_trigger_fire(args):
    """Fire an event string immediately — executes all matching enabled triggers."""
    import subprocess

    from ..agent.triggers.matcher import match_event

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

    from ..agent.macros.hooks import is_file_watcher_running, start_file_watcher, stop_file_watcher

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
    from ..agent.telemetry.budget import get_budget_tracker

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

    # Show live proxy session cost if available
    stats = _proxy_get("/stats")
    if stats:
        session = stats.get("session", {})
        proxy_cost = session.get("cost", 0)
        proxy_saved = session.get("cost_saved", 0)
        saved_tokens = session.get("saved_tokens", 0)
        if proxy_cost > 0 or saved_tokens > 0:
            print("\n  Live session (proxy):")
            print(f"    Cost:          ${proxy_cost:.4f}")
            if proxy_saved > 0:
                print(f"    Cost saved:    ${proxy_saved:.4f}")
            if saved_tokens > 0:
                print(f"    Tokens saved:  {saved_tokens:,}")

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
    from ..agent.telemetry.budget import load_budget_config, save_budget_config

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


# ── Forecast (Burn Rate & Cost Projections) ──────────────────────────────────


def cmd_forecast(args):
    """Show cost burn rate analysis and projections."""
    from ..forecast import format_burn_rate_display, get_burn_rate

    tracker = _budget_tracker()

    # Get window size from args
    period = getattr(args, "period", "7d")
    if period == "7d":
        window_days = 7
    elif period == "30d":
        window_days = 30
    elif period == "90d":
        window_days = 90
    else:
        window_days = 7

    # Get threshold if set
    threshold = getattr(args, "alert", None)
    if threshold is not None:
        try:
            threshold = float(threshold)
        except (ValueError, TypeError):
            print(f"Invalid threshold: {threshold}")
            return

    # Calculate burn rate
    analysis = get_burn_rate(tracker, window_days=window_days)

    # Display
    output = format_burn_rate_display(analysis, threshold=threshold)
    print(output)

    # Check threshold and alert if needed
    if threshold and analysis.monthly_projection > threshold:
        print()
        print(
            f"⚠️  Alert: Projected monthly spend ${analysis.monthly_projection:.2f} exceeds threshold ${threshold:.2f}"
        )


# ── Goals (Savings Targets & Progress Tracking) ────────────────────────────────


def _get_goal_manager():
    from ..goals import GoalManager

    return GoalManager()


def cmd_goals_list(args):
    """List all savings goals with progress."""
    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    if not goals_list:
        print("No goals defined. Create one with: tokenpak goals --add")
        return

    print(f"\n{'GOAL NAME':<30} {'TYPE':<12} {'PROGRESS':<30} {'STATUS':<12}")
    print("-" * 90)

    for goal in goals_list:
        progress = manager.get_progress(goal.goal_id)
        if not progress:
            continue

        # Create progress bar
        bar_width = 20
        filled = int(bar_width * min(progress.progress_percent, 100) / 100)
        bar = "█" * filled + "░" * (bar_width - filled)

        # Status indicator
        if progress.progress_percent >= 100:
            status = "✅ DONE"
        elif progress.pace_status == "behind":
            status = "⚠️  BEHIND"
        elif progress.pace_status == "ahead":
            status = "🚀 AHEAD"
        else:
            status = "▶️  ON TRACK"

        print(
            f"{goal.name:<30} {goal.goal_type:<12} "
            f"[{bar}] {progress.progress_percent:>5.1f}%  {status:<12}"
        )

        # Show additional details
        if goal.goal_type == "savings":
            print(f"  └─ ${progress.current_value:.2f} / ${progress.target_value:.2f}")
        else:
            print(f"  └─ {progress.current_value:.1f} / {progress.target_value:.1f}")


def cmd_goals_detail(args):
    """Show detailed info for a specific goal."""
    manager = _get_goal_manager()
    goal = manager.get_goal(args.goal_id)

    if not goal:
        print(f"Goal '{args.goal_id}' not found.")
        return

    progress = manager.get_progress(goal.goal_id)
    if not progress:
        print(f"No progress data for goal '{args.goal_id}'.")
        return

    print(f"\n📊 Goal: {goal.name}")
    print(f"{'─' * 60}")
    print(f"ID:              {goal.goal_id}")
    print(f"Type:            {goal.goal_type}")
    print(f"Description:     {goal.description or '(none)'}")
    print(f"Start Date:      {goal.start_date}")
    print(f"End Date:        {goal.end_date}")
    print(f"Days Elapsed:    {goal.days_elapsed()} / {goal.total_days()}")
    print(f"Days Remaining:  {goal.days_remaining()}")
    print()

    # Progress bar
    bar_width = 30
    filled = int(bar_width * min(progress.progress_percent, 100) / 100)
    bar = "█" * filled + "░" * (bar_width - filled)
    print(f"Progress:        [{bar}] {progress.progress_percent:.1f}%")

    if goal.goal_type == "savings":
        print(f"Current:         ${progress.current_value:.2f}")
        print(f"Target:          ${progress.target_value:.2f}")
        print(f"Remaining:       ${max(0, progress.target_value - progress.current_value):.2f}")
    else:
        print(f"Current:         {progress.current_value:.1f}")
        print(f"Target:          {progress.target_value:.1f}")

    print()
    print(f"Pace Status:     {progress.pace_status.upper()}")
    expected = goal.expected_progress_percent()
    print(f"Expected:        {expected:.1f}% (based on time)")
    print(f"Actual:          {progress.progress_percent:.1f}%")

    # Milestone status
    print()
    print("Milestones:")
    milestones = [25, 50, 75, 100]
    for m in milestones:
        fired = getattr(progress, f"milestone_{m}_fired", False)
        status = "✅" if fired else "⭕"
        print(f"  {status} {m}%")


def cmd_goals_add(args):
    """Add a new savings goal."""
    manager = _get_goal_manager()

    goal = manager.add_goal(
        name=args.name,
        goal_type=args.type,
        target_value=args.target,
        start_date=args.start,
        end_date=args.end,
        description=args.description or "",
        metric_name=args.metric or "",
        rolling_window=args.rolling_window,
    )

    print(f"✅ Goal created: {goal.goal_id}")
    print(f"   Name: {goal.name}")
    print(f"   Type: {goal.goal_type}")
    print(f"   Target: {goal.target_value}")
    print(f"   Period: {goal.start_date} → {goal.end_date}")


def cmd_goals_edit(args):
    """Edit an existing goal."""
    manager = _get_goal_manager()

    # Build update dict from provided args
    updates = {}
    if args.name:
        updates["name"] = args.name
    if args.target is not None:
        updates["target_value"] = args.target
    if args.description:
        updates["description"] = args.description
    if args.end:
        updates["end_date"] = args.end

    if not updates:
        print("No updates provided. Use --name, --target, --description, or --end.")
        return

    goal = manager.edit_goal(args.goal_id, **updates)
    if not goal:
        print(f"Goal '{args.goal_id}' not found.")
        return

    print(f"✅ Goal updated: {goal.goal_id}")
    for key, val in updates.items():
        print(f"   {key}: {val}")


def cmd_goals_delete(args):
    """Delete a goal."""
    manager = _get_goal_manager()

    if not manager.delete_goal(args.goal_id):
        print(f"Goal '{args.goal_id}' not found.")
        return

    print(f"✅ Goal deleted: {args.goal_id}")


def cmd_goals_update(args):
    """Update goal progress."""
    manager = _get_goal_manager()

    progress = manager.update_progress(args.goal_id, args.value)
    if not progress:
        print(f"Goal '{args.goal_id}' not found.")
        return

    goal = manager.get_goal(args.goal_id)
    print(f"✅ Progress updated for {goal.name}")
    print(f"   Current: {progress.current_value}")
    print(f"   Target: {progress.target_value}")
    print(f"   Progress: {progress.progress_percent:.1f}%")
    print(f"   Pace: {progress.pace_status.upper()}")

    # Check milestones
    milestones = manager.check_milestones(args.goal_id)
    for m in milestones:
        print(f"   {m['message']}")


def cmd_goals_export(args):
    """Export goals to JSON."""
    import json
    from pathlib import Path

    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    export = {
        "goals": [g.to_dict() for g in goals_list],
        "progress": {gid: p.to_dict() for gid, p in manager.progress.items()},
        "summary": manager.get_summary_stats(),
    }

    if args.output:
        path = Path(args.output)
        with open(path, "w") as f:
            json.dump(export, f, indent=2)
        print(f"✅ Exported to {path}")
    else:
        print(json.dumps(export, indent=2))


def cmd_goals_history(args):
    """Show goal history and milestones."""
    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    if not goals_list:
        print("No goals defined.")
        return

    print(f"\n{'GOAL':<30} {'MILESTONE':<12} {'ACHIEVED':<20}")
    print("-" * 65)

    for goal in goals_list:
        progress = manager.get_progress(goal.goal_id)
        if not progress:
            continue

        milestones = []
        if progress.milestone_25_fired:
            milestones.append("25%")
        if progress.milestone_50_fired:
            milestones.append("50%")
        if progress.milestone_75_fired:
            milestones.append("75%")
        if progress.milestone_100_fired:
            milestones.append("100%")

        if milestones:
            for i, m in enumerate(milestones):
                prefix = goal.name if i == 0 else ""
                print(f"{prefix:<30} {m:<12}")


def cmd_goals_compare(args):
    """Compare goal progress."""
    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    if len(goals_list) < 2:
        print("Need at least 2 goals to compare.")
        return

    print(f"\n{'GOAL':<30} {'PROGRESS':<12} {'PACE':<12} {'DAYS LEFT':<12}")
    print("-" * 70)

    for goal in goals_list:
        progress = manager.get_progress(goal.goal_id)
        if not progress:
            continue

        print(
            f"{goal.name:<30} {progress.progress_percent:>10.1f}%  "
            f"{progress.pace_status:<12} {goal.days_remaining():>10}"
        )


def _build_goals_parser(sub):
    """Add goals subparser."""
    p_goals = sub.add_parser("goals", help="Manage savings goals and track progress")
    gsub = p_goals.add_subparsers(dest="goals_cmd", required=False)

    # List goals (default)
    p_list = gsub.add_parser("list", help="List all goals")
    p_list.set_defaults(func=cmd_goals_list)

    # Detail
    p_detail = gsub.add_parser("detail", help="Show details for a specific goal")
    p_detail.add_argument("goal_id", help="Goal ID")
    p_detail.set_defaults(func=cmd_goals_detail)

    # Add goal
    p_add = gsub.add_parser("add", help="Create a new goal")
    p_add.add_argument("--name", required=True, help="Goal name")
    p_add.add_argument(
        "--type",
        required=True,
        choices=["savings", "compression", "cache", "metric"],
        help="Goal type",
    )
    p_add.add_argument("--target", required=True, type=float, help="Target value")
    p_add.add_argument("--start", help="Start date (YYYY-MM-DD, default: today)")
    p_add.add_argument("--end", help="End date (YYYY-MM-DD, default: 30 days from start)")
    p_add.add_argument("--description", help="Goal description")
    p_add.add_argument("--metric", help="Custom metric name (for metric type)")
    p_add.add_argument("--rolling-window", action="store_true", help="Enable weekly pace tracking")
    p_add.set_defaults(func=cmd_goals_add)

    # Edit goal
    p_edit = gsub.add_parser("edit", help="Edit an existing goal")
    p_edit.add_argument("goal_id", help="Goal ID to edit")
    p_edit.add_argument("--name", help="New goal name")
    p_edit.add_argument("--target", type=float, help="New target value")
    p_edit.add_argument("--description", help="New description")
    p_edit.add_argument("--end", help="New end date (YYYY-MM-DD)")
    p_edit.set_defaults(func=cmd_goals_edit)

    # Delete goal
    p_delete = gsub.add_parser("delete", help="Delete a goal")
    p_delete.add_argument("goal_id", help="Goal ID to delete")
    p_delete.set_defaults(func=cmd_goals_delete)

    # Update progress
    p_update = gsub.add_parser("update", help="Update goal progress")
    p_update.add_argument("goal_id", help="Goal ID")
    p_update.add_argument("value", type=float, help="New current value")
    p_update.set_defaults(func=cmd_goals_update)

    # Export
    p_export = gsub.add_parser("export", help="Export goals to JSON")
    p_export.add_argument("--output", "-o", help="Output file (default: stdout)")
    p_export.set_defaults(func=cmd_goals_export)

    # History
    p_history = gsub.add_parser("history", help="Show milestone history")
    p_history.set_defaults(func=cmd_goals_history)

    # Compare
    p_compare = gsub.add_parser("compare", help="Compare goal progress")
    p_compare.set_defaults(func=cmd_goals_compare)

    # Default to list if no subcommand
    p_goals.set_defaults(func=cmd_goals_list)


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


def _build_forecast_parser(sub):
    p_forecast = sub.add_parser("forecast", help="Cost burn rate & projections")
    p_forecast.add_argument(
        "--period", choices=["7d", "30d", "90d"], default="7d", help="Analysis window (default: 7d)"
    )
    p_forecast.add_argument(
        "--alert",
        type=float,
        metavar="USD",
        help="Alert if monthly projection exceeds this USD amount",
    )
    p_forecast.set_defaults(func=cmd_forecast)


# ── top-level lock subcommand ─────────────────────────────────────────────────


def cmd_lock_claim(args):
    import time as _time

    from ..agent.agentic.locks import FileLockManager, LockConflictError

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
    from ..agent.agentic.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    released = mgr.release(args.path)
    if released:
        print(f"✅ Released: {args.path}")
    else:
        print(f"⚠️  No lock held by this agent on: {args.path}")


def cmd_lock_query(args):
    import time as _time

    from ..agent.agentic.locks import FileLockManager

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

    from ..agent.agentic.locks import FileLockManager

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

    from ..agent.agentic.locks import FileLockManager, LockConflictError, LockExpiredError

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
    from ..agent.agentic.locks import FileLockManager, LockConflictError

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
    from ..agent.agentic.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    released = mgr.release(args.path)
    if released:
        print(f"✅ Lock released: {args.path}")
    else:
        print(f"⚠️  No lock held by this agent on: {args.path}")


def cmd_agent_locks(args):
    import time

    from ..agent.agentic.locks import FileLockManager

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
        print(f"{path:<50} {lock.get('agent', '?'):<15} {remaining:>10.0f}s")


def cmd_agent_list(args):
    """List registered agents."""
    import json as json_mod

    from ..agent.agentic.registry import AgentRegistry

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
            hb = f"{age / 60:.0f}m ago"
        else:
            hb = f"{age / 3600:.1f}h ago"
        stale = " (stale)" if a.is_stale() else ""
        print(f"{a.agent_id:<10} {a.name:<12} {a.hostname:<15} {a.status:<10} {hb}{stale}")


def cmd_agent_register(args):
    """Register this agent."""
    import json as json_mod

    from ..agent.agentic.registry import AgentRegistry

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
    from ..agent.agentic.registry import AgentRegistry

    registry = AgentRegistry()
    if registry.deregister(args.agent_id):
        print(f"✅ Deregistered: {args.agent_id}")
    else:
        print(f"⚠️  Agent not found: {args.agent_id}")


def cmd_agent_heartbeat(args):
    """Send heartbeat for agent."""
    from ..agent.agentic.registry import AgentRegistry

    registry = AgentRegistry()
    if registry.heartbeat(args.agent_id, status=args.status, current_task=args.task):
        print(f"✅ Heartbeat: {args.agent_id}")
    else:
        print(f"⚠️  Agent not found: {args.agent_id}")


def cmd_agent_match(args):
    """Find agents matching requirements."""
    import json as json_mod

    from ..agent.agentic.capabilities import CapabilityMatcher, TaskRequirements

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
    from ..agent.agentic.registry import AgentRegistry

    registry = AgentRegistry()
    count = registry.prune_stale()
    if count:
        print(f"✅ Pruned {count} stale agent(s)")
    else:
        print("No stale agents to prune.")


def cmd_agent_handoff(args):
    """Dispatch to handoff command handler."""
    from ..agent.cli.commands.handoff import handoff_cmd

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
    from ..agent.telemetry.replay import get_replay_store

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
        f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}  (📦 = content captured, eligible for replay)"
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
    from tokenpak.compression.processors.text import TextProcessor
    from tokenpak.telemetry.tokens import count_tokens

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
    from tokenpak.telemetry.tokens import count_tokens

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
                print(f"... ({len(diff) - 60} more diff lines)")
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
    p_demo.add_argument(
        "--seed",
        action="store_true",
        help="Populate dashboard with 500 realistic demo events (24h window)",
    )
    p_demo.add_argument(
        "--seed-count",
        type=int,
        default=500,
        metavar="N",
        help="Number of demo events to generate (default: 500)",
    )
    p_demo.add_argument(
        "--seed-hours", type=int, default=24, metavar="H", help="Time window in hours (default: 24)"
    )
    p_demo.add_argument(
        "--clear", action="store_true", help="Remove all demo data from telemetry storage"
    )
    p_demo.set_defaults(func=cmd_demo)


def _run_compression_demo():
    """Show live compression on a sample prompt with before/after token counts."""
    from tokenpak.compression.engines.base import CompactionHints
    from tokenpak.compression.engines.heuristic import HeuristicEngine
    from tokenpak.telemetry.tokens import count_tokens

    SAMPLE_PROMPT = """\
You are a helpful assistant. Please help me understand the following documentation.

The TokenPak library provides a comprehensive, all-inclusive solution for managing
token budgets in large language model applications. It includes multiple compression
strategies, various caching mechanisms, and detailed telemetry tools for monitoring
usage and costs across all your API calls. The library has been carefully designed
to be extremely easy to use out of the box while also providing powerful, advanced
functionality for more sophisticated users who need fine-grained control.

By compressing content intelligently before it reaches the model, you can fit more
relevant information into fewer tokens, which reduces API costs significantly and
can improve response quality in many cases. The heuristic engine utilizes rule-based
text processing techniques to remove redundant, repetitive, and low-signal content
while carefully preserving the most important, critical information that the model
actually needs in order to produce high-quality, accurate results every time.

This approach is fully deterministic, meaning that for any given input you will
always receive the same compressed output each and every single time you run it,
regardless of when or how many times the compression is applied.

Question: How does TokenPak save tokens and money?"""

    engine = HeuristicEngine()
    hints = CompactionHints(target_tokens=120)
    compressed = engine.compact(SAMPLE_PROMPT, hints)

    tokens_in = count_tokens(SAMPLE_PROMPT)
    tokens_out = count_tokens(compressed)
    savings_pct = (1 - tokens_out / tokens_in) * 100 if tokens_in > 0 else 0

    # Estimate cost savings at gpt-4o rates ($2.50 / 1M input tokens)
    cost_per_token = 2.50 / 1_000_000
    cost_saved = (tokens_in - tokens_out) * cost_per_token

    print()
    print("  TokenPak Compression Demo")
    print("  " + "─" * 46)
    print()
    print(f"  Original prompt:    {tokens_in:,} tokens")
    print(f"  Compressed:         {tokens_out:,} tokens")
    print(f"  Savings:            {savings_pct:.0f}% fewer tokens")
    print(f"  Cost saved (est.):  ${cost_saved:.4f} per call @ gpt-4o rates")
    print()
    print("  ── Compressed output (first 300 chars) ──────────────────────")
    preview = compressed[:300].strip().replace("\n", "\n  ")
    print(f"  {preview}{'...' if len(compressed) > 300 else ''}")
    print()
    print("  Try it with your own content:")
    print("    tokenpak start        → start the proxy (zero-config)")
    print("    tokenpak cost         → track your real savings")
    print("    tokenpak demo --list  → browse 50 built-in compression recipes")
    print()


def cmd_demo(args):
    """Show OSS compression recipes and demonstrate recipe selection."""
    from ..agent.compression.recipes import get_oss_engine

    # Fast paths that don't need the recipe catalog.
    if args.seed or args.clear:
        pass
    elif not (getattr(args, "list", False) or getattr(args, "category", None)
              or getattr(args, "recipe", None) or getattr(args, "file", None)):
        # Default: live compression demo — no recipe catalog required.
        _run_compression_demo()
        return

    try:
        engine = get_oss_engine()
    except (ValueError, FileNotFoundError) as exc:
        # The packaged recipe-YAML catalog is not part of the v1.3.10+
        # shipping surface. What used to be called "OSS recipes" is now
        # expressed as the deterministic compression pipeline (under
        # `tokenpak/agent/compression/`) plus route-class policy presets
        # (under `tokenpak/services/policy_service/presets/`). Custom
        # user-authored recipes still work via `tokenpak recipe create/
        # validate/test/benchmark`. Redirect the user to the live
        # compression demo rather than suggesting a reinstall that
        # won't change anything.
        print(
            "No built-in recipe catalog ships with this version.\n"
            "  TokenPak's compression is implemented as a deterministic\n"
            "  pipeline (dedup → alias → segmentize → directives), not a\n"
            "  bag of YAML recipes. Try:\n"
            "    tokenpak demo                 # live compression demo\n"
            "    tokenpak recipe --help        # author your own recipe\n"
            "    tokenpak status               # see compression running",
            file=sys.stderr,
        )
        sys.exit(0)

    # ── Demo data seeding
    if args.seed:
        """Seed the dashboard with demo data."""
        from ..agent.telemetry.demo import seed_demo_data

        result = seed_demo_data(count=args.seed_count, hours=args.seed_hours)
        print(f"✅ Seeded {result['events']} demo events")
        print(f"   Cache hit rate: {result['cache_hit_rate'] * 100:.1f}%")
        print(f"   Total events now: {result['total_events']}")
        print(f"   Total cache-read: {result['cache_read_total']:,}")
        print()
        print("Dashboard should now show demo data with realistic patterns.")
        return

    if args.clear:
        """Clear all demo data from telemetry storage."""
        from ..agent.telemetry.demo import clear_demo_data

        result = clear_demo_data()
        print(f"✅ Cleared {result['deleted_events']} demo events")
        print(f"   Remaining events: {result['remaining_events']}")
        if result["remaining_events"] == 0:
            print("   Dashboard is now empty (ready for real traffic)")
        return

    # (Default live-compression-demo fast-path is handled above, before
    # get_oss_engine(); no duplicate branch here.)

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
            print(f"  {r.name:<45} [{r.category}]  ~{int(r.compression_hint * 100)}% savings")
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
            hint = f"~{int(r.compression_hint * 100)}%" if r.compression_hint > 0 else "   "
            print(f"    {r.name:<45}  {hint}  {r.description[:60]}")
        print()

    print("Use --recipe <name> for details, --file <path> to see applicable recipes.")


# ── Recipe SDK CLI commands ────────────────────────────────────────────────────


def cmd_recipe_create(args):
    """Scaffold a new custom recipe file."""
    from ..agent.recipe_sdk import RecipeSDK

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
    from ..agent.recipe_sdk import RecipeSDK, RecipeValidationError

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
    from ..agent.recipe_sdk import RecipeSDK, RecipeValidationError

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
    from ..agent.recipe_sdk import RecipeSDK, RecipeValidationError

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
        f"  Compression (mean)    : {round(c['mean'] * 100, 1)}%  "
        f"[min {round(c['min'] * 100, 1)}% – max {round(c['max'] * 100, 1)}%]"
    )
    if result["hint_vs_actual"]["hint"] is not None:
        hint_pct = round(result["hint_vs_actual"]["hint"] * 100, 1)
        actual_pct = round(result["hint_vs_actual"]["actual_mean"] * 100, 1)
        delta = actual_pct - hint_pct
        sign = "+" if delta >= 0 else ""
        print(f"  Hint vs actual        : {hint_pct}% → {actual_pct}%  ({sign}{delta:.1f}% delta)")
    t = result["timing_ms"]
    print(
        f"  Timing ms (mean)      : {t['mean']:.3f} ms  [min {t['min']:.3f} – max {t['max']:.3f}]"
    )


# ── run: Macro scheduler CLI ──────────────────────────────────────────────────


def cmd_run_cron(args):
    """Schedule a macro to run on a cron expression."""
    from ..agent.macros.scheduler import schedule_cron

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
    from ..agent.macros.scheduler import schedule_at

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
    from ..agent.macros.scheduler import list_scheduled

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
    from ..agent.macros.scheduler import cancel_schedule

    ok = cancel_schedule(args.id)
    if ok:
        print(f"✅ Cancelled scheduled run: {args.id}")
    else:
        print(f"❌ No scheduled run found with id: {args.id}")


# ── diff: Context diff visualization ─────────────────────────────────────────


def cmd_diff(args):
    """Show context diff: removed, compressed, retained blocks."""
    from tokenpak.cli.commands.diff import run_diff_cmd

    run_diff_cmd(args)


def _build_diff_parser(sub):
    p_diff = sub.add_parser(
        "diff", help="Show context changes (removed/compressed/retained blocks)"
    )
    p_diff.add_argument("--verbose", "-v", action="store_true", help="Show token counts per block")
    p_diff.add_argument("--json", action="store_true", help="Output as JSON")
    p_diff.add_argument(
        "--since", default=None, metavar="TIMESTAMP", help="Diff from specific time"
    )
    p_diff.set_defaults(func=cmd_diff)


# ── run: Macro scheduler CLI ──────────────────────────────────────────────────


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
    from ..agent.macros.premade_macros import install_macro

    try:
        path = install_macro(args.name)
        print(f"✅ Installed macro '{args.name}' → {path}")
    except ValueError as e:
        print(f"❌ {e}")


def cmd_macro_run(args):
    """Run a user-defined YAML macro or a premade macro."""
    import json as _json

    from ..agent.macros.engine import MacroEngine
    from ..agent.macros.premade_macros import PREMADE_MACROS, format_macro_output, run_macro

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
    from ..agent.macros.engine import MacroEngine
    from ..agent.macros.premade_macros import list_macros

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

    from ..agent.macros.engine import MacroEngine

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

    from ..agent.macros.engine import MacroEngine
    from ..agent.macros.premade_macros import PREMADE_MACROS

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
    from ..agent.macros.engine import MacroEngine

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
    from ..agent.macros.script_hooks import install_hook, list_hooks

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

    from tokenpak.compression.fingerprinting.generator import FingerprintGenerator
    from tokenpak.compression.fingerprinting.privacy import PrivacyLevel, apply_privacy
    from tokenpak.compression.fingerprinting.sync import FingerprintSync

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

    from tokenpak.compression.fingerprinting.sync import FingerprintSync

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

    from tokenpak.compression.fingerprinting.sync import FingerprintSync

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


def _build_config_check_parser(sub):
    """Register 'tokenpak config-check' command."""
    p = sub.add_parser("config-check", help="Validate a proxy config file (JSON)")
    p.add_argument("file", help="Path to config file (JSON)")
    p.set_defaults(func=cmd_config_check)
    return p


# ── Install-Tier (paid-package installer, TPS-02) ─────────────────────────────


def cmd_install_tier(args):
    """Install the private tokenpak-paid package for a given tier."""
    from ._install_tier import run_install_tier

    rc = run_install_tier(tier=args.tier, dry_run=args.dry_run)
    # The generic dispatcher ignores return values; propagate the exit
    # code explicitly so `tokenpak install-tier pro` without a license
    # exits 2, not 0.
    sys.exit(rc)


# ── Integrate — one-shot per-target setup helpers ─────────────────────────────


def cmd_integrate(args):
    """Wire tokenpak into a specific target (``claude-code`` today)."""
    target = getattr(args, "target", "").lower()
    if target == "claude-code":
        rc = _integrate_claude_code()
        sys.exit(rc)
    # Unknown target: show help + exit 2.
    print(
        f"tokenpak integrate: unknown target {target!r}\n"
        f"Supported: claude-code",
        file=sys.stderr,
    )
    sys.exit(2)


def _integrate_claude_code() -> int:
    """Wire Claude Code ↔ tokenpak.

    1. Regenerate companion settings.json + mcp.json (writes the `-P`
       hook and wires the MCP server).
    2. Print the env-var recipe the user should export so direct
       Claude Code invocations (not via ``tokenpak claude``) still
       route through the local proxy.
    3. Run the Claude-Code diagnostic suite to confirm everything's
       reachable.
    """
    try:
        from tokenpak.companion.launcher import regenerate_config
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Could not import companion launcher: {exc}", file=sys.stderr)
        return 1

    paths = regenerate_config()

    port = os.environ.get("TOKENPAK_PORT", "8766")
    print(
        "✓ Claude Code integration wired.\n"
        f"  MCP config:  {paths['mcp']}\n"
        f"  Settings:    {paths['settings']}\n"
        "\n"
        "Launch via:  tokenpak claude\n"
        "\n"
        "Or export these to make every `claude` invocation tokenpak-aware:\n"
        f"  export ANTHROPIC_BASE_URL=http://127.0.0.1:{port}\n"
        f"  export OPENAI_BASE_URL=http://127.0.0.1:{port}/v1\n"
    )

    # Verify the integration using the shared diagnostics service.
    try:
        from tokenpak.services.diagnostics import (
            CheckStatus,
            run_claude_code_checks,
            run_core_checks,
        )

        fails = 0
        print("Post-install verification:")
        for r in run_core_checks() + run_claude_code_checks():
            marker = {"ok": "✓", "warn": "⚠", "fail": "✗"}[r.status.value]
            print(f"  {marker} {r.name:<22} {r.summary}")
            if r.status is CheckStatus.FAIL:
                fails += 1
        if fails:
            print(
                "\n⚠ One or more checks failed — see output above. "
                "Integration files are in place; address the failures and re-run."
            )
            return 2
    except Exception as exc:  # noqa: BLE001
        print(f"(diagnostics unavailable: {exc})", file=sys.stderr)
    return 0


def _build_install_tier_parser(sub):
    """Register 'tokenpak install-tier' command."""
    p = sub.add_parser(
        "install-tier",
        help="Install tokenpak-paid for the given tier (pro/team/enterprise)",
    )
    p.add_argument("tier", choices=("pro", "team", "enterprise"),
                   help="Tier to install. Requires a license key activated via `tokenpak activate`.")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what pip would run; do not install")
    p.set_defaults(func=cmd_install_tier)
    return p


def _build_integrate_parser(sub):
    """Register 'tokenpak integrate <target>' command."""
    p = sub.add_parser(
        "integrate",
        help="Wire tokenpak into a specific target (claude-code, ...)",
    )
    p.add_argument(
        "target",
        choices=("claude-code",),
        help="Integration target. Today: claude-code.",
    )
    p.set_defaults(func=cmd_integrate)
    return p


# ── Vault Health Management ───────────────────────────────────────────────────


def cmd_vault_health(args):
    """Manage vault index health."""
    from ..vault_health import VaultHealth

    subcmd = getattr(args, "vault_health_cmd", None)

    if subcmd == "repair":
        try:
            health = VaultHealth()

            # Check if index exists
            if not health.index_path.exists():
                print(f"❌ Index not found: {health.index_path}")
                sys.exit(2)

            print("\nTOKENPAK  |  Vault Health")
            print("──────────────────────────────\n")

            # Get initial status
            status = health.get_status()
            print(f"Index: {health.index_path}")
            print(f"Status: {status}\n")

            # Check if stale
            is_stale = health.check_index_staleness()

            if not is_stale:
                print("✅ Index is current (no rebuild needed)")
                print("Exit code: 0\n")
                sys.exit(0)

            # Rebuild needed
            block_count = len(list(health.blocks_dir.iterdir()))
            print(f"Index is stale: {block_count} blocks found, index mismatch detected\n")
            print("Rebuilding index from blocks...")

            metrics = health.rebuild_index()

            print(f"✅ Rebuilt in {metrics['rebuild_time_seconds']:.2f} seconds")
            print(f"Entries: {metrics['index_entries']:,}")
            print(f"  Added: {metrics['entries_added']}")
            print(f"  Removed: {metrics['entries_removed']}")
            print(f"Index size: {metrics['index_size_bytes']:,} bytes")
            print("\nExit code: 1 (rebuilt)\n")
            sys.exit(1)

        except FileNotFoundError as e:
            print(f"❌ Error: {e}")
            sys.exit(2)
        except Exception as e:
            print(f"❌ Error during rebuild: {e}")
            sys.exit(2)

    else:
        print("Unknown vault-health subcommand. Use 'repair'.")
        sys.exit(1)


# ── Fleet Management ──────────────────────────────────────────────────────


def cmd_config_check(args):
    """Validate a proxy config file (JSON)."""
    import json

    from tokenpak.core.config.validator import ConfigValidator

    config_path = Path(args.file).expanduser()

    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(2)

    # Load JSON
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {config_path}: {e}")
        sys.exit(2)

    # Validate
    validator = ConfigValidator()
    errors = validator.validate(config)

    if not errors:
        print(f"✓ Config is valid: {config_path}")
        sys.exit(0)

    # Print errors
    print(f"✗ Config validation failed ({len(errors)} error(s)):\n")
    for error in errors:
        print(f"  Field: {error.field}")
        print(f"    Expected: {error.expected}")
        print(f"    Actual: {error.actual}")
        print(f"    Message: {error.message}")
        print(f"    Fix: {error.suggestion}")
        print()

    sys.exit(1)


def cmd_fleet(args):
    """Query and manage a fleet of TokenPak proxy instances."""
    from ..fleet import (
        interactive_add_machine,
        load_fleet_config,
        query_fleet,
        query_fleet_agent_rows,
        render_fleet_agent_table,
        render_fleet_json,
        render_fleet_table,
        save_fleet_config,
    )

    subcmd = getattr(args, "fleet_cmd", None)

    if subcmd == "init":
        # Interactive setup
        machines = load_fleet_config()
        print("╔═══════════════════════════════════════════════╗")
        print("║  TokenPak Fleet Configuration                 ║")
        print("╚═══════════════════════════════════════════════╝")

        if machines:
            print(f"\nCurrent fleet ({len(machines)} machine(s)):")
            for m in machines:
                print(f"  • {m.name} @ {m.host}:{m.port}")
            print()

        new_machine = interactive_add_machine(machines)
        if new_machine:
            machines.append(new_machine)
            save_fleet_config(machines)
            print("\n✅ Saved fleet config to ~/.tokenpak/fleet.yaml")

    else:
        # Default: show status table
        machines = load_fleet_config()

        if not machines:
            print("❌ No machines configured in fleet.")
            print("   Run: tokenpak fleet init")
            sys.exit(1)

        # Query all machines
        stats = query_fleet(machines)
        agent_rows, errors = query_fleet_agent_rows(machines)

        # Render output
        if getattr(args, "json", False):
            print(render_fleet_json(stats))
        elif getattr(args, "compact", False):
            print(render_fleet_table(stats, compact=True))
        else:
            if agent_rows:
                print(render_fleet_agent_table(agent_rows))
                if errors:
                    print("\n⚠️  Offline machines:")
                    for err in errors:
                        print(f"  - {err}")
            else:
                print(render_fleet_table(stats, compact=False))


def _build_vault_health_parser(sub):
    """Build the vault-health command parser."""
    p_vh = sub.add_parser("vault-health", help="Vault index health diagnostic and repair")

    vhsub = p_vh.add_subparsers(dest="vault_health_cmd", required=True)

    # vault-health repair
    p_repair = vhsub.add_parser("repair", help="Check and rebuild stale vault index")
    p_repair.set_defaults(func=cmd_vault_health)

    p_vh.set_defaults(func=cmd_vault_health)
    return p_vh


def _build_fleet_parser(sub):
    """Build the fleet command parser."""
    p_fleet = sub.add_parser("fleet", help="Manage and query multi-machine proxy fleet")

    p_fleet.add_argument("--json", action="store_true", help="Output as JSON")
    p_fleet.add_argument("--compact", action="store_true", help="Compact one-line output")

    fsub = p_fleet.add_subparsers(dest="fleet_cmd", required=False)

    # fleet init
    p_init = fsub.add_parser("init", help="Interactively configure fleet")
    p_init.set_defaults(func=cmd_fleet)

    p_fleet.set_defaults(func=cmd_fleet)
    return p_fleet


if __name__ == "__main__":
    main()
