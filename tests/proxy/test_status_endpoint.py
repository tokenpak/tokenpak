"""tests/proxy/test_status_endpoint.py

Unit tests for GET /status endpoint (GAR-B4).

Tests cover:
  - All required fields present in status() response
  - Provider health states: reachable / unreachable / no-key
  - Graceful empty state when no providers are configured
  - Non-blocking: uses cached circuit-breaker state, not live probes
  - requests_total increments tracked correctly
  - last_request_at is None before first request, string after
"""
from __future__ import annotations

import os
import time
from unittest.mock import patch, MagicMock

import pytest

from tokenpak.proxy.server import ProxyServer
from tokenpak.proxy.circuit_breaker import (
    _reset_registry_for_testing,
    get_circuit_breaker_registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_server() -> ProxyServer:
    """Return a ProxyServer instance without starting a socket."""
    return ProxyServer(host="127.0.0.1", port=0)


# ---------------------------------------------------------------------------
# Field presence
# ---------------------------------------------------------------------------

class TestStatusFields:
    """GET /status returns all required fields."""

    def test_all_required_fields_present(self):
        ps = _make_server()
        result = ps.status()
        required = {
            "version",
            "uptime_seconds",
            "started_at",
            "providers",
            "active_alerts",
            "requests_total",
            "last_request_at",
        }
        missing = required - result.keys()
        assert not missing, f"Missing fields: {missing}"

    def test_version_is_string(self):
        ps = _make_server()
        result = ps.status()
        assert isinstance(result["version"], str)
        assert result["version"]  # non-empty

    def test_uptime_seconds_is_non_negative_int(self):
        ps = _make_server()
        result = ps.status()
        assert isinstance(result["uptime_seconds"], int)
        assert result["uptime_seconds"] >= 0

    def test_started_at_is_iso_timestamp(self):
        ps = _make_server()
        result = ps.status()
        sat = result["started_at"]
        assert isinstance(sat, str)
        # Must look like 2026-01-01T00:00:00Z
        assert sat.endswith("Z")
        assert "T" in sat

    def test_providers_is_dict(self):
        ps = _make_server()
        result = ps.status()
        assert isinstance(result["providers"], dict)

    def test_active_alerts_is_int(self):
        ps = _make_server()
        result = ps.status()
        assert isinstance(result["active_alerts"], int)
        assert result["active_alerts"] >= 0

    def test_requests_total_is_int(self):
        ps = _make_server()
        result = ps.status()
        assert isinstance(result["requests_total"], int)
        assert result["requests_total"] >= 0

    def test_last_request_at_is_none_before_first_request(self):
        ps = _make_server()
        result = ps.status()
        assert result["last_request_at"] is None

    def test_last_request_at_returns_timestamp_after_request(self):
        ps = _make_server()
        # Simulate a completed request
        with ps._last_lock:
            ps._last_request = {"timestamp": "2026-04-15T04:00:00", "model": "claude"}
        result = ps.status()
        assert result["last_request_at"] == "2026-04-15T04:00:00"


# ---------------------------------------------------------------------------
# requests_total
# ---------------------------------------------------------------------------

class TestRequestsTotal:
    """requests_total reflects session["requests"] counter."""

    def test_requests_total_starts_at_zero(self):
        ps = _make_server()
        assert ps.status()["requests_total"] == 0

    def test_requests_total_reflects_session_counter(self):
        ps = _make_server()
        with ps._session_lock:
            ps.session["requests"] = 42
        assert ps.status()["requests_total"] == 42


# ---------------------------------------------------------------------------
# Provider health
# ---------------------------------------------------------------------------

class TestProviderHealth:
    """Provider health values: reachable / unreachable / no-key."""

    def setup_method(self):
        _reset_registry_for_testing()

    def test_no_key_when_env_var_absent(self):
        ps = _make_server()
        with patch.dict(os.environ, {}, clear=True):
            # Remove all provider keys
            for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
                os.environ.pop(var, None)
            result = ps.status()
        providers = result["providers"]
        for name in ("anthropic", "openai", "google"):
            assert providers[name] == "no-key", (
                f"Expected 'no-key' for {name} when env var absent, got {providers[name]!r}"
            )

    def test_no_key_when_env_var_empty_string(self):
        ps = _make_server()
        env = {
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "   ",  # whitespace only
            "GOOGLE_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=False):
            result = ps.status()
        providers = result["providers"]
        assert providers["anthropic"] == "no-key"
        assert providers["openai"] == "no-key"

    def test_reachable_when_key_set_and_no_circuit_breaker(self):
        ps = _make_server()
        env = {"ANTHROPIC_API_KEY": "sk-ant-test-key"}
        _reset_registry_for_testing()  # empty registry
        with patch.dict(os.environ, env, clear=False):
            result = ps.status()
        assert result["providers"]["anthropic"] == "reachable"

    def test_unreachable_when_circuit_breaker_open(self):
        ps = _make_server()
        _reset_registry_for_testing()
        registry = get_circuit_breaker_registry()
        # Force anthropic CB to open state via registry.record_failure()
        cb = registry._get_or_create("anthropic")
        for _ in range(cb._config.failure_threshold + 1):
            registry.record_failure("anthropic")

        env = {"ANTHROPIC_API_KEY": "sk-ant-test-key"}
        with patch.dict(os.environ, env, clear=False):
            result = ps.status()
        assert result["providers"]["anthropic"] == "unreachable"

    def test_active_alerts_increments_per_open_circuit(self):
        ps = _make_server()
        _reset_registry_for_testing()
        registry = get_circuit_breaker_registry()
        env = {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "OPENAI_API_KEY": "sk-openai-test",
            "GOOGLE_API_KEY": "",
        }
        # Open anthropic and openai circuits
        for name in ("anthropic", "openai"):
            cb = registry._get_or_create(name)
            for _ in range(cb._config.failure_threshold + 1):
                registry.record_failure(name)

        with patch.dict(os.environ, env, clear=False):
            result = ps.status()
        assert result["active_alerts"] == 2, (
            f"Expected 2 active alerts (2 open circuits), got {result['active_alerts']}"
        )


# ---------------------------------------------------------------------------
# Graceful empty state
# ---------------------------------------------------------------------------

class TestGracefulEmptyState:
    """Endpoint works when no providers are configured."""

    def setup_method(self):
        _reset_registry_for_testing()

    def test_empty_providers_returns_no_key_for_all(self):
        """When no env vars set, providers shows no-key (not error or missing)."""
        ps = _make_server()
        with patch.dict(os.environ, {}, clear=True):
            for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
                os.environ.pop(var, None)
            result = ps.status()
        assert result["providers"] == {
            "anthropic": "no-key",
            "openai": "no-key",
            "google": "no-key",
        }
        assert result["active_alerts"] == 0

    def test_status_returns_200_compatible_dict(self):
        """status() returns a plain dict (no exceptions raised)."""
        ps = _make_server()
        result = ps.status()
        assert isinstance(result, dict)
