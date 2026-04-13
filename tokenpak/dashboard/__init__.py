"""TokenPak Metrics Dashboard - web UI for observability."""

import os  # noqa: F401 — reserved for dashboard file path expansion
from pathlib import Path

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


# Python API exports (from agent/dashboard/)
try:
    from .export_api import ExportAPI
    from .export_csv import CSVExporter, ExportDataType, ExportFormat
    from .session_filter import SessionFilter
    __all__ = ['get_dashboard_files', 'serve_dashboard_file', 'ExportAPI', 'CSVExporter', 'ExportDataType', 'ExportFormat', 'SessionFilter', 'account_dashboard', 'app', 'export_api', 'export_csv', 'session_filter']
except ImportError:
    __all__ = ["get_dashboard_files", "serve_dashboard_file", 'account_dashboard', 'app', 'export_api', 'export_csv', 'session_filter']
