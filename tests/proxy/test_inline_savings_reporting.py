"""
tests/proxy/test_inline_savings_reporting.py

CCI-10: Inline savings reporting — TUI footer, IDE header, SSE event.

Six cases (3 modes × 2: feature ON / feature OFF):
  A — TUI  (streaming)     with CHAT_FOOTER_ENABLED=True  → footer injected into SSE stream
  B — TUI  (streaming)     with CHAT_FOOTER_ENABLED=False → NO footer text injected
  C — IDE  (non-streaming) with INLINE_SAVINGS_HEADER_ENABLED=True  → X-TokenPak-Savings header
  D — IDE  (non-streaming) with INLINE_SAVINGS_HEADER_ENABLED=False → no savings header
  E — SSE  (streaming)     with INLINE_SAVINGS_ENABLED=True  → tokenpak.savings event emitted
  F — SSE  (streaming)     with INLINE_SAVINGS_ENABLED=False → NO savings event

Notes:
  - Real proxy (ForwardProxyHandler) + stub upstream, no Anthropic API calls.
  - Session state is reset between tests via SESSION.update().
  - proxy.py is imported once at module level; feature flags are patched per test.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap — import proxy.py as module
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("ANTHROPIC_API_KEY", "test-sk-cci10-dummy-not-real")
os.environ.setdefault("TOKENPAK_VAULT_INDEX", "0")

import proxy as _proxy  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SSE_BODY = (_FIXTURES / "sse_response_with_events.txt").read_bytes()
_JSON_BODY = (_FIXTURES / "json_response_messages.json").read_bytes()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_body(*, stream: bool = False, content: str = "hello CCI-10 test") -> bytes:
    return json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 32,
        "stream": stream,
        "messages": [{"role": "user", "content": content}],
    }).encode()


# ---------------------------------------------------------------------------
# Stub upstream
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        is_streaming = False
        try:
            is_streaming = bool(json.loads(raw).get("stream"))
        except Exception:
            pass
        if is_streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(_SSE_BODY)))
            self.end_headers()
            self.wfile.write(_SSE_BODY)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_JSON_BODY)))
            self.end_headers()
            self.wfile.write(_JSON_BODY)


class _StubServer(HTTPServer):
    pass


# ---------------------------------------------------------------------------
# Proxy + stub fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def proxy_and_stub():
    """
    Start a stub upstream and a real tokenpak proxy.
    Yields (proxy_port, stub_port).
    Resets SESSION before each test.
    """
    stub_port = _free_port()
    proxy_port = _free_port()

    stub = _StubServer(("127.0.0.1", stub_port), _StubHandler)
    stub_t = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_t.start()

    # Reset SESSION counters
    _proxy.SESSION["input_tokens"] = 0
    _proxy.SESSION["sent_input_tokens"] = 0
    _proxy.SESSION["requests"] = 0
    _proxy.SESSION.pop("vault_blocks_injected", None)

    proxy_server = _proxy.ThreadedHTTPServer(
        ("127.0.0.1", proxy_port), _proxy.ForwardProxyHandler
    )
    proxy_t = threading.Thread(target=proxy_server.serve_forever, daemon=True)
    proxy_t.start()

    yield proxy_port, stub_port

    proxy_server.shutdown()
    proxy_t.join(timeout=2)
    stub.shutdown()
    stub_t.join(timeout=2)


# ---------------------------------------------------------------------------
# Low-level send helpers (capture both body and response headers)
# ---------------------------------------------------------------------------

def _send_raw(
    proxy_port: int,
    stub_port: int,
    body: bytes,
    extra_headers: dict | None = None,
):
    """
    Send a request through the proxy and return (status, headers_dict, body_bytes).
    headers_dict keys are lowercased.
    """
    target_url = f"http://127.0.0.1:{stub_port}/v1/messages"
    req = urllib.request.Request(
        target_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            **(extra_headers or {}),
        },
    )
    proxy_handler = urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy_port}"})
    opener = urllib.request.build_opener(proxy_handler)
    try:
        with opener.open(req, timeout=10) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            data = resp.read()
            return resp.status, headers, data
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in e.headers.items()}
        return e.code, headers, e.read()


# ===========================================================================
# Case A — TUI mode (streaming), CHAT_FOOTER_ENABLED=True → footer injected
# ===========================================================================

class TestCaseA_TuiFooterWithEnabled:
    """TUI mode: savings footer text injected into SSE stream when CHAT_FOOTER_ENABLED=True."""

    def test_footer_present_in_sse_stream(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)
        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", True),
            patch.object(_proxy, "INLINE_SAVINGS_ENABLED", False),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        # When CHAT_FOOTER_ENABLED=True, the proxy injects a content_block_delta SSE event
        # before message_stop carrying the savings-tape footer text (e.g. "N→N tok (-N%)").
        # The text is JSON-encoded so the "→" arrow appears as its Unicode escape \u2192.
        assert (
            b"tok (-" in data  # the footer always contains "N tok (-N%)"
            or b"\\u2192" in data  # "→" JSON-escaped form
        ), (
            "Expected savings footer text in SSE stream when CHAT_FOOTER_ENABLED=True. "
            f"SSE tail: {data[-400:]!r}"
        )


# ===========================================================================
# Case B — TUI mode (streaming), CHAT_FOOTER_ENABLED=False → NO footer
# ===========================================================================

class TestCaseB_TuiFooterWithDisabled:
    """TUI mode: no footer injected when CHAT_FOOTER_ENABLED=False (CLI/cron mode)."""

    def test_footer_absent_in_sse_stream(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)
        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", False),
            patch.object(_proxy, "INLINE_SAVINGS_ENABLED", False),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        # Without the flag, the proxy must NOT inject any footer delta.
        # The "tok (-" pattern is unique to the proxy-injected savings footer text.
        assert b"tok (-" not in data, (
            "Footer 'tok (-N%)' must NOT appear when CHAT_FOOTER_ENABLED=False"
        )


# ===========================================================================
# Case C — IDE mode (non-streaming), INLINE_SAVINGS_HEADER_ENABLED=True → header present
# ===========================================================================

class TestCaseC_IdeHeaderWithEnabled:
    """IDE mode: X-TokenPak-Savings header emitted when INLINE_SAVINGS_HEADER_ENABLED=True."""

    def test_savings_header_present(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=False)
        # Use IDE user-agent so profile detection sets active_profile=claude-code-ide
        ide_headers = {"User-Agent": "vscode/1.88.0 claude-code/2.1.96"}
        with patch.object(_proxy, "INLINE_SAVINGS_HEADER_ENABLED", True):
            status, headers, _data = _send_raw(
                proxy_port, stub_port, body, extra_headers=ide_headers
            )

        assert status == 200, f"Expected 200, got {status}"
        assert "x-tokenpak-savings" in headers, (
            "Expected X-TokenPak-Savings header when INLINE_SAVINGS_HEADER_ENABLED=True. "
            f"Got headers: {list(headers.keys())}"
        )
        savings_val = headers["x-tokenpak-savings"]
        assert savings_val.startswith("$"), (
            f"X-TokenPak-Savings must be formatted as '$X.XX', got {savings_val!r}"
        )
        assert "x-tokenpak-cache-hit" in headers, (
            "Expected X-TokenPak-Cache-Hit header alongside X-TokenPak-Savings"
        )


# ===========================================================================
# Case D — IDE mode (non-streaming), INLINE_SAVINGS_HEADER_ENABLED=False → no header
# ===========================================================================

class TestCaseD_IdeHeaderWithDisabled:
    """IDE mode: no X-TokenPak-Savings header when INLINE_SAVINGS_HEADER_ENABLED=False."""

    def test_savings_header_absent(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=False)
        with patch.object(_proxy, "INLINE_SAVINGS_HEADER_ENABLED", False):
            status, headers, _data = _send_raw(proxy_port, stub_port, body)

        assert status == 200, f"Expected 200, got {status}"
        assert "x-tokenpak-savings" not in headers, (
            "X-TokenPak-Savings must NOT be present when INLINE_SAVINGS_HEADER_ENABLED=False"
        )


# ===========================================================================
# Case E — SSE event mode (streaming), INLINE_SAVINGS_ENABLED=True → event emitted
# ===========================================================================

class TestCaseE_SseEventWithEnabled:
    """SSE event: tokenpak.savings event appears in stream when INLINE_SAVINGS_ENABLED=True."""

    def test_savings_sse_event_present(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)
        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", False),
            patch.object(_proxy, "INLINE_SAVINGS_ENABLED", True),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        assert b"event: tokenpak.savings" in data, (
            "Expected 'event: tokenpak.savings' in SSE stream when INLINE_SAVINGS_ENABLED=True. "
            f"SSE stream tail: {data[-500:]!r}"
        )

        # Extract savings event data payload and validate required fields
        stream_text = data.decode("utf-8", errors="replace")
        savings_idx = stream_text.find("event: tokenpak.savings")
        assert savings_idx >= 0
        data_line_start = stream_text.find("data:", savings_idx)
        assert data_line_start >= 0, "tokenpak.savings event has no data line"
        data_line_end = stream_text.find("\n", data_line_start)
        savings_json_str = stream_text[data_line_start + 5:data_line_end].strip()
        savings_data = json.loads(savings_json_str)

        assert "total_savings_usd" in savings_data, (
            f"savings data missing 'total_savings_usd': {savings_data}"
        )
        assert "compression_savings_usd" in savings_data, (
            f"savings data missing 'compression_savings_usd': {savings_data}"
        )
        assert "vault_blocks_injected" in savings_data, (
            f"savings data missing 'vault_blocks_injected': {savings_data}"
        )
        assert "input_tokens_raw" in savings_data, (
            f"savings data missing 'input_tokens_raw': {savings_data}"
        )


# ===========================================================================
# Case F — SSE event mode (streaming), INLINE_SAVINGS_ENABLED=False → no event
# ===========================================================================

class TestCaseF_SseEventWithDisabled:
    """SSE event: no tokenpak.savings event when INLINE_SAVINGS_ENABLED=False (CLI/cron)."""

    def test_savings_sse_event_absent(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)
        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", False),
            patch.object(_proxy, "INLINE_SAVINGS_ENABLED", False),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        assert b"event: tokenpak.savings" not in data, (
            "tokenpak.savings event must NOT appear when INLINE_SAVINGS_ENABLED=False"
        )
