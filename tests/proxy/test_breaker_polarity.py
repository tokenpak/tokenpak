"""
Tests for circuit-breaker accounting polarity

The proxy previously recorded a breaker SUCCESS whenever the request path
completed without an exception — including upstream 5xx responses that
had exhausted their retries — so a provider 5xx storm never tripped the
breaker and /status stayed green. Meanwhile a raw BrokenPipeError from
writing the response to OUR client (the CLI vanished mid-response) was
recorded as a PROVIDER failure.

These tests pin the corrected accounting:

- classification helpers: 500/502/503/504/529 are provider failures;
  2xx/4xx (including 429, which feeds the separate rate-limit breaker)
  are not; raw client-socket errors are client disconnects, httpx
  transport errors are not
- end-to-end (threaded server + local stub upstream): a 503 that
  exhausts retries records a breaker failure, a 200 records a success,
  and a client that hangs up mid-response records NEITHER a provider
  failure nor a success
"""

from __future__ import annotations

import http.client
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

pytestmark = pytest.mark.needs_proxy

from tokenpak.proxy import server as proxy_server_module
from tokenpak.proxy.circuit_breaker import get_circuit_breaker_registry
from tokenpak.proxy.server import (
    ProxyServer,
    _cb_status_is_provider_failure,
    _is_client_disconnect_error,
)

# ---------------------------------------------------------------------------
# Unit tests — classification helpers
# ---------------------------------------------------------------------------


class TestStatusClassification:
    def test_retryable_5xx_are_provider_failures(self):
        for status in (500, 502, 503, 504, 529):
            assert _cb_status_is_provider_failure(status), status

    def test_success_and_client_errors_are_not_provider_failures(self):
        for status in (200, 201, 400, 401, 403, 404, 422):
            assert not _cb_status_is_provider_failure(status), status

    def test_429_is_excluded_from_provider_breaker(self):
        # 429 feeds the separate rate-limit circuit breaker (record_429);
        # it must not also count against the provider breaker.
        assert not _cb_status_is_provider_failure(429)

    def test_none_status_is_not_a_provider_failure(self):
        assert not _cb_status_is_provider_failure(None)


class TestClientDisconnectClassification:
    def test_raw_socket_errors_are_client_disconnects(self):
        assert _is_client_disconnect_error(BrokenPipeError())
        assert _is_client_disconnect_error(ConnectionResetError())

    def test_httpx_transport_errors_are_not_client_disconnects(self):
        # Upstream I/O failures arrive wrapped in httpx exception types and
        # MUST still count as provider failures.
        assert not _is_client_disconnect_error(httpx.ReadError("upstream reset"))
        assert not _is_client_disconnect_error(httpx.ConnectError("refused"))
        assert not _is_client_disconnect_error(TimeoutError())


# ---------------------------------------------------------------------------
# End-to-end tests — threaded server + stub upstream
# ---------------------------------------------------------------------------

PROXY_PORT = 18971

# Mutable stub behavior, set per-test.
_stub_state = {"status": 200, "delay": 0.0, "body": b'{"ok": true}'}


class _StubUpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if _stub_state["delay"]:
            time.sleep(_stub_state["delay"])
        body = _stub_state["body"]
        self.send_response(_stub_state["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture(scope="module")
def stub_upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubUpstreamHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield port
    server.shutdown()


@pytest.fixture(scope="module")
def proxy():
    server = ProxyServer(host="127.0.0.1", port=PROXY_PORT)
    server.start(blocking=False)
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=0.5)
            s.close()
            break
        except OSError:
            time.sleep(0.05)
    yield server
    server.stop()


@pytest.fixture()
def breaker_spy(monkeypatch, stub_upstream):
    """Spy on the breaker registry; make the stub an 'intercepted' host so
    the handler engages breaker accounting, with an isolated provider key."""
    monkeypatch.setattr(
        proxy_server_module,
        "INTERCEPT_HOSTS",
        set(proxy_server_module.INTERCEPT_HOSTS) | {"127.0.0.1"},
    )
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRIES", "2")
    monkeypatch.setenv("TOKENPAK_UPSTREAM_RETRY_BASE_WAIT", "0.01")

    registry = get_circuit_breaker_registry()
    registry._breakers.pop("127.0.0.1", None)  # clean slate per test
    calls = {"success": [], "failure": []}
    real_success, real_failure = registry.record_success, registry.record_failure

    def spy_success(provider):
        calls["success"].append(provider)
        return real_success(provider)

    def spy_failure(provider):
        calls["failure"].append(provider)
        return real_failure(provider)

    monkeypatch.setattr(registry, "record_success", spy_success)
    monkeypatch.setattr(registry, "record_failure", spy_failure)
    yield calls
    registry._breakers.pop("127.0.0.1", None)


def _proxy_request(stub_port: int, body: bytes) -> tuple:
    conn = http.client.HTTPConnection("127.0.0.1", PROXY_PORT, timeout=20)
    conn.request(
        "POST",
        f"http://127.0.0.1:{stub_port}/v1/messages",
        body=body,
        headers={
            "Content-Type": "application/json",
            # Intercepted /v1/messages requests require client credentials.
            "x-api-key": "sk-tokenpak-breaker-polarity-test",
        },
    )
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


_REQ_BODY = json.dumps(
    {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "breaker polarity probe"}],
    }
).encode()


class TestBreakerPolarityEndToEnd:
    def test_upstream_503_after_retries_records_provider_failure(
        self, proxy, stub_upstream, breaker_spy
    ):
        _stub_state.update(status=503, delay=0.0, body=b'{"error": "overloaded"}')
        status, _ = _proxy_request(stub_upstream, _REQ_BODY)

        assert status == 503
        assert "127.0.0.1" in breaker_spy["failure"], (
            "provider 5xx after retries must record a breaker failure"
        )
        assert "127.0.0.1" not in breaker_spy["success"], (
            "provider 5xx must not be recorded as breaker success"
        )

    def test_upstream_200_records_provider_success(
        self, proxy, stub_upstream, breaker_spy
    ):
        _stub_state.update(status=200, delay=0.0, body=b'{"ok": true}')
        status, _ = _proxy_request(stub_upstream, _REQ_BODY)

        assert status == 200
        assert "127.0.0.1" in breaker_spy["success"]
        assert "127.0.0.1" not in breaker_spy["failure"]

    def test_client_disconnect_mid_response_is_not_a_provider_failure(
        self, proxy, stub_upstream, breaker_spy
    ):
        # Big response + delayed stub: the client sends the request, then
        # RST-closes its socket before the proxy writes the response back —
        # the proxy's wfile.write raises BrokenPipe/ConnectionReset.
        _stub_state.update(
            status=200, delay=0.6, body=b'{"pad": "' + b"x" * (8 * 1024 * 1024) + b'"}'
        )
        ps = proxy
        errors_before = ps.session["errors"]

        s = socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=10)
        raw = (
            f"POST http://127.0.0.1:{stub_upstream}/v1/messages HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{stub_upstream}\r\n"
            "Content-Type: application/json\r\n"
            "x-api-key: sk-tokenpak-breaker-polarity-test\r\n"
            f"Content-Length: {len(_REQ_BODY)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + _REQ_BODY
        s.sendall(raw)
        time.sleep(0.2)  # let the proxy read the request and hit upstream
        # RST on close so the proxy's response write fails hard.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()

        # Wait for the handler to hit its error path.
        deadline = time.time() + 15
        while time.time() < deadline:
            if ps.session["errors"] > errors_before:
                break
            time.sleep(0.05)
        assert ps.session["errors"] > errors_before, (
            "handler never reached its error path — test setup issue"
        )

        assert "127.0.0.1" not in breaker_spy["failure"], (
            "a client hanging up mid-response must not count as a provider failure"
        )
        assert "127.0.0.1" not in breaker_spy["success"]
        # Reset stub for any later test.
        _stub_state.update(status=200, delay=0.0, body=b'{"ok": true}')
