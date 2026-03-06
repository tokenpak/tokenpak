"""tokenpak/agent/cli/commands/serve.py

Phase 5A: `tokenpak serve` command
====================================
Starts the ingest API server on the specified port.

Usage:
    tokenpak serve [--port PORT] [--host HOST]
"""
from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def run_serve_cmd(args) -> None:
    """Start the TokenPak ingest API server."""
    try:
        import uvicorn
    except ImportError:
        print("✖ uvicorn is required: pip install uvicorn", file=sys.stderr)
        sys.exit(1)

    try:
        from tokenpak.agent.ingest.api import create_ingest_app
    except ImportError as e:
        print(f"✖ Failed to load ingest API: {e}", file=sys.stderr)
        sys.exit(1)

    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    port = getattr(args, "port", 8765) or 8765

    print(f"TokenPak Ingest API — http://{host}:{port}")
    print(f"  POST /ingest         single entry")
    print(f"  POST /ingest/batch   batch entries")
    print(f"  GET  /health         health check")
    print()

    app = create_ingest_app()
    uvicorn.run(app, host=host, port=port)
