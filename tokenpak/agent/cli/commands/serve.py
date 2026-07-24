"""tokenpak/agent/cli/commands/serve.py

Phase 5A: `tokenpak serve` command
====================================
Starts the ingest API server on the specified port.

Usage:
    tokenpak serve [--port PORT] [--host HOST] [--workers N]

Workers:
    --workers N     Spawn N worker processes (each on its own CPU core).
                    Default: max(1, os.cpu_count() // 2).
                    Workers restart automatically on crash.
                    Graceful shutdown sends SIGTERM to all workers.
                    Telemetry (SQLite WAL) is safe for concurrent workers.

Single-worker mode (N=1) uses the in-process app object directly so that
hot-reload / test fixtures can inject a custom factory.  Multi-worker mode
passes the app as an import string + factory=True so uvicorn's multiprocess
supervisor can fork fresh worker processes.
"""

from __future__ import annotations

import logging
import os
import select
import sys

logger = logging.getLogger(__name__)

# Import string used by uvicorn when workers > 1.
# Uvicorn calls create_ingest_app() in each worker process.
_APP_FACTORY_IMPORT = "tokenpak.agent.ingest.api:create_ingest_app"


# ---------------------------------------------------------------------------
# First-run prompt (opt-in, defaults to OFF)
# ---------------------------------------------------------------------------

_FIRST_RUN_SENTINEL = "metrics_prompt_shown"


def _maybe_show_metrics_prompt() -> None:
    """Print the one-time metrics opt-in prompt on first `tokenpak serve`.

    Logic:
    - If already shown before (sentinel in config), skip.
    - If metrics already enabled/disabled in config, skip.
    - Print prompt, wait up to 5s for a 'y'. Default is NO (opt-out).
    - Write decision to config so we never prompt again.
    """
    try:
        from tokenpak.agent.config import _load, set_config

        cfg = _load()
        if cfg.get(_FIRST_RUN_SENTINEL):
            return  # already shown
        if "metrics.enabled" in cfg:
            return  # user already decided

        sys.stdout.write(
            "\nTokenPak ships anonymous usage metrics to help us improve.\n"
            "What's sent: install_id (random UUID), version, OS, Python version,\n"
            "  request count, model names. No prompts, no content, no API keys.\n"
            "Disable later: tokenpak metrics off\n"
            "Schema:       docs/telemetry.md  /  tokenpak.ai/metrics\n\n"
            "Enable metrics? [y/N] (5s timeout, default N): "
        )
        sys.stdout.flush()

        enabled = False
        try:
            # Non-blocking 5s read
            ready, _, _ = select.select([sys.stdin], [], [], 5.0)
            if ready:
                ans = sys.stdin.readline().strip().lower()
                enabled = ans in ("y", "yes")
        except (OSError, ValueError):
            pass  # not a TTY or other error — default to OFF

        set_config("metrics.enabled", enabled)
        set_config(_FIRST_RUN_SENTINEL, True)

        if enabled:
            print("✔ Metrics enabled. Thank you!")
        else:
            print("○ Metrics disabled. Enable anytime with: tokenpak metrics on")
        print()
    except Exception:
        pass  # never break startup


def _default_workers() -> int:
    """Return default worker count: max(1, cpu_count // 2)."""
    cpu = os.cpu_count() or 1
    return max(1, cpu // 2)


def run_serve_cmd(args) -> None:
    """Start the TokenPak ingest API server."""
    try:
        import uvicorn
    except ImportError:
        print("✖ uvicorn is required: pip install uvicorn", file=sys.stderr)
        sys.exit(1)

    # First-run metrics opt-in prompt (one-time, defaults to OFF)
    _maybe_show_metrics_prompt()

    # Start install-level metrics heartbeat in daemon thread (no-op if disabled)
    try:
        from tokenpak.telemetry.install_reporter import schedule_install_heartbeat

        schedule_install_heartbeat()
    except Exception:
        pass  # telemetry must never block startup

    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    port = getattr(args, "port", 8766) or 8766
    workers = getattr(args, "workers", None)

    if workers is None:
        workers = _default_workers()

    if workers < 1:
        print("✖ --workers must be >= 1", file=sys.stderr)
        sys.exit(1)

    print(f"TokenPak Ingest API — http://{host}:{port}")
    print(f"  Workers:             {workers} (CPU cores: {os.cpu_count() or '?'})")
    print("  POST /ingest         single entry")
    print("  POST /ingest/batch   batch entries")
    print("  GET  /health         health check")
    print()

    if workers == 1:
        # Single-worker: use in-process app object (compatible with tests/hot-reload)
        try:
            from tokenpak.agent.ingest.api import create_ingest_app
        except ImportError as e:
            print(f"✖ Failed to load ingest API: {e}", file=sys.stderr)
            sys.exit(1)

        app = create_ingest_app()
        uvicorn.run(app, host=host, port=port)

    else:
        # Multi-worker: pass import string + factory=True so uvicorn forks workers
        # that each call create_ingest_app() in their own process.
        #
        # Crash recovery: uvicorn's multiprocess supervisor (UvicornWorker) restarts
        # any worker that exits unexpectedly, up to the configured limit.
        #
        # Graceful shutdown: SIGTERM/SIGINT propagated to all workers; each drains
        # active requests then exits cleanly.
        #
        # SQLite telemetry: WAL mode allows concurrent readers + independent writers
        # from separate processes without corruption.
        print("  Mode: multi-process (workers restart on crash, graceful shutdown)")
        print()
        uvicorn.run(
            _APP_FACTORY_IMPORT,
            host=host,
            port=port,
            workers=workers,
            factory=True,
        )
