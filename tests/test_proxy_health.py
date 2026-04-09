"""
Tests for the /health endpoint — TokenPak Proxy

Covers:
- HTTP 200 status code
- All required JSON fields present
- Field types and value constraints
- Counter increments on requests
- Compression ratio tracking
- Uptime accuracy
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import deque

import pytest

from tokenpak.agent.proxy.server import ProxyServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def proxy():
    """Start a proxy server on an ephemeral port for all tests in this module."""
    server = ProxyServer(host="127.0.0.1", port=18766)
    server.start(blocking=False)
    time.sleep(0.1)  # brief settle
    yield server
    server.stop()


def _get_health(port: int = 18766) -> tuple[int, dict]:
    """Hit /health and return (status_code, response_dict)."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


# ---------------------------------------------------------------------------
# Test 1 — HTTP 200
# ---------------------------------------------------------------------------

@pytest.mark.quick
def test_health_returns_200(proxy):
    status, _ = _get_health()
    assert status == 200


# ---------------------------------------------------------------------------
# Test 2 — All required fields present
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "status",
    "uptime_seconds",
    "version",
    "requests_total",
    "requests_errors",
    "compression_ratio_avg",
    "timestamp",
}


@pytest.mark.quick
def test_health_required_fields_present(proxy):
    _, data = _get_health()
    missing = REQUIRED_FIELDS - data.keys()
    assert not missing, f"Missing fields: {missing}"


# ---------------------------------------------------------------------------
# Test 3 — status field equals "ok"
# ---------------------------------------------------------------------------

@pytest.mark.quick
def test_health_status_ok(proxy):
    _, data = _get_health()
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 4 — uptime_seconds >= 0
# ---------------------------------------------------------------------------

def test_health_uptime_non_negative(proxy):
    _, data = _get_health()
    assert data["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# Test 5 — uptime_seconds is an integer
# ---------------------------------------------------------------------------

def test_health_uptime_is_int(proxy):
    _, data = _get_health()
    assert isinstance(data["uptime_seconds"], int)


# ---------------------------------------------------------------------------
# Test 6 — version matches expected pattern
# ---------------------------------------------------------------------------

@pytest.mark.quick
def test_health_version_string(proxy):
    _, data = _get_health()
    assert isinstance(data["version"], str)
    parts = data["version"].split(".")
    assert len(parts) == 3, "version should be X.Y.Z"


# ---------------------------------------------------------------------------
# Test 7 — requests_total is non-negative int
# ---------------------------------------------------------------------------

def test_health_requests_total_non_negative(proxy):
    _, data = _get_health()
    assert isinstance(data["requests_total"], int)
    assert data["requests_total"] >= 0


# ---------------------------------------------------------------------------
# Test 8 — requests_errors is non-negative int
# ---------------------------------------------------------------------------

def test_health_requests_errors_non_negative(proxy):
    _, data = _get_health()
    assert isinstance(data["requests_errors"], int)
    assert data["requests_errors"] >= 0


# ---------------------------------------------------------------------------
# Test 9 — compression_ratio_avg is float in [0.0, 1.0]
# ---------------------------------------------------------------------------

def test_health_compression_ratio_avg_valid(proxy):
    _, data = _get_health()
    ratio = data["compression_ratio_avg"]
    assert isinstance(ratio, (int, float))
    assert 0.0 <= ratio <= 1.0


# ---------------------------------------------------------------------------
# Test 10 — timestamp is ISO 8601 UTC
# ---------------------------------------------------------------------------

def test_health_timestamp_format(proxy):
    _, data = _get_health()
    ts = data["timestamp"]
    assert isinstance(ts, str)
    # Must end with Z and contain T separator
    assert "T" in ts and ts.endswith("Z"), f"Bad timestamp format: {ts!r}"


# ---------------------------------------------------------------------------
# Test 11 — requests_total increments on session requests
# ---------------------------------------------------------------------------

def test_health_requests_total_increments(proxy):
    _, before = _get_health()
    before_count = before["requests_total"]

    # Manually bump the session counter (simulating a proxied request)
    with proxy._session_lock:
        proxy.session["requests"] += 3

    _, after = _get_health()
    assert after["requests_total"] == before_count + 3

    # Clean up
    with proxy._session_lock:
        proxy.session["requests"] -= 3


# ---------------------------------------------------------------------------
# Test 12 — requests_errors increments
# ---------------------------------------------------------------------------

def test_health_errors_increment(proxy):
    _, before = _get_health()
    before_errors = before["requests_errors"]

    with proxy._session_lock:
        proxy.session["errors"] += 2

    _, after = _get_health()
    assert after["requests_errors"] == before_errors + 2

    # Clean up
    with proxy._session_lock:
        proxy.session["errors"] -= 2


# ---------------------------------------------------------------------------
# Test 13 — compression_ratio_avg reflects rolling window
# ---------------------------------------------------------------------------

def test_health_compression_ratio_rolling(proxy):
    # Inject known ratios into the deque
    with proxy._compression_lock:
        original = list(proxy._compression_ratios)
        proxy._compression_ratios.clear()
        for ratio in [0.2, 0.4, 0.6]:
            proxy._compression_ratios.append(ratio)

    _, data = _get_health()
    expected = round((0.2 + 0.4 + 0.6) / 3, 4)
    assert abs(data["compression_ratio_avg"] - expected) < 0.01

    # Restore
    with proxy._compression_lock:
        proxy._compression_ratios.clear()
        for r in original:
            proxy._compression_ratios.append(r)


# ---------------------------------------------------------------------------
# Test 14 — compression_ratio_avg is 0.0 when no requests
# ---------------------------------------------------------------------------

def test_health_compression_ratio_zero_on_no_requests(proxy):
    with proxy._compression_lock:
        original = list(proxy._compression_ratios)
        proxy._compression_ratios.clear()

    _, data = _get_health()
    assert data["compression_ratio_avg"] == 0.0

    # Restore
    with proxy._compression_lock:
        for r in original:
            proxy._compression_ratios.append(r)


# ---------------------------------------------------------------------------
# Test 15 — response time < 50 ms
# ---------------------------------------------------------------------------

def test_health_response_time_under_50ms(proxy):
    t0 = time.time()
    _get_health()
    elapsed_ms = (time.time() - t0) * 1000
    assert elapsed_ms < 50, f"Health check took {elapsed_ms:.1f}ms (>50ms)"


# ---------------------------------------------------------------------------
# Test 16 — uptime grows over time
# ---------------------------------------------------------------------------

def test_health_uptime_grows(proxy):
    _, before = _get_health()
    time.sleep(1.1)
    _, after = _get_health()
    assert after["uptime_seconds"] >= before["uptime_seconds"]


# ---------------------------------------------------------------------------
# Test 17 — no auth required (no token, still 200)
# ---------------------------------------------------------------------------

def test_health_no_auth_required(proxy):
    """Health endpoint must be accessible without any Authorization header."""
    req = urllib.request.Request(f"http://127.0.0.1:18766/health")
    # Explicitly do NOT set Authorization
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


# ---------------------------------------------------------------------------
# Test 18 — Content-Type is application/json
# ---------------------------------------------------------------------------

def test_health_content_type_json(proxy):
    req = urllib.request.Request(f"http://127.0.0.1:18766/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        ct = resp.headers.get("Content-Type", "")
        assert "application/json" in ct
