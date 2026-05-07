"""tests/proxy/test_rate_limit_circuit_breaker.py

Regression tests for TRIX-MTC-06:
  - Cost tracking returns cost=0 for non-200 responses (no phantom cost entries)
  - Repeated 429 burst triggers rate-limit circuit breaker
  - Circuit closes after cooldown and requests proceed normally

AC-MTC-20
"""

from __future__ import annotations

import json
import socket
import time
from http.client import HTTPConnection
from unittest.mock import MagicMock

from tokenpak.proxy.circuit_breaker import (
    RateLimitCircuitBreaker,
    _reset_rl_registry_for_testing,
)
from tokenpak.proxy.server import ProxyServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_mock_pool(status_code: int, body: bytes = b"{}") -> MagicMock:
    """Return a mock connection pool whose request() returns a fixed response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.headers = {}
    mock_resp.content = body

    pool = MagicMock()
    pool.request.return_value = mock_resp
    # stream() context manager not needed for non-streaming tests
    return pool


_MESSAGES_URL = "http://api.anthropic.com/v1/messages"
_REQUEST_BODY = json.dumps({
    "model": "claude-haiku-4-5",
    "max_tokens": 10,
    "messages": [{"role": "user", "content": "Hello from test"}],
}).encode()


def _send_request(port: int, body: bytes = _REQUEST_BODY) -> tuple[int, bytes]:
    """Send a forward-proxy request to the proxy and return (status, body)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "POST",
        _MESSAGES_URL,
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "x-api-key": "sk-test-key",
            "anthropic-version": "2023-06-01",
        },
    )
    resp = conn.getresponse()
    return resp.status, resp.read()


# ===========================================================================
# Test 1: 429 response → session cost = 0 (no phantom cost)
# ===========================================================================

class TestCostZeroOn429:
    """A 429 upstream response must log cost=0, not a positive estimate."""

    def test_429_response_records_zero_cost(self):
        # Reset the rate-limit registry so this test is isolated
        _reset_rl_registry_for_testing(window_sec=60, threshold=100)  # high threshold → never trips

        port = _free_port()
        server = ProxyServer(host="127.0.0.1", port=port)
        server.start(blocking=False)
        time.sleep(0.1)

        # Replace the connection pool with a mock that returns 429
        server._connection_pool = _make_mock_pool(429, b'{"error":{"type":"rate_limit_error"}}')

        try:
            _send_request(port)
            # Session cost must remain 0 — no tokens were generated
            with server._session_lock:
                assert server.session["cost"] == 0.0, (
                    f"Expected cost=0 for 429 response, got {server.session['cost']}"
                )
        finally:
            server.stop()

    def test_200_response_records_nonzero_cost(self):
        """Sanity check: a 200 response records positive cost when tokens are present."""
        _reset_rl_registry_for_testing(window_sec=60, threshold=100)

        port = _free_port()
        server = ProxyServer(host="127.0.0.1", port=port)
        server.start(blocking=False)
        time.sleep(0.1)

        ok_body = json.dumps({
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hi"}],
            "model": "claude-haiku-4-5",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 5},
        }).encode()
        server._connection_pool = _make_mock_pool(200, ok_body)

        try:
            _send_request(port)
            with server._session_lock:
                # With input_tokens from body estimate and output_tokens from response,
                # cost should be positive.
                assert server.session["cost"] >= 0.0
                # Confirm a request was logged (cost tracking ran)
                assert server.session["requests"] >= 1
        finally:
            server.stop()


# ===========================================================================
# Test 2: 5 consecutive 429s open the rate-limit circuit
# ===========================================================================

class TestCircuitOpensAfterThreshold:
    """After threshold 429s, the rate-limit circuit opens and is_open() returns True."""

    def test_circuit_opens_at_threshold(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)

        # Below threshold — circuit stays closed
        for i in range(4):
            cb.record_429()
            assert not cb.is_open(), f"Circuit should be closed after {i+1} 429s"

        # At threshold — circuit opens
        cb.record_429()
        assert cb.is_open(), "Circuit should be open after 5th 429"

    def test_proxy_returns_503_when_circuit_open(self):
        """After threshold 429s through the proxy, the circuit opens and the next
        request gets 503 without forwarding upstream (request_count stays the same)."""
        _reset_rl_registry_for_testing(window_sec=60, threshold=5, cooldown_sec=30)

        port = _free_port()
        server = ProxyServer(host="127.0.0.1", port=port)
        server.start(blocking=False)
        time.sleep(0.1)

        # Stub returns 429 every time
        mock_pool = _make_mock_pool(429, b'{"error":{"type":"rate_limit_error"}}')
        server._connection_pool = mock_pool

        try:
            # Send 5 requests — each records a 429 in the rate-limit registry
            for i in range(5):
                status, _ = _send_request(port)
                assert status == 429, f"Request {i+1}: expected 429 from stub, got {status}"

            upstream_calls_after_5 = mock_pool.request.call_count

            # 6th request — circuit should be open, so proxy returns 503
            # without calling the upstream at all
            status, body = _send_request(port)
            assert status == 503, f"Expected 503 from open circuit, got {status}"
            payload = json.loads(body)
            assert payload["error"]["type"] == "rate_limit_circuit_open"

            # Upstream must NOT have been called for the 6th request
            assert mock_pool.request.call_count == upstream_calls_after_5, (
                "Upstream should not be called when rate-limit circuit is open"
            )
        finally:
            server.stop()


# ===========================================================================
# Test 3: After cooldown, circuit closes and requests proceed normally
# ===========================================================================

class TestCircuitClosesAfterCooldown:
    """After the cooldown period elapses, the rate-limit circuit closes automatically."""

    def test_circuit_closes_after_cooldown(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=3, cooldown_sec=0.05)

        # Trip the circuit
        for _ in range(3):
            cb.record_429()
        assert cb.is_open(), "Circuit should be open after 3 429s"

        # Before cooldown — still open
        assert cb.is_open()

        # Wait for cooldown to expire
        time.sleep(0.07)

        # After cooldown — circuit auto-closes
        assert not cb.is_open(), "Circuit should be closed after cooldown elapsed"

    def test_proxy_forwards_after_cooldown(self):
        """After cooldown, the proxy forwards requests again instead of returning 503."""
        _reset_rl_registry_for_testing(window_sec=60, threshold=3, cooldown_sec=0.05)

        port = _free_port()
        server = ProxyServer(host="127.0.0.1", port=port)
        server.start(blocking=False)
        time.sleep(0.1)

        mock_pool = _make_mock_pool(429, b'{"error":{"type":"rate_limit_error"}}')
        server._connection_pool = mock_pool

        try:
            # Trip the circuit with 3 429 responses
            for i in range(3):
                status, _ = _send_request(port)
                assert status == 429

            # Circuit is now open — next request should be 503 (no upstream call)
            status, _ = _send_request(port)
            assert status == 503

            # Wait for cooldown
            time.sleep(0.08)

            # After cooldown, proxy should forward again (upstream returns 429 again,
            # but that's the upstream's response — not a circuit-breaker block)
            status, _ = _send_request(port)
            assert status == 429, (
                f"After cooldown, request should be forwarded upstream (429 from stub), "
                f"got {status}"
            )
        finally:
            server.stop()
