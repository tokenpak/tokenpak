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
import sys
import time
import urllib.request
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.needs_proxy

from tokenpak.proxy.server import ProxyServer

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


def _get_health(port: int = 18766, *, path: str = "/health") -> tuple[int, dict]:
    """Hit /health and return (status_code, response_dict)."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


CANONICAL_HEALTH_TOP_LEVEL = {
    "status",
    "uptime_seconds",
    "version",
    "requests_total",
    "requests_errors",
    "compression_ratio_avg",
    "is_degraded",
    "is_shutting_down",
    "in_flight_requests",
    "memory_guard",
    "admission",
    "agent_concurrency",
    "timestamp",
    "connection_pool",
    "circuit_breakers",
}


LEGACY_MIXIN_HEALTH_TOP_LEVEL = {
    "status",
    "compilation_mode",
    "vault_index",
    "router",
    "capsule_available",
    "canon",
    "skeleton",
    "shadow_reader",
    "budget",
    "tool_schema_registry",
    "term_resolver",
    "query_expansion",
    "cache_poison_removal",
    "upstream_timeout_seconds",
    "circuit_breakers",
    "stats",
    "latency",
}


def _recursive_health_paths(value: object, prefix: str = "$") -> set[str]:
    """Return JSON object paths, including the root contract path."""
    paths = {prefix}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}"
            paths.add(child_path)
            paths.update(_recursive_health_paths(child, child_path))
    return paths


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


@pytest.mark.quick
def test_health_canonical_wire_contract_is_exact_and_uncached(proxy):
    """The shipped handler keeps the 15-field/73-static-path basic shape."""
    _, first = _get_health()

    assert set(first) == CANONICAL_HEALTH_TOP_LEVEL
    # Provider names and their breaker-status fields are a dynamic keyed map.
    # Earlier tests may legitimately populate that process-global registry, so
    # exclude only descendants of the map while keeping the map path itself in
    # the exact static-shape assertion.
    static_paths = {
        path
        for path in _recursive_health_paths(first)
        if not path.startswith("$.circuit_breakers.providers.")
    }
    assert len(static_paths) == 73
    assert set(first["memory_guard"]) == {
        "enabled",
        "state",
        "thread_alive",
        "callback_policy",
        "configuration",
        "callbacks",
    }
    assert set(first["memory_guard"]["configuration"]) == {
        "source",
        "mode",
        "plan_sha256",
        "managed_config_path",
        "managed_file_present",
        "managed_file_ignored",
        "triggering_env",
        "warning",
    }
    assert set(first["memory_guard"]["callbacks"]) == {"compact", "token", "semantic"}
    assert set(first["admission"]) == {"limit", "available", "rejected"}
    assert set(first["agent_concurrency"]) == {
        "enabled",
        "max_parallel_subagents",
        "effective_cap",
        "degraded_serial",
        "in_flight",
        "queued",
        "queue_depth_max",
        "admitted_total",
        "queued_total",
        "rejected_queue_full",
        "rejected_wait_timeout",
        "source",
    }
    assert set(first["connection_pool"]) == {
        "http2_enabled",
        "active_providers",
        "total_requests",
        "reused_connections",
        "new_connections",
        "errors",
        "evicted_clients",
        "reuse_rate",
        "cleanup_pending_close",
        "cleanup_queued",
        "cleanup_in_progress",
        "cleanup_retrying",
        "cleanup_failures_total",
        "cleanup_worker_start_failures_total",
        "cleanup_completed_total",
        "cleanup_oldest_pending_seconds",
        "cleanup_workers_alive",
        "client_slots_used",
        "client_slots_max",
        "client_capacity_rejections_total",
        "cleanup_saturated",
        "retired_pending_close",
    }
    assert set(first["circuit_breakers"]) == {"enabled", "any_open", "providers"}

    assert isinstance(first["status"], str)
    assert type(first["uptime_seconds"]) is int
    assert isinstance(first["version"], str)
    assert type(first["requests_total"]) is int
    assert type(first["requests_errors"]) is int
    assert isinstance(first["compression_ratio_avg"], float)
    assert type(first["is_degraded"]) is bool
    assert type(first["is_shutting_down"]) is bool
    assert type(first["in_flight_requests"]) is int
    assert isinstance(first["timestamp"], str)
    assert isinstance(first["connection_pool"]["active_providers"], list)
    assert isinstance(first["circuit_breakers"]["providers"], dict)

    # The canonical endpoint is intentionally uncached: a state change is
    # visible immediately, inside the legacy mixin's one-second TTL window.
    with proxy._session_lock:
        proxy.session["requests"] += 1
    try:
        _, second = _get_health()
        assert second["requests_total"] == first["requests_total"] + 1
    finally:
        with proxy._session_lock:
            proxy.session["requests"] -= 1


def test_legacy_mixin_health_preserves_v113_schema_and_one_second_cache(monkeypatch):
    from tokenpak.core.runtime.proxy import SESSION
    from tokenpak.proxy import config, fallback, request_pipeline, vault_bridge
    from tokenpak.proxy.routes import ProxyRoutesMixin

    class LegacyHandler(ProxyRoutesMixin):
        def __init__(self):
            self.responses = []

        def _send_json(self, data, *, status=200):
            self.responses.append((status, data))

    fake_vault = SimpleNamespace(
        available=True,
        blocks={"a": object(), "b": object()},
        tokenpak_dir="/compat/vault",
    )
    monkeypatch.setattr(vault_bridge, "get_vault_index", lambda: fake_vault)
    monkeypatch.setattr(vault_bridge, "get_capsule_builder", lambda: None)
    monkeypatch.setattr(vault_bridge, "get_term_resolver", lambda: None)
    monkeypatch.setattr(request_pipeline, "_router_health", lambda: {"components": {}})
    monkeypatch.setattr(config, "skeleton_active", lambda: False)
    monkeypatch.setattr(
        fallback,
        "_provider_circuits",
        {"anthropic": {"open": False, "failures": 0}},
    )

    original_cache = dict(request_pipeline._health_cache)
    original_session = dict(SESSION)
    SESSION.update(
        {
            "requests": 7,
            "input_tokens": 100,
            "sent_input_tokens": 80,
            "saved_tokens": 20,
            "errors": 1,
        }
    )
    request_pipeline._health_cache.update({"ts": 0.0, "data": None})
    clock = iter((100.0, 100.999, 101.0))
    monkeypatch.setattr("tokenpak.proxy.routes.time.monotonic", lambda: next(clock))
    handler = LegacyHandler()
    try:
        handler._route_health()
        first = handler.responses[-1][1]
        SESSION["requests"] = 8

        handler._route_health()
        cached = handler.responses[-1][1]
        handler._route_health()
        refreshed = handler.responses[-1][1]
    finally:
        request_pipeline._health_cache.clear()
        request_pipeline._health_cache.update(original_cache)
        SESSION.clear()
        SESSION.update(original_session)

    assert set(first) == LEGACY_MIXIN_HEALTH_TOP_LEVEL
    assert set(first) != CANONICAL_HEALTH_TOP_LEVEL
    assert first["vault_index"] == {
        "available": True,
        "blocks": 2,
        "path": "/compat/vault",
    }
    assert first["stats"]["requests"] == 7
    assert cached is first
    assert cached["stats"]["requests"] == 7
    assert refreshed is not first
    assert refreshed["stats"]["requests"] == 8


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
# Test 17 — loopback requires no auth (no token, still 200)
# ---------------------------------------------------------------------------


def test_health_loopback_no_auth_required(proxy):
    """Loopback health access stays available without an Authorization header."""
    req = urllib.request.Request("http://127.0.0.1:18766/health")
    # Explicitly do NOT set Authorization
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


# ---------------------------------------------------------------------------
# Test 18 — Content-Type is application/json
# ---------------------------------------------------------------------------


def test_health_content_type_json(proxy):
    req = urllib.request.Request("http://127.0.0.1:18766/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        ct = resp.headers.get("Content-Type", "")
        assert resp.status == 200
        assert ct == "application/json"
        assert isinstance(json.loads(resp.read()), dict)


def test_health_deep_without_psutil_returns_json_and_keeps_other_diagnostics(proxy):
    """A base install must not lose the connection when psutil is absent."""
    disk = SimpleNamespace(free=5 * 1024**3)
    with patch("shutil.disk_usage", return_value=disk), patch.dict(sys.modules, {"psutil": None}):
        status, data = _get_health(path="/health?deep=true")

    assert status == 200
    assert data["memory"] == {
        "rss_mb": None,
        "available": False,
        "reason": "optional_dependency_unavailable",
    }
    assert isinstance(data["providers"], list)
    assert data["disk"]["available"] is True
    assert data["disk"]["available_gb"] == 5.0


def test_health_deep_with_installed_psutil_returns_measured_rss(proxy):
    """The installed optional dependency reports a real, non-null measurement."""
    pytest.importorskip("psutil", reason="optional psutil dependency is not installed")

    status, data = _get_health(path="/health?deep=yes")

    assert status == 200
    assert data["memory"]["available"] is True
    assert isinstance(data["memory"]["rss_mb"], float)
    assert data["memory"]["rss_mb"] > 0
    assert "reason" not in data["memory"]
    assert isinstance(data["providers"], list)
    assert "disk" in data


def test_health_deep_psutil_probe_failure_is_independent_and_nonzero_fabricating(proxy):
    """A failed RSS probe is unavailable, while provider/disk probes survive."""

    def broken_process():
        raise OSError("simulated psutil failure")

    fake_psutil = SimpleNamespace(Process=broken_process)
    disk = SimpleNamespace(free=7 * 1024**3)
    with (
        patch("shutil.disk_usage", return_value=disk),
        patch.dict(sys.modules, {"psutil": fake_psutil}),
    ):
        status, data = _get_health(path="/health?deep=1")

    assert status == 200
    assert data["memory"] == {
        "rss_mb": None,
        "available": False,
        "reason": "probe_failed",
    }
    assert data["memory"]["rss_mb"] is None
    assert isinstance(data["providers"], list)
    assert data["disk"]["available"] is True
    assert data["disk"]["available_gb"] == 7.0
