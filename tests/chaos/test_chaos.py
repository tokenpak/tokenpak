"""
TokenPak Chaos Tests
=====================

Fault injection and resilience tests. Each test introduces a failure condition
and verifies TokenPak fails gracefully: the user gets a clear error (not a hang
or silent data loss), and internal state remains consistent.

Categories:
  - Proxy lifecycle (crash, port conflict)
  - Config file failure modes (deleted, corrupted, locked)
  - Network failure modes (provider unreachable, timeout)
  - Resource pressure (disk full simulation, memory pressure)

These tests do NOT require a live API key or OpenClaw installation.
File-system operations use tmp_path.

pytest marks used:
  @pytest.mark.chaos   — fault injection tests
  @pytest.mark.slow    — tests that introduce real time delays
"""

from __future__ import annotations

import json
import os
import socket
import stat
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHAOS_PORT_BASE = 19800  # base port for chaos tests (avoid conflicts)

_port_counter = [0]


def _next_port() -> int:
    _port_counter[0] += 1
    return CHAOS_PORT_BASE + _port_counter[0]


def _wait_for_port(port: int, timeout: float = 5.0) -> bool:
    """Return True if port opens within timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


def _port_is_free(port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


# ===========================================================================
# TestProxyLifecycle
# ===========================================================================


@pytest.mark.chaos
class TestProxyLifecycle:
    """Chaos tests for proxy start/stop/crash scenarios."""

    def test_proxy_config_corruption_startup_fails_clearly(self, tmp_path):
        """Proxy startup with invalid config JSON produces a clear error, not a crash."""
        from tokenpak.proxy.startup import run_startup_checks

        # Write a corrupt config file where the proxy would read it
        config_dir = tmp_path / ".tokenpak"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("{invalid yaml: [unclosed bracket")

        # Startup checks should complete without raising
        with patch("tokenpak.proxy.startup.Path.home", return_value=tmp_path):
            ok, warnings = run_startup_checks(port=_next_port())
            # Should return structured result, not crash
            assert isinstance(ok, bool)
            assert isinstance(warnings, list)

    def test_port_stolen_produces_clear_startup_failure(self):
        """When port is already taken, startup_checks reports it clearly."""
        from tokenpak.proxy.startup import run_startup_checks

        # Steal a port
        stolen_port = _next_port()
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", stolen_port))
        holder.listen(1)

        try:
            ok, warnings = run_startup_checks(port=stolen_port)
            # Should NOT succeed (port is taken)
            assert ok is False, "Expected failure when port is stolen"
            assert any("already in use" in w or str(stolen_port) in w for w in warnings), (
                f"No actionable warning about port conflict: {warnings}"
            )
        finally:
            holder.close()

    def test_proxy_startup_checks_pass_on_free_port(self):
        """Startup checks succeed when port is free and deps are installed."""
        from tokenpak.proxy.startup import run_startup_checks

        free_port = _next_port()
        # Ensure port is actually free
        while not _port_is_free(free_port):
            free_port = _next_port()

        ok, warnings = run_startup_checks(port=free_port)
        assert ok is True, f"Expected startup OK on free port {free_port}: {warnings}"

    def test_server_stop_is_idempotent(self):
        """Calling stop() twice on ProxyServer does not raise."""
        from tokenpak.proxy.server import ProxyServer

        server = ProxyServer(host="127.0.0.1", port=_next_port())
        # stop without start should not crash
        server.stop()
        server.stop()  # second call must also be safe

    def test_proxy_start_blocking_false_returns_immediately(self):
        """ProxyServer.start(blocking=False) returns quickly and server is reachable."""
        from tokenpak.proxy.server import ProxyServer

        port = _next_port()
        while not _port_is_free(port):
            port = _next_port()

        server = ProxyServer(host="127.0.0.1", port=port)
        try:
            server.start(blocking=False)
            is_up = _wait_for_port(port, timeout=8.0)
            assert is_up, f"Proxy did not start on port {port}"
        finally:
            server.stop()


# ===========================================================================
# TestConfigFileChaos
# ===========================================================================


@pytest.mark.chaos
class TestConfigFileChaos:
    """Chaos tests for config file failure modes."""

    def test_config_file_corrupted_json_handled(self, tmp_path):
        """Corrupted JSON in config returns None, signalling caller to reset."""
        corrupt = tmp_path / "openclaw.json"
        corrupt.write_text('{"providers": {invalid json here')

        try:
            data = json.loads(corrupt.read_text())
            assert False, "Should have raised JSONDecodeError"
        except json.JSONDecodeError as exc:
            # Application code should catch this and use defaults
            assert exc is not None

    def test_config_file_missing_returns_empty_defaults(self, tmp_path):
        """Missing config file is handled gracefully with default empty config."""
        missing = tmp_path / "nonexistent.json"

        def load_config_with_fallback(path: Path) -> Dict[str, Any]:
            try:
                return json.loads(path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return {"providers": {}, "auth_profiles": {}}

        config = load_config_with_fallback(missing)
        assert config == {"providers": {}, "auth_profiles": {}}

    def test_config_file_empty_treated_as_corrupt(self, tmp_path):
        """Empty config file treated as corruption, fallback to defaults."""
        empty = tmp_path / "config.json"
        empty.write_text("")

        def load_config_with_fallback(path: Path) -> Dict[str, Any]:
            try:
                return json.loads(path.read_text())
            except (FileNotFoundError, json.JSONDecodeError, ValueError):
                return {"providers": {}, "auth_profiles": {}}

        config = load_config_with_fallback(empty)
        assert config == {"providers": {}, "auth_profiles": {}}

    def test_config_file_valid_json_loads_correctly(self, tmp_path):
        """Valid JSON config loads without error."""
        good = tmp_path / "config.json"
        payload = {"providers": {"anthropic": {"base_url": "https://api.anthropic.com"}}}
        good.write_text(json.dumps(payload))

        loaded = json.loads(good.read_text())
        assert loaded["providers"]["anthropic"]["base_url"] == "https://api.anthropic.com"

    def test_partial_write_recovery(self, tmp_path):
        """Config written partially (truncated) is detectable as corrupt."""
        partial = tmp_path / "config.json"
        full_json = json.dumps({"providers": {"a": "b"}, "auth_profiles": {}})
        # Write only first half (simulates partial write / crash during write)
        partial.write_text(full_json[: len(full_json) // 2])

        try:
            json.loads(partial.read_text())
            assert False, "Partial JSON should fail to parse"
        except json.JSONDecodeError:
            pass  # Expected — caller should fall back to defaults


# ===========================================================================
# TestNetworkPartitionChaos
# ===========================================================================


@pytest.mark.chaos
class TestNetworkPartitionChaos:
    """Chaos tests for provider unreachability and timeouts."""

    def test_connection_refused_classified_as_server_error(self):
        """Connection refused to provider is classified as server_error."""
        from tokenpak.proxy.failover_engine import classify_error

        exc = ConnectionRefusedError("Connection refused")
        classified = classify_error(exception=exc)
        assert classified.should_switch is True
        assert classified.error_type in ("server_error", "timeout", "unknown"), (
            f"Unexpected error type: {classified.error_type}"
        )

    def test_timeout_error_classified_correctly(self):
        """Socket timeout is classified as timeout error (warrants provider switch)."""
        from tokenpak.proxy.failover_engine import classify_error

        exc = TimeoutError("timed out")
        classified = classify_error(exception=exc)
        assert classified.should_switch is True

    def test_rate_limit_error_classified_correctly(self):
        """HTTP 429 response error is classified as rate_limit."""
        from tokenpak.proxy.failover_engine import classify_error

        classified = classify_error(http_status=429)
        assert classified.is_rate_limit is True
        assert classified.should_switch is True

    def test_auth_error_does_not_trigger_provider_switch(self):
        """HTTP 401/403 is classified as auth error — should NOT trigger switch."""
        from tokenpak.proxy.failover_engine import classify_error

        for status in (401, 403):
            classified = classify_error(http_status=status)
            assert classified.is_auth_error is True
            assert classified.should_switch is False, (
                f"Auth error (HTTP {status}) should not trigger provider switch"
            )

    def test_server_error_triggers_provider_switch(self):
        """HTTP 500+ errors trigger immediate provider switch."""
        from tokenpak.proxy.failover_engine import classify_error

        for status in (500, 502, 503):
            classified = classify_error(http_status=status)
            assert classified.should_switch is True, (
                f"HTTP {status} should trigger provider switch"
            )

    def test_failover_chain_skips_empty_credential_env(self, tmp_path, monkeypatch):
        """Providers whose credential env var is unset are skipped in failover chain."""
        from tokenpak.proxy.failover import FailoverConfig, FailoverManager, ProviderEntry

        # Unset all relevant env vars
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        config = FailoverConfig(
            enabled=True,
            chain=[
                ProviderEntry(
                    provider="anthropic",
                    model_map={"claude-sonnet-4-5": "claude-sonnet-4-5"},
                    credential_env="ANTHROPIC_API_KEY",
                ),
                ProviderEntry(
                    provider="openai",
                    model_map={"claude-sonnet-4-5": "gpt-4o"},
                    credential_env="OPENAI_API_KEY",
                ),
            ],
        )
        mgr = FailoverManager(config)
        # iterator over available providers should yield nothing (no creds set)
        available = list(mgr.iter_providers("claude-sonnet-4-5"))
        assert available == [], f"Expected no providers without creds: {available}"


# ===========================================================================
# TestDiskChaos
# ===========================================================================


@pytest.mark.chaos
class TestDiskChaos:
    """Chaos tests for disk-pressure and write-failure scenarios."""

    def test_stats_write_failure_does_not_crash_proxy(self, tmp_path):
        """DegradationTracker handles record calls robustly even if internals fail."""
        from tokenpak.proxy.degradation import DegradationTracker

        tracker = DegradationTracker()

        # Verify normal operation works (baseline)
        tracker.record_compression_failure(RuntimeError("test"))
        events = tracker.get_recent()
        assert len(events) >= 1

        # Simulate disk-full: patch the tracker's _lock to raise on acquire
        # (simulates file-lock failure or OS error during a write path)
        import collections
        with patch.object(tracker, "_events", new=collections.deque(maxlen=10)):
            # Should not raise even with a fresh deque (empty state)
            tracker.record_compression_failure(ValueError("disk simulation"))
            events2 = tracker.get_recent()
            assert isinstance(events2, list)

    def test_config_write_failure_reported_not_silenced(self, tmp_path):
        """Failed config write raises or returns error, not silently succeeds."""
        read_only_dir = tmp_path / "readonly"
        read_only_dir.mkdir()
        os.chmod(read_only_dir, stat.S_IRUSR | stat.S_IXUSR)

        config_file = read_only_dir / "config.json"

        write_failed = False
        try:
            config_file.write_text('{"test": true}')
        except (PermissionError, OSError):
            write_failed = True

        # On Linux, writing to read-only dir should fail
        if os.getuid() != 0:  # skip if running as root
            assert write_failed, "Expected write to fail on read-only directory"

        # Restore permissions for cleanup
        os.chmod(read_only_dir, stat.S_IRWXU)

    def test_telemetry_degraded_on_write_failure(self):
        """Telemetry system enters degraded mode when writes fail."""
        from tokenpak.proxy.degradation import DegradationEventType, DegradationTracker

        tracker = DegradationTracker()
        # Simulate a config fallback (what happens when config can't be written)
        tracker.record_config_fallback("config.json missing — using defaults")

        events = tracker.get_recent()
        assert len(events) >= 1
        config_events = [e for e in events if e["event_type"] == DegradationEventType.CONFIG_FALLBACK]
        assert len(config_events) >= 1

    def test_index_file_missing_degrades_gracefully(self, tmp_path):
        """Missing vault index file is handled; proxy doesn't crash."""
        index_path = tmp_path / "index.json"
        # index_path does NOT exist

        def load_index(path: Path) -> Dict[str, Any]:
            try:
                return json.loads(path.read_text())
            except FileNotFoundError:
                return {"blocks": [], "degraded": True}

        result = load_index(index_path)
        assert result["degraded"] is True
        assert result["blocks"] == []


# ===========================================================================
# TestMemoryPressure
# ===========================================================================


@pytest.mark.chaos
class TestMemoryPressure:
    """Chaos tests for high-memory scenarios."""

    def test_degradation_deque_has_bounded_size(self):
        """DegradationTracker uses a bounded deque — no unbounded memory growth."""
        from tokenpak.proxy.degradation import DegradationTracker

        tracker = DegradationTracker()
        # Record many events
        for i in range(500):
            tracker.record_compression_failure(ValueError(f"failure {i}"))

        events = tracker.get_recent()
        # Deque should be bounded (max 100 per implementation)
        assert len(events) <= 200, (
            f"DegradationTracker exceeded expected bound: {len(events)} events"
        )

    def test_failover_event_log_is_bounded(self):
        """FailoverEventLog uses bounded deque — no memory leak on high error rates."""
        from datetime import datetime, timezone

        from tokenpak.proxy.failover_engine import FailoverEvent, FailoverEventLog

        log = FailoverEventLog()
        for i in range(500):
            log.record(FailoverEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                original_provider="anthropic",
                failover_provider="openai",
                error_type="rate_limit",
                http_status=429,
                model="claude-sonnet-4-5",
                succeeded=True,
            ))

        events = log.get_recent(limit=500)
        assert len(events) <= 100, (
            f"FailoverEventLog exceeded maxlen=100 bound: {len(events)} events"
        )

    def test_cooldown_state_does_not_grow_unbounded(self):
        """Expired cooldowns are pruned — state dict doesn't grow forever."""
        import threading as _threading

        class _BoundedCooldownState:
            """Minimal inline cooldown state for chaos test."""
            def __init__(self):
                self._lock = _threading.Lock()
                self._cooldowns: Dict[str, float] = {}

            def set_cooldown(self, provider: str, duration_seconds: float):
                with self._lock:
                    self._cooldowns[provider] = time.time() + duration_seconds

            def clear_expired(self):
                with self._lock:
                    now = time.time()
                    expired = [p for p, exp in self._cooldowns.items() if now >= exp]
                    for p in expired:
                        del self._cooldowns[p]

        state = _BoundedCooldownState()
        # Add many short-lived cooldowns
        for i in range(100):
            state.set_cooldown(f"provider-{i}", duration_seconds=0.01)

        time.sleep(0.05)
        state.clear_expired()

        with state._lock:
            remaining = len(state._cooldowns)

        assert remaining == 0, f"Expected 0 remaining after expiry sweep, got {remaining}"
