"""
tests/test_proxy_error_paths.py

Integration regression tests for BUG-002 error paths (proxy_v4.py).

Commit b2e95eb fixed three critical uncaught exception paths:

  C-1 — Removed unconditional open('/tmp/proxy_debug.log') that crashed requests
          when /tmp was full or read-only.
  C-2 — Wrapped vault injection + compaction in try/except that falls back to
          original request body so the request still forwards.
  C-3 — Wrapped Monitor.log() SQLite calls in try/except so database errors
          never propagate to crash the request.

Each test verifies the proxy returns a valid HTTP 200 response (not crash/hang)
under the corresponding failure condition.
"""
from __future__ import annotations

import builtins
import importlib.util
import json
import socket
import sqlite3
import sys
import threading
import time
import urllib.request as urllib_request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Return an available TCP port on 127.0.0.1."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chat_payload(repeat: int = 400) -> bytes:
    """
    Build a minimal /v1/messages payload.
    repeat=400 → ~7 200 chars → ~1 800 tokens (enough to trigger the pipeline;
    compaction threshold is 4 500 tokens so set repeat=1200 for compaction).
    """
    content = "Please explain the history and future of artificial intelligence. " * repeat
    return json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": content}],
    }).encode()


def _mock_https_conn_factory(body: bytes | None = None) -> MagicMock:
    """
    Return a mock class for http.client.HTTPSConnection that yields a
    deterministic 200 application/json response without touching the network.
    """
    if body is None:
        body = json.dumps({
            "id": "msg_mock_001",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Mock upstream response"}],
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode()

    def _getheader(name: str, default: str = "") -> str:
        table = {
            "content-type": "application/json",
            "content-encoding": "",
        }
        return table.get(name.lower(), default)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.getheaders.return_value = [("Content-Type", "application/json")]
    mock_resp.getheader.side_effect = _getheader
    mock_resp.read.return_value = body

    mock_conn_instance = MagicMock()
    mock_conn_instance.getresponse.return_value = mock_resp
    mock_conn_instance.close.return_value = None

    return MagicMock(return_value=mock_conn_instance)


# ---------------------------------------------------------------------------
# Import proxy_v4 as a standalone module (it lives outside the package)
# ---------------------------------------------------------------------------

_PROXY_V4_PATH = Path(__file__).parent.parent / "proxy_v4.py"


@pytest.fixture(scope="module")
def pv4():
    """
    Load proxy_v4.py as a Python module once for the entire test session.
    The background ollama-health daemon thread is harmless (it's a daemon and
    catches all connection errors internally).
    """
    mod_name = "_test_proxy_v4"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, _PROXY_V4_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Proxy server — started once per module on a free port
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def proxy_server(pv4):
    """
    Start proxy_v4's ThreadedHTTPServer on a free port.
    Yields the port number; shuts down after the module tests finish.
    """
    port = _free_port()
    server = pv4.ThreadedHTTPServer(("127.0.0.1", port), pv4.ForwardProxyHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.15)   # let the server thread bind and enter serve_forever
    yield port
    server.shutdown()


# ---------------------------------------------------------------------------
# HTTP helper — sends a /v1/messages POST through the proxy
# ---------------------------------------------------------------------------

def _post_via_proxy(port: int, payload: bytes, timeout: int = 10) -> tuple[int, bytes]:
    """
    POST `payload` to http://127.0.0.1:{port}/v1/messages (reverse-proxy endpoint).

    proxy_v4's do_POST routes /v1/... through _reverse_proxy() which targets
    https://api.anthropic.com — an INTERCEPT_HOST — so the full pipeline runs.
    """
    req = urllib_request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": "sk-test-key",
            "anthropic-version": "2023-06-01",
            "Content-Length": str(len(payload)),
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib_request.HTTPError as exc:
        return exc.code, exc.read()


# ---------------------------------------------------------------------------
# C-1 — /tmp unwritable: proxy must NOT open /tmp/proxy_debug.log
# ---------------------------------------------------------------------------

class TestC1TmpUnwritable:
    """
    BUG-002 C-1 regression.

    Original bug: `open('/tmp/proxy_debug.log', ...)` was called unconditionally
    at the top of _proxy_to() — before any try/except — so it crashed every
    request when /tmp was full or mounted read-only.

    Fix: that line was removed entirely.  These tests verify:
      (a) The proxy completes requests even when /tmp is blocked.
      (b) proxy_v4 never attempts to open /tmp/proxy_debug.log at all.
    """

    def test_proxy_completes_with_tmp_permission_blocked(self, pv4, proxy_server):
        """Proxy returns 200 even when any /tmp/ open() would raise PermissionError."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()
        real_open = builtins.open

        def _blocked_open(path, *args, **kwargs):
            if "/tmp/" in str(path):
                raise PermissionError(f"[Errno 13] Permission denied: '{path}'")
            return real_open(path, *args, **kwargs)

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch("builtins.open", side_effect=_blocked_open):
                status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200, f"Expected 200, got {status}: {body[:200]}"

    def test_no_debug_log_open_during_pipeline(self, pv4, proxy_server):
        """Verify proxy_v4 never opens /tmp/proxy_debug.log during a request."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()
        debug_opens: list[str] = []
        real_open = builtins.open

        def _tracking_open(path, *args, **kwargs):
            path_str = str(path)
            if "proxy_debug" in path_str:
                debug_opens.append(path_str)
            return real_open(path, *args, **kwargs)

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch("builtins.open", side_effect=_tracking_open):
                status, _ = _post_via_proxy(proxy_server, payload)

        assert status == 200
        assert not debug_opens, (
            f"proxy_v4 opened debug file(s) that should not exist: {debug_opens}"
        )


# ---------------------------------------------------------------------------
# C-2 — Vault injection raises: proxy falls back to original body
# ---------------------------------------------------------------------------

class TestC2VaultInjectionFails:
    """
    BUG-002 C-2 regression.

    Original bug: if inject_vault_context() or compact_request_body() raised,
    the exception propagated uncaught and returned a 502 / crashed the handler.

    Fix: the pre-pipeline block is wrapped in try/except; on any exception the
    handler restores _original_body and continues forwarding.

    These tests inject failures into both pipeline functions and confirm the
    proxy still returns a 200 with a valid JSON body.
    """

    def test_proxy_200_when_inject_vault_raises_runtime_error(self, pv4, proxy_server):
        """inject_vault_context() raises RuntimeError → proxy still returns 200."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4, "inject_vault_context",
                side_effect=RuntimeError("simulated corrupt vault index"),
            ):
                status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200, f"Expected 200, got {status}: {body[:200]}"

    def test_proxy_response_valid_json_after_injection_failure(self, pv4, proxy_server):
        """inject_vault_context() raises ValueError → response body is valid JSON."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4, "inject_vault_context",
                side_effect=ValueError("bad index block format"),
            ):
                status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200
        data = json.loads(body)
        # The mock upstream response has these fields
        assert "id" in data or "content" in data, (
            f"Response JSON missing expected fields: {list(data.keys())}"
        )

    def test_proxy_200_when_compact_request_body_raises(self, pv4, proxy_server):
        """compact_request_body() raises MemoryError → proxy falls back, returns 200."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4, "compact_request_body",
                side_effect=MemoryError("simulated OOM during compaction"),
            ):
                status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200, f"Expected 200, got {status}: {body[:200]}"

    def test_proxy_forwards_original_body_when_pipeline_fails(self, pv4, proxy_server):
        """
        When the pipeline raises, the proxy must forward the *original* body
        (not an empty body or a partial mutation). Verify the upstream receives
        a parseable JSON payload by checking that our mock was called with a body.
        """
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()
        received_bodies: list[bytes] = []

        original_request = mock_conn.return_value.request

        def _capture_body(method, path, body=None, headers=None):
            if body is not None:
                received_bodies.append(body)
            return original_request(method, path, body=body, headers=headers)

        mock_conn.return_value.request = _capture_body

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4, "inject_vault_context",
                side_effect=RuntimeError("index unavailable"),
            ):
                status, _ = _post_via_proxy(proxy_server, payload)

        assert status == 200
        assert received_bodies, "Proxy did not forward any body to upstream"
        # The forwarded body must be valid JSON matching original structure
        fwd_data = json.loads(received_bodies[-1])
        assert fwd_data.get("model") == "claude-sonnet-4-6"
        assert fwd_data["messages"][0]["role"] == "user"


# ---------------------------------------------------------------------------
# C-3 — Monitor.log() raises SQLite error: request still completes
# ---------------------------------------------------------------------------

class TestC3MonitorLogFails:
    """
    BUG-002 C-3 regression.

    Original bug: Monitor.log() calls sqlite3.connect() / conn.execute() —
    if those raised (disk full, database locked, I/O error), the exception
    propagated past the response-write and the handler returned a 502.

    Fix: Monitor.log() is now wrapped in try/except; errors are printed as
    warnings but never re-raised.

    These tests mock Monitor.log() to raise SQLite errors and confirm the
    proxy returns 200 and continues updating SESSION counters.
    """

    def test_proxy_200_when_monitor_log_raises_operational_error(self, pv4, proxy_server):
        """Monitor.log() raises OperationalError('disk I/O error') → proxy returns 200."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4.MONITOR, "log",
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ):
                status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200, f"Expected 200, got {status}: {body[:200]}"

    def test_proxy_200_when_monitor_log_raises_database_locked(self, pv4, proxy_server):
        """Monitor.log() raises DatabaseError('database is locked') → proxy returns 200."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4.MONITOR, "log",
                side_effect=sqlite3.DatabaseError("database is locked"),
            ):
                status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200, f"Expected 200, got {status}: {body[:200]}"

    def test_response_body_valid_json_despite_monitor_failure(self, pv4, proxy_server):
        """Response body is valid JSON even when Monitor.log() fails."""
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4.MONITOR, "log",
                side_effect=sqlite3.OperationalError("no space left on device"),
            ):
                status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200
        data = json.loads(body)
        assert "id" in data, f"Response JSON missing 'id': {list(data.keys())}"

    def test_session_requests_increments_despite_monitor_failure(self, pv4, proxy_server):
        """
        SESSION['requests'] must still increment even when Monitor.log() raises.
        In proxy_v4, the SESSION update code runs AFTER the Monitor.log try/except,
        so it is unaffected by SQLite failures.
        """
        before = pv4.SESSION["requests"]
        payload = _chat_payload(400)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            with patch.object(
                pv4.MONITOR, "log",
                side_effect=sqlite3.OperationalError("disk full"),
            ):
                status, _ = _post_via_proxy(proxy_server, payload)

        assert status == 200
        assert pv4.SESSION["requests"] >= before + 1, (
            f"SESSION['requests'] should have incremented from {before}; "
            f"got {pv4.SESSION['requests']}"
        )


# ---------------------------------------------------------------------------
# Normal path — end-to-end with all features enabled
# ---------------------------------------------------------------------------

class TestNormalPath:
    """
    Verify the proxy works end-to-end with all features in their default state.
    Only the outbound HTTPS connection is mocked — the full pipeline runs.
    """

    def test_normal_request_returns_200(self, pv4, proxy_server):
        """Basic end-to-end: proxy returns 200 with no injected failures."""
        payload = _chat_payload(200)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            status, body = _post_via_proxy(proxy_server, payload)

        assert status == 200, f"Expected 200, got {status}: {body[:200]}"

    def test_normal_response_is_valid_json(self, pv4, proxy_server):
        """Response body is valid JSON with expected Claude response fields."""
        payload = _chat_payload(200)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            _, body = _post_via_proxy(proxy_server, payload)

        data = json.loads(body)
        assert "id" in data
        assert "model" in data

    def test_normal_session_counter_increments(self, pv4, proxy_server):
        """SESSION['requests'] increments on a normal successful request."""
        before = pv4.SESSION["requests"]
        payload = _chat_payload(200)
        mock_conn = _mock_https_conn_factory()

        with patch("http.client.HTTPSConnection", mock_conn):
            status, _ = _post_via_proxy(proxy_server, payload)

        assert status == 200
        assert pv4.SESSION["requests"] > before
