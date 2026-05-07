"""
tests/proxy/test_tui_savings_tape.py

CCI-14: Real-time TUI savings tape (chat footer hook).

Six cases covering all acceptance criteria:
  A — Tape appears in SSE stream for TUI profile (streaming, CHAT_FOOTER_ENABLED=True)
  B — Tape suppressed when savings data is zero (formatter returns None)
  C — Tape format matches the spec (unit test of _format_tui_savings_tape)
  D — Tape does not appear for non-TUI profiles (TUI_SAVINGS_TAPE_ENABLED=False)
  E — Graceful degradation: shows "tokenpak: active" when formatter raises exception
  F — TOKENPAK_CHAT_FOOTER=false (CHAT_FOOTER_ENABLED=False) disables the tape

Notes:
  - Real proxy (ForwardProxyHandler) + stub upstream, no Anthropic API calls.
  - Module-level flags are patched per test; SESSION reset between tests.
  - _format_tui_savings_tape is patched for injection tests (A, B, D, E, F) to avoid
    depending on real compression being active in the test environment.
  - TestCaseC tests the formatter directly as a unit test.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.runtime", reason="module not available in current build")
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
# Bootstrap: load the monolith proxy.py via importlib (no sys.path mutation)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

os.environ.setdefault("ANTHROPIC_API_KEY", "test-sk-cci14-dummy-not-real")
os.environ.setdefault("TOKENPAK_VAULT_INDEX", "0")

import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("proxy", _PROJECT_ROOT / "proxy.py")
_proxy = _ilu.module_from_spec(_spec)
sys.modules.setdefault("proxy", _proxy)
_spec.loader.exec_module(_proxy)

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SSE_BODY = (_FIXTURES / "sse_response_with_events.txt").read_bytes()
_JSON_BODY = (_FIXTURES / "json_response_messages.json").read_bytes()

# Known tape string returned by the patched formatter in injection tests
_TAPE_FIXED = "tokenpak: 4,500 \u2192 2,350 tokens (47.8% saved, $0.31), cache 81%, +4 vault"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_body(*, stream: bool = False, content: str = "hello CCI-14 test") -> bytes:
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


# ---------------------------------------------------------------------------
# Proxy + stub fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def proxy_and_stub():
    """Start stub upstream + real tokenpak proxy. Yields (proxy_port, stub_port)."""
    stub_port = _free_port()
    proxy_port = _free_port()

    stub = HTTPServer(("127.0.0.1", stub_port), _StubHandler)
    stub_t = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_t.start()

    # Reset session state
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
# Send helper
# ---------------------------------------------------------------------------

def _send_raw(
    proxy_port: int,
    stub_port: int,
    body: bytes,
    extra_headers: dict | None = None,
):
    """Send request through proxy. Returns (status, headers_dict, body_bytes)."""
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
# Case A — TUI profile (streaming): tape appears in SSE stream
# ===========================================================================

class TestCaseA_TapeAppearsForTuiProfile:
    """TUI mode (streaming): savings tape injected into SSE stream when enabled and savings exist."""

    def test_tape_present_in_sse_stream(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)
        # Patch _format_tui_savings_tape to return the fixed tape string so the test
        # doesn't depend on compression being active in the test environment.
        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", True),
            patch.object(_proxy, "TUI_SAVINGS_TAPE_ENABLED", True),
            patch.object(_proxy, "_format_tui_savings_tape", return_value=_TAPE_FIXED),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        # The tape appears as text in a content_block_delta event before message_stop.
        # "tokenpak:" is the unique CCI-14 prefix; "→" becomes \u2192 in JSON encoding.
        assert (
            b"tokenpak:" in data
            or b"\\u2192" in data
        ), (
            f"Expected CCI-14 savings tape ('tokenpak:' prefix) in SSE stream. "
            f"Stream tail: {data[-500:]!r}"
        )


# ===========================================================================
# Case B — Tape suppressed when savings are zero
# ===========================================================================

class TestCaseB_TapeSuppressedWhenZeroSavings:
    """Tape must not appear when _format_tui_savings_tape returns None (zero savings)."""

    def test_tape_absent_when_formatter_returns_none(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)
        # Patch formatter to return None — simulates zero-savings case
        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", True),
            patch.object(_proxy, "TUI_SAVINGS_TAPE_ENABLED", True),
            patch.object(_proxy, "_format_tui_savings_tape", return_value=None),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        assert b"tokenpak:" not in data, (
            "Savings tape must be suppressed when formatter returns None (zero savings). "
            f"Stream tail: {data[-300:]!r}"
        )
        # The message_stop event must still be forwarded (stream integrity check)
        assert b"message_stop" in data, (
            "message_stop event must still be present even when footer is suppressed"
        )


# ===========================================================================
# Case C — Format matches spec (unit test of _format_tui_savings_tape)
# ===========================================================================

class TestCaseC_FormatMatchesSpec:
    """Unit-test _format_tui_savings_tape output against the CCI-14 spec format."""

    def test_format_with_cache_and_vault(self):
        """Full tape: tokens + pct + usd + cache% + vault count."""
        tape = _proxy._format_tui_savings_tape(
            input_tokens=4500,
            sent_input_tokens=2350,
            cache_read_tokens=1903,  # ~81% of 2350
            vault_blocks=4,
            model="claude-sonnet-4-6",
            target_url="https://api.anthropic.com/v1/messages",
        )
        assert tape is not None, "_format_tui_savings_tape must return a string for non-zero savings"
        # Prefix
        assert tape.startswith("tokenpak:"), f"Tape must start with 'tokenpak:'. Got: {tape!r}"
        # Token counts with thousands separators
        assert "4,500" in tape, f"input_tokens must use thousands separator. Got: {tape!r}"
        assert "2,350" in tape, f"sent_input_tokens must use thousands separator. Got: {tape!r}"
        # Arrow (Unicode or literal)
        assert "\u2192" in tape or "->" in tape, f"Tape must contain arrow. Got: {tape!r}"
        # "tokens" label
        assert "tokens" in tape, f"Tape must contain 'tokens'. Got: {tape!r}"
        # Percentage with 1 decimal (e.g. "47.8% saved")
        assert "% saved" in tape, f"Tape must contain '% saved'. Got: {tape!r}"
        pct_end = tape.index("% saved")
        # Find the digit block before "% saved" — should contain a "."
        pct_segment = tape[max(0, pct_end - 6):pct_end]
        assert "." in pct_segment, (
            f"Percentage must have 1 decimal place. Segment before '% saved': {pct_segment!r}"
        )
        # Dollar amount with 2 decimals
        assert "$" in tape, f"Tape must contain '$'. Got: {tape!r}"
        dollar_idx = tape.index("$")
        dollar_segment = tape[dollar_idx:dollar_idx + 6]
        assert "." in dollar_segment, f"USD must have 2 decimal places. Segment: {dollar_segment!r}"
        # Cache percentage
        assert "cache" in tape, f"Tape must contain 'cache'. Got: {tape!r}"
        cache_idx = tape.index("cache")
        assert "%" in tape[cache_idx:cache_idx + 10], (
            f"cache section must have '%'. Got: {tape[cache_idx:cache_idx + 10]!r}"
        )
        # Vault
        assert "+4 vault" in tape, f"Tape must contain '+4 vault'. Got: {tape!r}"
        # Single line — no newlines
        assert "\n" not in tape, f"Tape must be single-line. Got: {tape!r}"
        # Length constraint: ≤120 chars
        assert len(tape) <= 120, f"Tape must be ≤120 chars. Length={len(tape)}. Got: {tape!r}"

    def test_format_without_cache_or_vault(self):
        """Minimal tape: compression savings only (no cache, no vault blocks)."""
        tape = _proxy._format_tui_savings_tape(
            input_tokens=3000,
            sent_input_tokens=1500,
            cache_read_tokens=0,
            vault_blocks=0,
            model="claude-sonnet-4-6",
            target_url="https://api.anthropic.com/v1/messages",
        )
        assert tape is not None, "Must return tape when compression savings exist"
        assert "cache" not in tape, f"No 'cache' section when cache_read_tokens=0. Got: {tape!r}"
        assert "vault" not in tape, f"No 'vault' section when vault_blocks=0. Got: {tape!r}"
        assert tape.startswith("tokenpak:")

    def test_returns_none_when_all_zero(self):
        """Returns None when there are no savings at all (suppress the footer)."""
        tape = _proxy._format_tui_savings_tape(
            input_tokens=1000,
            sent_input_tokens=1000,  # no compression
            cache_read_tokens=0,      # no cache
            vault_blocks=0,           # no vault
            model="claude-sonnet-4-6",
            target_url="https://api.anthropic.com/v1/messages",
        )
        assert tape is None, (
            "_format_tui_savings_tape must return None when all savings are zero. "
            f"Got: {tape!r}"
        )


# ===========================================================================
# Case D — Non-TUI profile: tape does not appear
# ===========================================================================

class TestCaseD_TapeAbsentForNonTuiProfile:
    """Tape must not appear when TUI_SAVINGS_TAPE_ENABLED=False (non-TUI profiles)."""

    def test_tape_absent_when_flag_disabled(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)
        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", True),
            patch.object(_proxy, "TUI_SAVINGS_TAPE_ENABLED", False),
            # Formatter would return a tape if called — verify the gate blocks it
            patch.object(_proxy, "_format_tui_savings_tape", return_value=_TAPE_FIXED),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        assert b"tokenpak:" not in data, (
            "Savings tape must NOT appear when TUI_SAVINGS_TAPE_ENABLED=False. "
            f"Stream tail: {data[-300:]!r}"
        )


# ===========================================================================
# Case E — Graceful degradation: "tokenpak: active" fallback on exception
# ===========================================================================


class TestCaseE_GracefulDegradationFallback:
    """When formatter raises an exception, shows 'tokenpak: active' fallback."""

    def test_active_fallback_on_formatter_exception(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated savings data unavailable")

        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", True),
            patch.object(_proxy, "TUI_SAVINGS_TAPE_ENABLED", True),
            patch.object(_proxy, "_format_tui_savings_tape", side_effect=_raise),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        # Falls back to "tokenpak: active" — JSON-encoded inside the SSE event
        assert b"tokenpak: active" in data or b"tokenpak:\\u0020active" in data, (
            "Expected 'tokenpak: active' fallback when formatter raises exception. "
            f"SSE tail: {data[-400:]!r}"
        )


# ===========================================================================
# Case F — CHAT_FOOTER_ENABLED=False disables the tape
# ===========================================================================


class TestCaseF_ChatFooterFlagDisables:
    """TOKENPAK_CHAT_FOOTER=false (CHAT_FOOTER_ENABLED=False) disables the savings tape."""

    def test_tape_absent_when_chat_footer_disabled(self, proxy_and_stub):
        proxy_port, stub_port = proxy_and_stub
        body = _make_body(stream=True)

        with (
            patch.object(_proxy, "CHAT_FOOTER_ENABLED", False),  # TOKENPAK_CHAT_FOOTER=false
            patch.object(_proxy, "TUI_SAVINGS_TAPE_ENABLED", True),
            patch.object(_proxy, "_format_tui_savings_tape", return_value=_TAPE_FIXED),
        ):
            _status, _hdrs, data = _send_raw(proxy_port, stub_port, body)

        assert b"tokenpak:" not in data, (
            "Expected NO savings tape when CHAT_FOOTER_ENABLED=False. "
            f"SSE tail: {data[-400:]!r}"
        )
