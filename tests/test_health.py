"""
tests/test_health.py — P1-T3: Health & Readiness Endpoints

Tests for /health and /ready endpoints on proxy.py.

Covers:
- /health schema (status, uptime, version, timestamp, components, suggestions)
- /health healthy/degraded/critical state transitions
- /health 200 for healthy/degraded; 503 for critical
- /health response < 100ms, no auth required
- /ready 200 when ready; 503 during startup/shutdown
- /ready response < 50ms
- Integration: circuit breaker → degraded
- Integration: all circuits open → critical (503)
"""
from __future__ import annotations

import json
import threading

# Compat shims — the old monolith exposed these as module-level globals.
# The modular tree uses ProxyServer instances with GracefulShutdown instead.
# For test purposes, we provide lightweight module-level stand-ins.
import threading as _threading
import time
from typing import Tuple

import pytest

from tokenpak.core.runtime.proxy import SESSION
from tokenpak.proxy.fallback import _provider_circuit_lock, _provider_circuits

# ---------------------------------------------------------------------------
# Modular imports (migrated from proxy monolith)
# ---------------------------------------------------------------------------
from tokenpak.proxy.server import ForwardProxyHandler

_proxy_ready: bool = False
_shutdown_event = _threading.Event()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(path: str, port: int) -> Tuple[int, dict]:
    """Minimal HTTP GET without urllib so we control auth headers."""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()
    return resp.status, body


def _reset_circuits():
    """Reset all circuit breakers to closed state."""
    with _provider_circuit_lock:
        for cb in _provider_circuits.values():
            cb["open"] = False
            cb["failures"] = 0


# ---------------------------------------------------------------------------
# Fixture — start the proxy server once for the module
# ---------------------------------------------------------------------------

_HEALTH_TEST_PORT = 19777


@pytest.fixture(scope="module")
def proxy_server():
    """Spin up proxy server on an ephemeral port for all tests."""
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", _HEALTH_TEST_PORT), ForwardProxyHandler)

    # Mark proxy as ready (simulates post-startup state)
    global _proxy_ready
    _proxy_ready = True
    _shutdown_event.clear()

    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    time.sleep(0.1)  # settle
    yield server
    _proxy_ready = False
    server.shutdown()


# ---------------------------------------------------------------------------
# /health — Schema tests
# ---------------------------------------------------------------------------

class TestHealthSchema:
    """Validate /health response structure."""

    def test_returns_200_healthy(self, proxy_server):
        _reset_circuits()
        status, _ = _make_request("/health", _HEALTH_TEST_PORT)
        assert status == 200

    def test_status_field_present(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert "status" in data

    def test_status_is_valid_value(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert data["status"] in ("healthy", "degraded", "critical")

    def test_uptime_present_and_non_negative(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert "uptime" in data
        assert isinstance(data["uptime"], int)
        assert data["uptime"] >= 0

    def test_version_present_and_string(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0

    def test_timestamp_present_and_utc(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert "timestamp" in data
        ts = data["timestamp"]
        assert "T" in ts and ts.endswith("Z"), f"Bad timestamp: {ts!r}"

    def test_components_present(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert "components" in data
        components = data["components"]
        assert "cache" in components
        assert "provider_connections" in components
        assert "config" in components

    def test_suggestions_present_and_list(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)

    def test_no_auth_required(self, proxy_server):
        """Health endpoint must be accessible without Authorization header."""
        _reset_circuits()
        status, _ = _make_request("/health", _HEALTH_TEST_PORT)
        assert status == 200

    def test_response_under_100ms(self, proxy_server):
        _reset_circuits()
        t0 = time.monotonic()
        _make_request("/health", _HEALTH_TEST_PORT)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 100, f"/health took {elapsed_ms:.1f}ms (>100ms)"


# ---------------------------------------------------------------------------
# /health — State transition tests
# ---------------------------------------------------------------------------

class TestHealthStates:
    """Validate healthy/degraded/critical state transitions."""

    def test_healthy_when_all_circuits_closed(self, proxy_server):
        _reset_circuits()
        _, data = _make_request("/health", _HEALTH_TEST_PORT)
        assert data["status"] == "healthy"

    def test_degraded_when_one_circuit_open(self, proxy_server):
        _reset_circuits()
        with _provider_circuit_lock:
            _provider_circuits["openai"]["open"] = True
        try:
            _, data = _make_request("/health", _HEALTH_TEST_PORT)
            assert data["status"] == "degraded"
        finally:
            _reset_circuits()

    def test_degraded_returns_200(self, proxy_server):
        _reset_circuits()
        with _provider_circuit_lock:
            _provider_circuits["openai"]["open"] = True
        try:
            status, _ = _make_request("/health", _HEALTH_TEST_PORT)
            assert status == 200
        finally:
            _reset_circuits()

    def test_degraded_has_suggestion(self, proxy_server):
        _reset_circuits()
        with _provider_circuit_lock:
            _provider_circuits["openai"]["open"] = True
        try:
            _, data = _make_request("/health", _HEALTH_TEST_PORT)
            assert len(data["suggestions"]) > 0
        finally:
            _reset_circuits()

    def test_critical_when_all_circuits_open(self, proxy_server):
        _reset_circuits()
        with _provider_circuit_lock:
            for cb in _provider_circuits.values():
                cb["open"] = True
        try:
            _, data = _make_request("/health", _HEALTH_TEST_PORT)
            assert data["status"] == "critical"
        finally:
            _reset_circuits()

    def test_critical_returns_503(self, proxy_server):
        _reset_circuits()
        with _provider_circuit_lock:
            for cb in _provider_circuits.values():
                cb["open"] = True
        try:
            status, _ = _make_request("/health", _HEALTH_TEST_PORT)
            assert status == 503
        finally:
            _reset_circuits()

    def test_critical_has_suggestion(self, proxy_server):
        _reset_circuits()
        with _provider_circuit_lock:
            for cb in _provider_circuits.values():
                cb["open"] = True
        try:
            _, data = _make_request("/health", _HEALTH_TEST_PORT)
            assert len(data["suggestions"]) > 0
            assert any("unreachable" in s.lower() or "provider" in s.lower()
                       for s in data["suggestions"])
        finally:
            _reset_circuits()

    def test_degraded_high_error_rate(self, proxy_server):
        _reset_circuits()
        # Inject high error rate: >10% errors
        original_requests = SESSION.get("requests", 0)
        original_errors = SESSION.get("errors", 0)
        SESSION["requests"] = 100
        SESSION["errors"] = 15  # 15%
        try:
            _, data = _make_request("/health", _HEALTH_TEST_PORT)
            assert data["status"] in ("degraded", "critical")
        finally:
            SESSION["requests"] = original_requests
            SESSION["errors"] = original_errors

    def test_healthy_suggestions_empty(self, proxy_server):
        _reset_circuits()
        SESSION["requests"] = 100
        SESSION["errors"] = 0
        try:
            _, data = _make_request("/health", _HEALTH_TEST_PORT)
            assert data["status"] == "healthy"
            assert data["suggestions"] == []
        finally:
            SESSION["requests"] = 0
            SESSION["errors"] = 0


# ---------------------------------------------------------------------------
# /ready — Readiness probe tests
# ---------------------------------------------------------------------------

class TestReadiness:
    """Validate /ready lifecycle probe."""

    def test_ready_200_when_ready(self, proxy_server):
        _proxy_ready = True
        _shutdown_event.clear()
        status, _ = _make_request("/ready", _HEALTH_TEST_PORT)
        assert status == 200

    def test_ready_body_true_when_ready(self, proxy_server):
        _proxy_ready = True
        _shutdown_event.clear()
        _, data = _make_request("/ready", _HEALTH_TEST_PORT)
        assert data["ready"] is True

    def test_ready_503_when_not_ready(self, proxy_server):
        _proxy_ready = False
        try:
            status, _ = _make_request("/ready", _HEALTH_TEST_PORT)
            assert status == 503
        finally:
            _proxy_ready = True

    def test_ready_body_false_when_not_ready(self, proxy_server):
        _proxy_ready = False
        try:
            _, data = _make_request("/ready", _HEALTH_TEST_PORT)
            assert data["ready"] is False
        finally:
            _proxy_ready = True

    def test_ready_503_during_shutdown(self, proxy_server):
        _proxy_ready = False
        _shutdown_event.set()
        try:
            status, data = _make_request("/ready", _HEALTH_TEST_PORT)
            assert status == 503
            assert data["status"] == "shutting_down"
        finally:
            _proxy_ready = True
            _shutdown_event.clear()

    def test_ready_no_auth_required(self, proxy_server):
        _proxy_ready = True
        _shutdown_event.clear()
        status, _ = _make_request("/ready", _HEALTH_TEST_PORT)
        assert status == 200

    def test_ready_response_under_50ms(self, proxy_server):
        _proxy_ready = True
        _shutdown_event.clear()
        t0 = time.monotonic()
        _make_request("/ready", _HEALTH_TEST_PORT)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 50, f"/ready took {elapsed_ms:.1f}ms (>50ms)"

    def test_ready_starting_up_reason(self, proxy_server):
        _proxy_ready = False
        _shutdown_event.clear()
        try:
            _, data = _make_request("/ready", _HEALTH_TEST_PORT)
            assert data["status"] == "starting_up"
        finally:
            _proxy_ready = True
