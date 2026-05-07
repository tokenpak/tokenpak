"""
tests/proxy/conftest.py

Shared fixtures for proxy-level regression tests.

Provides:
  - stub_upstream: a lightweight HTTP server that replays canned SSE or JSON
    responses based on the request body's `stream` field. No real Anthropic API
    calls are made.
  - proxy_handler_class: returns the ProxyHandler class from proxy.py with a
    patched upstream URL pointing at the stub.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Paths to canned fixture responses
# ---------------------------------------------------------------------------
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

_SSE_BODY = (_FIXTURES_DIR / "sse_response_message_delta.txt").read_bytes()
_JSON_BODY = (_FIXTURES_DIR / "json_response_messages.json").read_bytes()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Return an available ephemeral TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Stub upstream server
# ---------------------------------------------------------------------------

class _StubUpstreamHandler(BaseHTTPRequestHandler):
    """
    Minimal Anthropic-shaped upstream stub.

    Behaviour:
      - POST /v1/messages
          * If body contains `"stream": true`  → SSE response (text/event-stream)
          * Otherwise                           → JSON response (application/json)
      - GET  /health → 200 OK
      - Anything else → 404

    Request count is tracked in self.server.request_count so tests can assert
    how many upstream calls were made.
    """

    def log_message(self, fmt: str, *args: object) -> None:  # silence test output
        pass

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""

        self.server.request_count += 1  # type: ignore[attr-defined]

        is_streaming = False
        try:
            parsed = json.loads(raw)
            is_streaming = bool(parsed.get("stream"))
        except Exception:
            pass

        if is_streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Content-Length", str(len(_SSE_BODY)))
            self.end_headers()
            self.wfile.write(_SSE_BODY)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_JSON_BODY)))
            self.end_headers()
            self.wfile.write(_JSON_BODY)


class _CountingHTTPServer(HTTPServer):
    """HTTPServer with a request_count attribute for test assertions."""
    request_count: int = 0


@pytest.fixture()
def stub_upstream() -> Generator[_CountingHTTPServer, None, None]:
    """
    Spin up the stub upstream on a free port, yield it, then shut it down.

    Usage in tests:
        def test_foo(stub_upstream):
            url = f"http://127.0.0.1:{stub_upstream.server_port}"
            # point proxy at this URL
    """
    port = _free_port()
    server = _CountingHTTPServer(("127.0.0.1", port), _StubUpstreamHandler)
    server.server_port = port  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()
    t.join(timeout=2)
