"""TokenPak Metrics Dashboard — local web UI for observability.

Level-5 read-only entrypoint per Architecture §1. Reads from the
telemetry store (`~/.tokenpak/telemetry.db`); never writes to it
(verified by §7.1 authoritative-store rule; importlinter contract
C2 enforces).

Per Architecture §2.4, any dispatch-style call (e.g. a test request
from a diagnostic view) goes through tokenpak.proxy.client — made
available here for that narrow purpose.
"""

import os
from pathlib import Path

# §2.4 availability import; no current call sites.
from tokenpak.proxy import client as proxy_client  # noqa: F401

DASHBOARD_DIR = Path(__file__).parent


def get_dashboard_files():
    """Return paths to dashboard files."""
    return {
        "index.html": DASHBOARD_DIR / "index.html",
        "metrics.js": DASHBOARD_DIR / "metrics.js",
        "charts.js": DASHBOARD_DIR / "charts.js",
        "styles.css": DASHBOARD_DIR / "styles.css",
    }


async def serve_dashboard_file(path: str) -> tuple[str, str] | None:
    """Serve a dashboard file. Returns (content, mime_type) or None."""
    files = get_dashboard_files()

    # Default to index.html
    if path in ("", "/"):
        path = "index.html"

    # Remove leading slash
    if path.startswith("/"):
        path = path[1:]

    if path not in files:
        return None

    filepath = files[path]
    if not filepath.exists():
        return None

    content = filepath.read_text()

    mime_types = {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
    }

    ext = filepath.suffix
    mime_type = mime_types.get(ext, "text/plain")

    return content, mime_type
