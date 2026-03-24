"""
TokenPak Live Monitor Dashboard Server.
Serves the static HTML dashboard and a /api proxy endpoint.
"""

import glob
import json
import os
import pathlib
import socketserver
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

DEFAULT_PORT = 8767
PROXY_URL = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
LOGS_DIR = os.path.expanduser("~/.tokenpak/logs")
DASHBOARD_HTML = pathlib.Path(__file__).parent / "dashboard.html"


def _fetch_stats() -> dict:
    """Fetch live stats from the running proxy."""
    try:
        with urllib.request.urlopen(f"{PROXY_URL}/stats", timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def _fetch_errors(limit: int = 100, model_filter: Optional[str] = None) -> list:
    """Read recent errors from ~/.tokenpak/logs/errors-*.jsonl"""
    entries = []
    pattern = os.path.join(LOGS_DIR, "errors-*.jsonl")
    files = sorted(glob.glob(pattern), reverse=True)[:3]  # last 3 days
    for fpath in files:
        try:
            with open(fpath) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if model_filter:
                            ctx = entry.get("context", {})
                            if ctx.get("model") != model_filter:
                                continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass
    # newest first, capped
    entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return entries[:limit]


class MonitorHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for the monitor dashboard."""

    def log_message(self, fmt, *args):
        pass  # suppress server logs

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_dashboard()
        elif self.path == "/api/stats":
            self._api_stats()
        elif self.path.startswith("/api/errors"):
            self._api_errors()
        else:
            self.send_error(404, "Not Found")

    def _serve_dashboard(self):
        if DASHBOARD_HTML.exists():
            content = DASHBOARD_HTML.read_bytes()
        else:
            content = b"<h1>Dashboard not found</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _api_stats(self):
        data = _fetch_stats()
        self._json_response(data)

    def _api_errors(self):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        limit = int(qs.get("limit", [100])[0])
        model = qs.get("model", [None])[0]
        entries = _fetch_errors(limit=limit, model_filter=model)
        self._json_response({"errors": entries, "count": len(entries)})

    def _json_response(self, data: dict, status: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run(port: int = DEFAULT_PORT):
    """Start the monitor server (blocking)."""
    server = ThreadedHTTPServer(("0.0.0.0", port), MonitorHandler)
    print(f"TokenPak Monitor → http://localhost:{port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
