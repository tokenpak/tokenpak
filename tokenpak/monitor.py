"""
tokenpak.monitor — Live Monitor Dashboard HTTP server (port 8767).

Serves:
  GET /                  → monitor_dashboard.html
  GET /api/stats         → proxy /stats from localhost:8766
  GET /api/errors        → read ~/.tokenpak/logs/errors-YYYY-MM-DD.jsonl

Usage:
  python -m tokenpak.monitor [--port 8767]
  tokenpak monitor [--port 8767]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen


PROXY_URL = "http://localhost:8766"
DASHBOARD_HTML = Path(__file__).parent / "monitor_dashboard.html"


def _fetch_proxy_stats() -> dict:
    """Fetch /stats from the proxy. Returns error dict if offline."""
    try:
        resp = urlopen(f"{PROXY_URL}/stats", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return {"error": "proxy offline"}


def _read_errors(date_str: str | None = None) -> list:
    """Read errors-YYYY-MM-DD.jsonl from ~/.tokenpak/logs/."""
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = Path.home() / ".tokenpak" / "logs" / f"errors-{date_str}.jsonl"
    errors = []
    if not log_path.exists():
        return errors
    try:
        for line in log_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    errors.append(json.loads(line))
                except json.JSONDecodeError:
                    errors.append({"raw": line})
    except Exception:
        pass
    return errors


class MonitorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress default access log noise

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._serve_dashboard()
        elif path == "/api/stats":
            self._serve_stats()
        elif path == "/api/errors":
            qs = parse_qs(parsed.query)
            date_param = qs.get("date", [None])[0]
            self._serve_errors(date_param)
        else:
            self._send_json({"error": "not found"}, status=404)

    def _serve_dashboard(self):
        if DASHBOARD_HTML.exists():
            html = DASHBOARD_HTML.read_bytes()
        else:
            html = b"<h1>monitor_dashboard.html not found</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _serve_stats(self):
        data = _fetch_proxy_stats()
        self._send_json(data)

    def _serve_errors(self, date_str: str | None):
        errors = _read_errors(date_str)
        self._send_json({"date": date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                         "errors": errors,
                         "count": len(errors)})

    def _send_json(self, data: dict | list, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def run(port: int = 8767):
    server = HTTPServer(("127.0.0.1", port), MonitorHandler)
    print(f"TokenPak Monitor  →  http://localhost:{port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(description="TokenPak Live Monitor Dashboard")
    parser.add_argument("--port", type=int, default=8767, help="Port to listen on (default: 8767)")
    args = parser.parse_args()
    run(args.port)


if __name__ == "__main__":
    main()
