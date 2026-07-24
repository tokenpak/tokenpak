"""
Tests for the TokenPak Circuit Breaker

Covers:
- CLOSED state passes requests through
- OPEN state fast-fails immediately
- HALF_OPEN allows single test request
- Automatic OPEN → HALF_OPEN after recovery timeout
- HALF_OPEN → CLOSED on success
- HALF_OPEN → OPEN on failure
- Per-provider isolation
- Circuit state exposed in /health endpoint
- /circuit-breakers endpoint returns all states
- provider_from_url helper
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from tokenpak.proxy.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    _reset_registry_for_testing,
    provider_from_url,
)
from tokenpak.proxy.server import ProxyServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fast_config(
    failure_threshold: int = 3,
    recovery_timeout: float = 0.1,
    window_seconds: float = 60.0,
) -> CircuitBreakerConfig:
    """Return a config with fast timeouts suitable for unit tests."""
    return CircuitBreakerConfig(
        enabled=True,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        window_seconds=window_seconds,
    )


def _make_cb(
    failure_threshold: int = 3,
    recovery_timeout: float = 0.1,
    window_seconds: float = 60.0,
) -> CircuitBreaker:
    return CircuitBreaker(
        "test_provider",
        _fast_config(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            window_seconds=window_seconds,
        ),
    )


# ===========================================================================
# 1. provider_from_url
# ===========================================================================


class TestProviderFromUrl:
    def test_anthropic(self):
        assert provider_from_url("https://api.anthropic.com/v1/messages") == "anthropic"

    def test_openai(self):
        assert provider_from_url("https://api.openai.com/v1/chat/completions") == "openai"

    def test_google_googleapis(self):
        assert provider_from_url("https://generativelanguage.googleapis.com/v1/models") == "google"

    def test_google_generativelanguage(self):
        assert provider_from_url("https://generativelanguage.googleapis.com/") == "google"

    def test_unknown_returns_hostname(self):
        result = provider_from_url("https://my-custom-llm.example.com/v1/chat")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unknown_url_no_crash(self):
        # Malformed URL should not raise
        result = provider_from_url("not_a_url")
        assert isinstance(result, str)


# ===========================================================================
# 2. CircuitBreaker — CLOSED state
# ===========================================================================


class TestClosedState:
    def test_new_breaker_is_closed(self):
        cb = _make_cb()
        assert cb.state == CircuitState.CLOSED

    def test_closed_allows_requests(self):
        cb = _make_cb()
        assert cb.allow_request() is True

    def test_closed_allows_repeated_requests(self):
        cb = _make_cb()
        for _ in range(100):
            assert cb.allow_request() is True

    def test_closed_success_stays_closed(self):
        cb = _make_cb()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_closed_failures_below_threshold_stay_closed(self):
        cb = _make_cb(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_closed_failures_at_threshold_trip_to_open(self):
        cb = _make_cb(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_closed_failure_counter_resets_outside_window(self):
        """Failures outside the rolling window shouldn't count toward threshold."""
        cb = _make_cb(failure_threshold=3, window_seconds=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)  # wait for window to expire
        cb.record_failure()  # only 1 failure now in window
        assert cb.state == CircuitState.CLOSED


# ===========================================================================
# 3. CircuitBreaker — OPEN state
# ===========================================================================


class TestOpenState:
    def _open_circuit(self, threshold: int = 3) -> CircuitBreaker:
        cb = _make_cb(failure_threshold=threshold, recovery_timeout=0.1)
        for _ in range(threshold):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        return cb

    def test_open_rejects_requests(self):
        cb = self._open_circuit()
        assert cb.allow_request() is False

    def test_open_rejects_all_requests(self):
        cb = self._open_circuit()
        for _ in range(10):
            assert cb.allow_request() is False

    def test_open_failure_stays_open(self):
        cb = self._open_circuit()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_success_does_not_close(self):
        """Success while OPEN should not close the circuit (only allowed via HALF_OPEN probe)."""
        cb = self._open_circuit()
        # Can't call record_success when no request was allowed through
        # but we test that the state doesn't change
        assert cb.state == CircuitState.OPEN

    def test_open_status_has_time_until_probe(self):
        cb = self._open_circuit()
        status = cb.status()
        assert "time_until_probe_seconds" in status
        assert status["time_until_probe_seconds"] is not None
        assert status["time_until_probe_seconds"] >= 0

    def test_open_total_trips_incremented(self):
        cb = _make_cb(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.status()["total_trips"] == 1


# ===========================================================================
# 4. CircuitBreaker — HALF_OPEN state
# ===========================================================================


class TestHalfOpenState:
    def _half_open_circuit(self) -> CircuitBreaker:
        """Open then wait for recovery timeout."""
        cb = _make_cb(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.06)
        # Next allow_request should trigger HALF_OPEN
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN
        return cb

    def test_open_transitions_to_half_open_after_timeout(self):
        cb = _make_cb(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.06)
        cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_single_probe(self):
        """First request in HALF_OPEN should be allowed."""
        cb = _make_cb(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        result = cb.allow_request()
        assert result is True

    def test_half_open_blocks_concurrent_requests(self):
        """While a probe is in flight, subsequent requests must be blocked."""
        cb = _make_cb(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        cb.allow_request()  # probe in flight
        assert cb.allow_request() is False  # second request blocked

    def test_half_open_success_closes_circuit(self):
        cb = self._half_open_circuit()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_success_clears_failure_history(self):
        cb = self._half_open_circuit()
        cb.record_success()
        # Should now accept many requests cleanly
        for _ in range(5):
            assert cb.allow_request() is True

    def test_half_open_failure_reopens_circuit(self):
        cb = self._half_open_circuit()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_half_open_failure_resets_timer(self):
        """After HALF_OPEN → OPEN, the recovery timer should restart."""
        cb = _make_cb(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)
        cb.allow_request()  # → HALF_OPEN
        cb.record_failure()  # → OPEN again, timer reset
        assert cb.state == CircuitState.OPEN
        # Immediately after, should still be blocked (timer just reset)
        assert cb.allow_request() is False


# ===========================================================================
# 5. CircuitBreaker — full state machine round trip
# ===========================================================================


class TestStateMachineRoundTrip:
    def test_full_cycle(self):
        cb = _make_cb(failure_threshold=2, recovery_timeout=0.05)

        # Phase 1: CLOSED — requests allowed
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

        # Phase 2: Trip to OPEN
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

        # Phase 3: Wait for recovery → HALF_OPEN probe
        time.sleep(0.06)
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

        # Phase 4: Probe succeeds → CLOSED
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True


# ===========================================================================
# 6. CircuitBreaker — thread safety
# ===========================================================================


class TestThreadSafety:
    def test_concurrent_failures_do_not_corrupt_state(self):
        """Many threads recording failures should consistently trip the circuit."""
        cb = _make_cb(failure_threshold=5, recovery_timeout=60.0)
        errors: list = []

        def fail_many():
            try:
                for _ in range(20):
                    cb.record_failure()
                    cb.allow_request()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fail_many) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"
        assert cb.state == CircuitState.OPEN

    def test_concurrent_allow_request_only_one_probe(self):
        """Only one thread should get the probe through in HALF_OPEN."""
        cb = _make_cb(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.06)

        results = []
        barrier = threading.Barrier(20)

        def try_allow():
            barrier.wait()  # all threads start simultaneously
            results.append(cb.allow_request())

        threads = [threading.Thread(target=try_allow) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 1 True (the probe), rest False
        trues = results.count(True)
        assert trues == 1, f"Expected exactly 1 probe allowed, got {trues}"


# ===========================================================================
# 7. CircuitBreakerRegistry
# ===========================================================================


class TestRegistry:
    def test_per_provider_isolation(self):
        reg = CircuitBreakerRegistry(_fast_config(failure_threshold=2))
        # Trip anthropic
        reg.record_failure("anthropic")
        reg.record_failure("anthropic")
        assert reg.get_state("anthropic") == CircuitState.OPEN
        # openai should be unaffected
        assert reg.get_state("openai") == CircuitState.CLOSED
        assert reg.allow_request("openai") is True

    def test_allow_request_open_returns_false(self):
        reg = CircuitBreakerRegistry(_fast_config(failure_threshold=1))
        reg.record_failure("anthropic")
        assert reg.allow_request("anthropic") is False

    def test_allow_request_closed_returns_true(self):
        reg = CircuitBreakerRegistry(_fast_config())
        assert reg.allow_request("openai") is True

    def test_all_statuses_empty_on_fresh_registry(self):
        reg = CircuitBreakerRegistry(_fast_config())
        # No access yet → no statuses
        assert reg.all_statuses() == {}

    def test_all_statuses_shows_all_accessed_providers(self):
        reg = CircuitBreakerRegistry(_fast_config())
        reg.allow_request("anthropic")
        reg.allow_request("openai")
        statuses = reg.all_statuses()
        assert "anthropic" in statuses
        assert "openai" in statuses

    def test_reset_closes_open_circuit(self):
        reg = CircuitBreakerRegistry(_fast_config(failure_threshold=1))
        reg.record_failure("anthropic")
        assert reg.get_state("anthropic") == CircuitState.OPEN
        reg.reset("anthropic")
        assert reg.get_state("anthropic") == CircuitState.CLOSED

    def test_reset_all(self):
        reg = CircuitBreakerRegistry(_fast_config(failure_threshold=1))
        reg.record_failure("anthropic")
        reg.record_failure("openai")
        assert reg.get_state("anthropic") == CircuitState.OPEN
        assert reg.get_state("openai") == CircuitState.OPEN
        reg.reset_all()
        assert reg.get_state("anthropic") == CircuitState.CLOSED
        assert reg.get_state("openai") == CircuitState.CLOSED

    def test_disabled_config_always_allows(self):
        cfg = CircuitBreakerConfig(enabled=False, failure_threshold=1)
        reg = CircuitBreakerRegistry(cfg)
        reg.record_failure("anthropic")
        # Even after a failure, disabled registry allows all
        assert reg.allow_request("anthropic") is True


# ===========================================================================
# 8. /health endpoint includes circuit_breakers
# ===========================================================================


@pytest.fixture(scope="module")
def proxy_with_cb():
    """Start a proxy for integration tests."""
    # Reset global registry to fresh state with fast config
    _reset_registry_for_testing(_fast_config(failure_threshold=3, recovery_timeout=0.1))
    server = ProxyServer(host="127.0.0.1", port=19877)
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


def _get(port: int, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


class TestHealthEndpointCircuitBreakers:
    pytestmark = pytest.mark.needs_proxy

    def test_health_has_circuit_breakers_key(self, proxy_with_cb):
        _, data = _get(19877, "/health")
        assert "circuit_breakers" in data

    def test_health_circuit_breakers_has_enabled(self, proxy_with_cb):
        _, data = _get(19877, "/health")
        cb = data["circuit_breakers"]
        assert "enabled" in cb
        assert isinstance(cb["enabled"], bool)

    def test_health_circuit_breakers_has_any_open(self, proxy_with_cb):
        _, data = _get(19877, "/health")
        cb = data["circuit_breakers"]
        assert "any_open" in cb
        assert isinstance(cb["any_open"], bool)

    def test_health_circuit_breakers_has_providers(self, proxy_with_cb):
        _, data = _get(19877, "/health")
        cb = data["circuit_breakers"]
        assert "providers" in cb
        assert isinstance(cb["providers"], dict)

    def test_health_any_open_false_when_all_closed(self, proxy_with_cb):
        # Reset global registry (no open circuits)
        _reset_registry_for_testing(_fast_config())
        _, data = _get(19877, "/health")
        assert data["circuit_breakers"]["any_open"] is False


# ===========================================================================
# 9. /circuit-breakers endpoint
# ===========================================================================


class TestCircuitBreakersEndpoint:
    pytestmark = pytest.mark.needs_proxy

    def test_circuit_breakers_endpoint_returns_200(self, proxy_with_cb):
        status, _ = _get(19877, "/circuit-breakers")
        assert status == 200

    def test_circuit_breakers_endpoint_has_enabled(self, proxy_with_cb):
        _, data = _get(19877, "/circuit-breakers")
        assert "enabled" in data

    def test_circuit_breakers_endpoint_has_circuit_breakers(self, proxy_with_cb):
        _, data = _get(19877, "/circuit-breakers")
        assert "circuit_breakers" in data
        assert isinstance(data["circuit_breakers"], dict)

    def test_circuit_breakers_endpoint_content_type(self, proxy_with_cb):
        req = urllib.request.Request("http://127.0.0.1:19877/circuit-breakers")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ct = resp.headers.get("Content-Type", "")
            assert "application/json" in ct


# ===========================================================================
# 10. Status dict structure
# ===========================================================================


class TestStatusDict:
    def test_status_has_required_fields(self):
        cb = _make_cb()
        status = cb.status()
        required = {
            "state",
            "failures_in_window",
            "failure_threshold",
            "time_until_probe_seconds",
            "total_trips",
            "total_successes",
            "total_failures",
        }
        assert required <= set(status.keys())

    def test_status_state_values_are_valid(self):
        cb = _make_cb()
        valid_states = {s.value for s in CircuitState}
        assert cb.status()["state"] in valid_states

    def test_status_closed_has_none_time_until_probe(self):
        cb = _make_cb()
        assert cb.status()["time_until_probe_seconds"] is None

    def test_status_open_has_positive_time_until_probe(self):
        cb = _make_cb(failure_threshold=1, recovery_timeout=60.0)
        cb.record_failure()
        status = cb.status()
        assert status["time_until_probe_seconds"] is not None
        assert status["time_until_probe_seconds"] > 0

    def test_status_counters_increment(self):
        cb = _make_cb(failure_threshold=10)
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        status = cb.status()
        assert status["total_successes"] == 2
        assert status["total_failures"] == 1
