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
import logging
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from types import SimpleNamespace
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
_SESSION_HEADER = "X-Claude-Code-Session-Id"


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
    monkeypatch.delenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_WINDOW_SEC", raising=False)
    monkeypatch.delenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_EXEMPT", raising=False)


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
    """Each session's first startup check is exempt (one per session)."""
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)
    for i in range(20):
        cb.record_429(startup_check=True, session_id=f"session-{i}")
    assert cb.is_open() is False
    status = cb.status()
    assert status["recent_429s_in_window"] == 0
    assert status["startup_check_429s_ignored"] == 20


def test_work_429s_still_open_the_breaker():
    registry = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=5, cooldown_sec=30)
    for i in range(4):
        registry.record_429("anthropic", startup_check=True, session_id=f"session-{i}")
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


def _send(port: int, body: bytes, session: str | None = None) -> int:
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "x-api-key": "sk-test-key",
        "anthropic-version": "2023-06-01",
    }
    if session is not None:
        headers[_SESSION_HEADER] = session
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", _MESSAGES_URL, body=body, headers=headers)
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
        for i in range(5):
            # Five sessions starting, each with its own identity; each
            # reply is passed through unchanged.
            assert _send(port, STARTUP_CHECK, session=f"session-{i}") == 429
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


# ---------------------------------------------------------------------------
# The exemption is bounded: one per session, per window, capped per provider
#
# Exempting every request shaped like a startup check let one session repeat
# such requests indefinitely without ever opening the circuit.
# ---------------------------------------------------------------------------


@contextmanager
def _upstream_refusing() -> Iterator[tuple[int, MagicMock]]:
    """A proxy whose upstream answers every request with a limit response."""
    _reset_rl_registry_for_testing(window_sec=60, threshold=5, cooldown_sec=30)
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.headers = {}
    mock_resp.content = b'{"error":{"type":"busy"}}'
    pool = MagicMock()
    pool.request.return_value = mock_resp
    port = _free_port()
    server = ProxyServer(host="127.0.0.1", port=port)
    server.start(blocking=False)
    time.sleep(0.1)
    server._connection_pool = pool
    try:
        yield port, pool
    finally:
        server.stop()
        _reset_rl_registry_for_testing()


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    """Drive the breaker module's monotonic clock by hand."""
    now = [1000.0]
    monkeypatch.setattr(
        cb_mod, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time, sleep=time.sleep)
    )
    return now


def test_repeated_same_session_requests_eventually_open_breaker():
    """Reproducer: repeated one-token requests with no session identity count."""
    reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=5, cooldown_sec=30)
    body = json.dumps(
        {
            "model": "test",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "work"}],
            "metadata": {"user_id": "same-session"},
        }
    ).encode()
    for _ in range(20):
        reg.record_429("anthropic", startup_check=cb_mod.is_startup_check(body))
    assert reg.is_open("anthropic"), f"state={reg.all_statuses()}"


def test_repeated_request_through_proxy_is_bounded():
    """Reproducer: the same request repeated through the proxy is refused once the circuit opens."""
    body = json.dumps(
        {
            "model": "test",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "work"}],
            "metadata": {"user_id": "same-session"},
        }
    ).encode()
    with _upstream_refusing() as (port, pool):
        statuses = [_send(port, body) for _ in range(8)]
        assert 503 in statuses, f"statuses={statuses}; upstream_calls={pool.request.call_count}"


def test_one_session_gets_one_exemption_then_its_checks_count():
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)
    for _ in range(5):
        cb.record_429(startup_check=True, session_id="session-a")
    status = cb.status()
    assert cb.is_open() is False
    assert status["startup_check_429s_ignored"] == 1
    assert status["recent_429s_in_window"] == 4
    cb.record_429(startup_check=True, session_id="session-a")
    assert cb.is_open() is True


def test_startup_check_without_a_session_identity_counts():
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)
    for session in (None, ""):
        cb.record_429(startup_check=True, session_id=session)
    assert cb.status()["startup_check_429s_ignored"] == 0
    assert cb.status()["recent_429s_in_window"] == 2


def test_same_session_repeating_through_the_proxy_opens_the_circuit():
    with _upstream_refusing() as (port, _pool):
        statuses = [_send(port, STARTUP_CHECK, session="session-a") for _ in range(8)]
        status = get_rate_limit_registry().all_statuses()
    assert statuses[0] == 429, "the session's first check is passed through"
    assert 503 in statuses, f"statuses={statuses}"
    assert any(s["startup_check_429s_ignored"] == 1 for s in status.values())


def test_a_session_regains_its_exemption_only_after_the_window(monkeypatch, clock):
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_WINDOW_SEC", "10")
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=50, cooldown_sec=30)
    cb.record_429(startup_check=True, session_id="session-a")
    clock[0] += 9
    cb.record_429(startup_check=True, session_id="session-a")
    assert (cb.status()["startup_check_429s_ignored"], cb.status()["recent_429s_in_window"]) == (
        1,
        1,
    )
    clock[0] += 2  # 11 s after the exemption: the window has passed
    cb.record_429(startup_check=True, session_id="session-a")
    assert cb.status()["startup_check_429s_ignored"] == 2


def test_exemptions_per_window_are_capped(monkeypatch, clock):
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_WINDOW_SEC", "10")
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_EXEMPT", "2")
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=50, cooldown_sec=30)
    for session in ("a", "b", "c"):
        cb.record_429(startup_check=True, session_id=session)
    status = cb.status()
    assert (status["startup_check_429s_ignored"], status["recent_429s_in_window"]) == (2, 1)
    assert status["startup_check_exemptions_in_window"] == 2
    clock[0] += 11
    assert cb.status()["startup_check_exemptions_in_window"] == 0
    cb.record_429(startup_check=True, session_id="d")
    assert cb.status()["startup_check_429s_ignored"] == 3


def test_settings_are_read_at_use_time_and_zero_cap_disables(monkeypatch):
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=50, cooldown_sec=30)
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_EXEMPT", "0")
    cb.record_429(startup_check=True, session_id="session-a")
    assert cb.status()["startup_check_429s_ignored"] == 0
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_MAX_EXEMPT", "bogus")
    cb.record_429(startup_check=True, session_id="session-b")
    assert cb.status()["startup_check_429s_ignored"] == 1, "an invalid value keeps the default"


@pytest.mark.parametrize("raw", ["0", "-5", "nan", "inf", "bogus"])
def test_invalid_window_keeps_the_default(monkeypatch, raw):
    monkeypatch.setenv("TOKENPAK_RATE_LIMIT_STARTUP_CHECK_WINDOW_SEC", raw)
    assert cb_mod._startup_check_window_sec() == cb_mod._DEFAULT_STARTUP_WINDOW_SEC


def test_session_identity_is_kept_only_as_a_keyed_digest(caplog):
    secret_looking = "session-value-that-must-not-be-stored"
    cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)
    with caplog.at_level(logging.DEBUG):
        cb.record_429(startup_check=True, session_id=secret_looking)
        cb.record_429(startup_check=True, session_id=secret_looking)
    held = repr(vars(cb)) + json.dumps(cb.status())
    assert secret_looking not in held
    assert secret_looking not in caplog.text
    (digest,) = cb._startup_exempt
    assert len(digest) == 64 and digest != secret_looking
