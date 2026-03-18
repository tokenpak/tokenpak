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
import sys

logger = logging.getLogger(__name__)

# Import string used by uvicorn when workers > 1.
# Uvicorn calls create_ingest_app() in each worker process.
_APP_FACTORY_IMPORT = "tokenpak.agent.ingest.api:create_ingest_app"


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
        print(f"  Mode: multi-process (workers restart on crash, graceful shutdown)")
        print()
        uvicorn.run(
            _APP_FACTORY_IMPORT,
            host=host,
            port=port,
            workers=workers,
            factory=True,
        )
