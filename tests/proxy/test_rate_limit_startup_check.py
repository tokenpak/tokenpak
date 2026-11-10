# SPDX-License-Identifier: Apache-2.0
"""A client's one-token startup check does not count toward the shared 429 breaker.

Claude Code opens each session with a non-streaming request for a single
output token and reads the provider's limit headers from the reply. When that
reply is a 429 the client carries on and the session's real requests succeed.
Counting those replies let a handful of sessions starting inside one window
open the provider-wide breaker and refuse every session's traffic for the
cooldown. They are now counted separately and never open the circuit.
"""

from __future__ import annotations

import json
import socket
import time
from http.client import HTTPConnection
from unittest.mock import MagicMock

import pytest

from tokenpak.proxy import circuit_breaker as cb_mod
from tokenpak.proxy.circuit_breaker import (
    RateLimitCircuitBreaker,
    RateLimitCircuitBreakerRegistry,
    _reset_rl_registry_for_testing,
    get_rate_limit_registry,
)
from tokenpak.proxy.server import ProxyServer

_MESSAGES_URL = "http://api.anthropic.com/v1/messages"


def _body(max_tokens: object, **extra: object) -> bytes:
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "quota"}],
    }
    payload.update(extra)
    return json.dumps(payload).encode()


STARTUP_CHECK = _body(1)
WORK = _body(4096)


@pytest.fixture(autouse=True)
def _default_limit(monkeypatch):
    monkeypatch.delenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_TOKENS", raising=False)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_one_token_non_streaming_request_is_a_startup_check():
    assert cb_mod.is_startup_check(STARTUP_CHECK) is True


@pytest.mark.parametrize(
    "body",
    [
        WORK,
        _body(1, stream=True),
        _body(0),
        _body(True),
        _body("1"),
        b"",
        None,
        b"not json",
        b"[1]",
    ],
)
def test_other_requests_are_not_startup_checks(body):
    assert cb_mod.is_startup_check(body) is False


def test_limit_is_read_from_the_environment_at_use_time(monkeypatch):
    body = _body(5)
    assert cb_mod.is_startup_check(body) is False
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_TOKENS", "8")
    assert cb_mod.is_startup_check(body) is True
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_TOKENS", "0")
    assert cb_mod.is_startup_check(STARTUP_CHECK) is False, "0 disables the exemption"
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_TOKENS", "bogus")
    assert cb_mod.is_startup_check(STARTUP_CHECK) is True, "an invalid value keeps the default"


# ---------------------------------------------------------------------------
# Breaker accounting
# ---------------------------------------------------------------------------


def test_startup_check_429s_never_open_the_breaker():
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)
    for _ in range(20):
        cb.record_429(startup_check=True)
    assert cb.is_open() is False
    status = cb.status()
    assert status["recent_429s_in_window"] == 0
    assert status["startup_check_429s_ignored"] == 20


def test_work_429s_still_open_the_breaker():
    registry = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=5, cooldown_sec=30)
    for _ in range(4):
        registry.record_429("anthropic", startup_check=True)
        registry.record_429("anthropic")
    assert registry.is_open("anthropic") is False
    registry.record_429("anthropic")
    assert registry.is_open("anthropic") is True


# ---------------------------------------------------------------------------
# Through the proxy
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _send(port: int, body: bytes) -> int:
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
    resp.read()
    return resp.status


def test_session_start_429s_do_not_refuse_other_sessions():
    """Five sessions starting inside one window leave the provider usable."""
    _reset_rl_registry_for_testing(window_sec=60, threshold=5, cooldown_sec=30)
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {}
    mock_resp.content = b'{"error":{"type":"rate_limit_error"}}'
    pool = MagicMock()
    pool.request.return_value = mock_resp

    port = _free_port()
    server = ProxyServer(host="127.0.0.1", port=port)
    server.start(blocking=False)
    time.sleep(0.1)
    server._connection_pool = pool
    try:
        for _ in range(5):
            assert _send(port, STARTUP_CHECK) == 429  # passed through unchanged
        calls = pool.request.call_count
        # The next real request is forwarded (the stub's 429, after the
        # proxy's own upstream retries), not refused by an open circuit (503).
        assert _send(port, WORK) == 429
        assert pool.request.call_count > calls
        status = get_rate_limit_registry().all_statuses()
        assert any(s["startup_check_429s_ignored"] == 5 for s in status.values())
    finally:
        server.stop()
        _reset_rl_registry_for_testing()
