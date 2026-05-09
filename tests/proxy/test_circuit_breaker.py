"""
tests/proxy/test_circuit_breaker.py

Comprehensive unit tests for tokenpak.proxy.circuit_breaker (AC-TEST-CIRCUIT-01).

Sections:
  A. Regression: reload_config() — TRIX-MTC-07 guard (keep intact)
  B. CircuitState enum
  C. CircuitBreakerConfig
  D. CircuitBreaker state machine
  E. CircuitBreakerRegistry
  F. Singleton helpers (get_circuit_breaker_registry / _reset_registry_for_testing)
  G. Module-level dict-based functions: _provider_for_url, _circuit_check/record
  H. _sanitize_headers
  I. _make_structured_error / _enrich_upstream_error
  J. _rate_limit_check
  K. provider_from_url (OOP version)
  L. RateLimitCircuitBreaker unit tests
  M. RateLimitCircuitBreakerRegistry unit tests
  N. Rate-limit singleton helpers
  O. Concurrent-access tests (edge cases)
"""
from __future__ import annotations

import copy
import os
import threading
import time
from typing import List

import pytest

import tokenpak.proxy.circuit_breaker as _cb_mod
from tokenpak.proxy.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    RateLimitCircuitBreaker,
    RateLimitCircuitBreakerRegistry,
    _enrich_upstream_error,
    _make_structured_error,
    _provider_for_url,
    _reset_registry_for_testing,
    _reset_rl_registry_for_testing,
    _sanitize_headers,
    get_circuit_breaker_registry,
    get_rate_limit_registry,
    provider_from_url,
)

# ===========================================================================
# A. Regression: reload_config() (TRIX-MTC-07 guard — keep intact)
# ===========================================================================

def test_circuit_breaker_registry_reload_config_propagates():
    """
    reload_config() must update self._config AND each existing breaker's _config.
    """
    # Start with threshold=5
    cfg = CircuitBreakerConfig(enabled=True, failure_threshold=5, recovery_timeout=60)
    registry = CircuitBreakerRegistry(config=cfg)

    # Create a breaker so it exists before reload
    _ = registry._get_or_create("anthropic")
    assert registry._breakers["anthropic"]._config.failure_threshold == 5

    # Simulate an env-var change and reload
    env_overrides = {
        "TOKENPAK_CB_ENABLED": "1",
        "TOKENPAK_CB_FAILURE_THRESHOLD": "10",
        "TOKENPAK_CB_RECOVERY_TIMEOUT": "120",
        "TOKENPAK_CB_WINDOW_SECONDS": "60",
    }
    original_env = {k: os.environ.get(k) for k in env_overrides}
    try:
        os.environ.update(env_overrides)
        registry.reload_config()
    finally:
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # Registry and existing breaker both see the new threshold
    assert registry._config.failure_threshold == 10
    assert registry._breakers["anthropic"]._config.failure_threshold == 10


def test_circuit_breaker_registry_reload_config_concurrent_no_error():
    """
    Concurrent allow_request() + reload_config() must not raise or deadlock.
    """
    errors: List[Exception] = []
    registry = CircuitBreakerRegistry(
        config=CircuitBreakerConfig(enabled=True, failure_threshold=5, recovery_timeout=30)
    )
    # Pre-create two breakers
    registry._get_or_create("openai")
    registry._get_or_create("google")

    stop = threading.Event()

    def checker():
        try:
            for _ in range(300):
                registry.allow_request("openai")
                registry.allow_request("google")
        except Exception as exc:
            errors.append(exc)
        finally:
            stop.set()

    def reloader():
        while not stop.is_set():
            registry.reload_config()

    t_check = threading.Thread(target=checker, daemon=True)
    t_reload = threading.Thread(target=reloader, daemon=True)
    t_reload.start()
    t_check.start()
    t_check.join(timeout=5)
    stop.set()
    t_reload.join(timeout=2)

    assert not errors, f"Concurrent reload raised: {errors}"


# ===========================================================================
# B. CircuitState enum
# ===========================================================================

class TestCircuitState:
    def test_closed_value(self):
        assert CircuitState.CLOSED == "closed"
        assert CircuitState.CLOSED.value == "closed"

    def test_open_value(self):
        assert CircuitState.OPEN == "open"
        assert CircuitState.OPEN.value == "open"

    def test_half_open_value(self):
        assert CircuitState.HALF_OPEN == "half_open"
        assert CircuitState.HALF_OPEN.value == "half_open"

    def test_three_distinct_states(self):
        states = {CircuitState.CLOSED, CircuitState.OPEN, CircuitState.HALF_OPEN}
        assert len(states) == 3


# ===========================================================================
# C. CircuitBreakerConfig
# ===========================================================================

class TestCircuitBreakerConfig:
    def test_defaults(self):
        cfg = CircuitBreakerConfig()
        assert cfg.enabled is True
        assert cfg.failure_threshold == 5
        assert cfg.recovery_timeout == 60.0
        assert cfg.window_seconds == 60.0

    def test_custom_values(self):
        cfg = CircuitBreakerConfig(
            enabled=False,
            failure_threshold=3,
            recovery_timeout=30.0,
            window_seconds=120.0,
        )
        assert cfg.enabled is False
        assert cfg.failure_threshold == 3
        assert cfg.recovery_timeout == 30.0
        assert cfg.window_seconds == 120.0

    def test_from_env_defaults(self, monkeypatch):
        for k in ("TOKENPAK_CB_ENABLED", "TOKENPAK_CB_FAILURE_THRESHOLD",
                  "TOKENPAK_CB_RECOVERY_TIMEOUT", "TOKENPAK_CB_WINDOW_SECONDS"):
            monkeypatch.delenv(k, raising=False)
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.enabled is True
        assert cfg.failure_threshold == 5
        assert cfg.recovery_timeout == 60.0
        assert cfg.window_seconds == 60.0

    def test_from_env_overrides(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_CB_ENABLED", "0")
        monkeypatch.setenv("TOKENPAK_CB_FAILURE_THRESHOLD", "3")
        monkeypatch.setenv("TOKENPAK_CB_RECOVERY_TIMEOUT", "15")
        monkeypatch.setenv("TOKENPAK_CB_WINDOW_SECONDS", "30")
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.enabled is False
        assert cfg.failure_threshold == 3
        assert cfg.recovery_timeout == 15.0
        assert cfg.window_seconds == 30.0


# ===========================================================================
# D. CircuitBreaker state machine
# ===========================================================================

def _make_cb(threshold: int = 3, recovery_timeout: float = 0.1, window: float = 60.0) -> CircuitBreaker:
    cfg = CircuitBreakerConfig(
        enabled=True,
        failure_threshold=threshold,
        recovery_timeout=recovery_timeout,
        window_seconds=window,
    )
    return CircuitBreaker("test-provider", cfg)


class TestCircuitBreakerInitialState:
    def test_starts_closed(self):
        cb = _make_cb()
        assert cb.state == CircuitState.CLOSED

    def test_allows_request_when_closed(self):
        cb = _make_cb()
        assert cb.allow_request() is True

    def test_counters_start_at_zero(self):
        cb = _make_cb()
        s = cb.status()
        assert s["total_trips"] == 0
        assert s["total_successes"] == 0
        assert s["total_failures"] == 0
        assert s["failures_in_window"] == 0


class TestCircuitBreakerClosedToOpen:
    def test_failures_below_threshold_stay_closed(self):
        cb = _make_cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_failure_at_threshold_opens_circuit(self):
        cb = _make_cb(threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_rejects_requests(self):
        cb = _make_cb(threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_total_trips_increments_on_open(self):
        cb = _make_cb(threshold=1)
        cb.record_failure()
        assert cb.status()["total_trips"] == 1

    def test_total_failures_increments(self):
        cb = _make_cb(threshold=5)
        cb.record_failure()
        cb.record_failure()
        assert cb.status()["total_failures"] == 2

    def test_failures_in_window_count(self):
        cb = _make_cb(threshold=5, window=60.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.status()["failures_in_window"] == 2


class TestCircuitBreakerOpenToHalfOpen:
    def test_open_transitions_to_half_open_after_recovery_timeout(self):
        cb = _make_cb(threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # Before timeout — still rejected
        assert cb.allow_request() is False
        # After timeout — probe allowed
        time.sleep(0.07)
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_allows_exactly_one_probe(self):
        cb = _make_cb(threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.07)
        # First call: allowed (probe)
        assert cb.allow_request() is True
        # Second call: rejected (probe already in flight)
        assert cb.allow_request() is False


class TestCircuitBreakerHalfOpen:
    def _make_half_open(self) -> CircuitBreaker:
        cb = _make_cb(threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.07)
        cb.allow_request()  # transitions to HALF_OPEN, sets probe_in_flight
        return cb

    def test_success_in_half_open_closes_circuit(self):
        cb = self._make_half_open()
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_success_clears_failures_in_window(self):
        cb = self._make_half_open()
        cb.record_success()
        assert cb.status()["failures_in_window"] == 0

    def test_failure_in_half_open_reopens_circuit(self):
        cb = self._make_half_open()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_failure_in_half_open_resets_probe_flag(self):
        cb = self._make_half_open()
        cb.record_failure()
        # After re-open, probe_in_flight should be cleared
        assert cb._probe_in_flight is False

    def test_total_successes_increments(self):
        cb = self._make_half_open()
        cb.record_success()
        assert cb.status()["total_successes"] == 1


class TestCircuitBreakerRollingWindow:
    def test_old_failures_expire_from_window(self):
        cb = _make_cb(threshold=5, recovery_timeout=60.0, window=0.05)
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.07)
        # Old failures expired; new failure count is 1
        cb.record_failure()
        assert cb.status()["failures_in_window"] == 1
        assert cb.state == CircuitState.CLOSED  # below threshold of 5

    def test_failures_in_window_after_expiry_dont_trip(self):
        cb = _make_cb(threshold=3, window=0.05)
        for _ in range(2):
            cb.record_failure()
        time.sleep(0.07)
        # The 2 old failures expire; add 2 more — still below threshold
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerStatus:
    def test_status_closed_has_no_time_until_probe(self):
        cb = _make_cb()
        s = cb.status()
        assert s["state"] == "closed"
        assert s["time_until_probe_seconds"] is None

    def test_status_open_has_time_until_probe(self):
        cb = _make_cb(threshold=1, recovery_timeout=60.0)
        cb.record_failure()
        s = cb.status()
        assert s["state"] == "open"
        assert s["time_until_probe_seconds"] is not None
        assert 0.0 < s["time_until_probe_seconds"] <= 60.0

    def test_status_includes_threshold(self):
        cb = _make_cb(threshold=7)
        assert cb.status()["failure_threshold"] == 7


class TestCircuitBreakerReset:
    def test_reset_from_open_returns_to_closed(self):
        cb = _make_cb(threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_reset_clears_failure_times(self):
        cb = _make_cb(threshold=1)
        cb.record_failure()
        cb.reset()
        assert cb.status()["failures_in_window"] == 0

    def test_reset_allows_requests_after_open(self):
        cb = _make_cb(threshold=1)
        cb.record_failure()
        cb.reset()
        assert cb.allow_request() is True

    def test_reset_from_half_open(self):
        cb = _make_cb(threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.07)
        cb.allow_request()  # → HALF_OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerDisabled:
    def test_disabled_always_allows_request(self):
        cfg = CircuitBreakerConfig(enabled=False, failure_threshold=1)
        cb = CircuitBreaker("test", cfg)
        cb.record_failure()  # would open if enabled
        assert cb.allow_request() is True

    def test_disabled_failures_dont_trip(self):
        cfg = CircuitBreakerConfig(enabled=False, failure_threshold=1)
        cb = CircuitBreaker("test", cfg)
        for _ in range(10):
            cb.record_failure()
        # State is still CLOSED (failures recorded but circuit logic skipped by allow_request)
        # Note: record_failure() still runs internal logic, but allow_request ignores it
        assert cb.allow_request() is True


class TestCircuitBreakerZeroThreshold:
    def test_zero_threshold_opens_on_first_failure(self):
        cb = _make_cb(threshold=0)
        # With threshold=0, failures >= 0 is always True — first failure trips it
        cb.record_failure()
        assert cb.state == CircuitState.OPEN


# ===========================================================================
# E. CircuitBreakerRegistry
# ===========================================================================

class TestCircuitBreakerRegistryOperations:
    def test_creates_breaker_on_first_access(self):
        reg = CircuitBreakerRegistry(config=CircuitBreakerConfig())
        assert "new-provider" not in reg._breakers
        reg._get_or_create("new-provider")
        assert "new-provider" in reg._breakers

    def test_reuses_existing_breaker(self):
        reg = CircuitBreakerRegistry(config=CircuitBreakerConfig())
        b1 = reg._get_or_create("anthropic")
        b2 = reg._get_or_create("anthropic")
        assert b1 is b2

    def test_allow_request_delegates(self):
        cfg = CircuitBreakerConfig(enabled=True, failure_threshold=1)
        reg = CircuitBreakerRegistry(config=cfg)
        assert reg.allow_request("openai") is True
        reg.record_failure("openai")
        assert reg.allow_request("openai") is False

    def test_record_success_closes_half_open(self):
        cfg = CircuitBreakerConfig(enabled=True, failure_threshold=1, recovery_timeout=0.05)
        reg = CircuitBreakerRegistry(config=cfg)
        reg.record_failure("google")
        time.sleep(0.07)
        reg.allow_request("google")  # → HALF_OPEN
        reg.record_success("google")
        assert reg.get_state("google") == CircuitState.CLOSED

    def test_get_state_returns_current_state(self):
        reg = CircuitBreakerRegistry(config=CircuitBreakerConfig(failure_threshold=1))
        assert reg.get_state("anthropic") == CircuitState.CLOSED
        reg.record_failure("anthropic")
        assert reg.get_state("anthropic") == CircuitState.OPEN

    def test_reset_single_provider(self):
        cfg = CircuitBreakerConfig(failure_threshold=1)
        reg = CircuitBreakerRegistry(config=cfg)
        reg.record_failure("openai")
        assert reg.get_state("openai") == CircuitState.OPEN
        reg.reset("openai")
        assert reg.get_state("openai") == CircuitState.CLOSED

    def test_reset_all(self):
        cfg = CircuitBreakerConfig(failure_threshold=1)
        reg = CircuitBreakerRegistry(config=cfg)
        for p in ("anthropic", "openai", "google"):
            reg.record_failure(p)
            assert reg.get_state(p) == CircuitState.OPEN
        reg.reset_all()
        for p in ("anthropic", "openai", "google"):
            assert reg.get_state(p) == CircuitState.CLOSED

    def test_all_statuses_returns_dict_per_provider(self):
        cfg = CircuitBreakerConfig(failure_threshold=5)
        reg = CircuitBreakerRegistry(config=cfg)
        reg.allow_request("anthropic")
        reg.allow_request("openai")
        statuses = reg.all_statuses()
        assert "anthropic" in statuses
        assert "openai" in statuses
        assert "state" in statuses["anthropic"]

    def test_enabled_property_reflects_config(self):
        reg = CircuitBreakerRegistry(config=CircuitBreakerConfig(enabled=True))
        assert reg.enabled is True
        reg2 = CircuitBreakerRegistry(config=CircuitBreakerConfig(enabled=False))
        assert reg2.enabled is False

    def test_isolated_per_provider_state(self):
        cfg = CircuitBreakerConfig(failure_threshold=1)
        reg = CircuitBreakerRegistry(config=cfg)
        reg.record_failure("anthropic")
        # openai should still be closed
        assert reg.get_state("anthropic") == CircuitState.OPEN
        assert reg.get_state("openai") == CircuitState.CLOSED


# ===========================================================================
# F. Singleton helpers
# ===========================================================================

class TestCircuitBreakerSingleton:
    def test_get_circuit_breaker_registry_returns_same_instance(self):
        r1 = get_circuit_breaker_registry()
        r2 = get_circuit_breaker_registry()
        assert r1 is r2

    def test_reset_registry_for_testing_returns_new_instance(self):
        original = get_circuit_breaker_registry()
        new_reg = _reset_registry_for_testing()
        assert new_reg is not original
        # Subsequent call returns the new instance
        assert get_circuit_breaker_registry() is new_reg

    def test_reset_registry_accepts_custom_config(self):
        cfg = CircuitBreakerConfig(failure_threshold=99)
        reg = _reset_registry_for_testing(config=cfg)
        assert reg._config.failure_threshold == 99


# ===========================================================================
# G. Module-level dict-based functions
# ===========================================================================

@pytest.fixture()
def isolated_provider_circuits():
    """Save and restore module-level _provider_circuits dict."""
    saved = copy.deepcopy(_cb_mod._provider_circuits)
    yield _cb_mod._provider_circuits
    _cb_mod._provider_circuits.clear()
    _cb_mod._provider_circuits.update(saved)


class TestModuleProviderForUrl:
    def test_anthropic(self):
        assert _provider_for_url("https://api.anthropic.com/v1/messages") == "anthropic"

    def test_openai(self):
        assert _provider_for_url("https://api.openai.com/v1/chat/completions") == "openai"

    def test_google(self):
        assert _provider_for_url("https://generativelanguage.googleapis.com/v1/models") == "google"

    def test_unknown_returns_empty(self):
        assert _provider_for_url("https://example.com/api") == ""

    def test_empty_url_returns_empty(self):
        assert _provider_for_url("") == ""


class TestModuleCircuitCheck:
    def test_check_unknown_provider_returns_false(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_check
        assert _circuit_check("nonexistent") is False

    def test_check_closed_circuit_returns_false(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_check
        isolated_provider_circuits["anthropic"]["open"] = False
        assert _circuit_check("anthropic") is False

    def test_check_open_circuit_within_cooldown_returns_true(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_check
        isolated_provider_circuits["anthropic"]["open"] = True
        isolated_provider_circuits["anthropic"]["last_failure"] = time.time()
        isolated_provider_circuits["anthropic"]["cooldown"] = 120
        assert _circuit_check("anthropic") is True

    def test_check_auto_closes_after_cooldown(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_check
        isolated_provider_circuits["anthropic"]["open"] = True
        isolated_provider_circuits["anthropic"]["last_failure"] = time.time() - 200
        isolated_provider_circuits["anthropic"]["cooldown"] = 60
        # Cooldown expired — should return False and reset open flag
        assert _circuit_check("anthropic") is False
        assert isolated_provider_circuits["anthropic"]["open"] is False

    def test_check_empty_provider_returns_false(self):
        from tokenpak.proxy.circuit_breaker import _circuit_check
        assert _circuit_check("") is False


class TestModuleCircuitRecord:
    def test_record_failure_increments_count(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_record_failure
        before = isolated_provider_circuits["openai"]["failures"]
        _circuit_record_failure("openai")
        assert isolated_provider_circuits["openai"]["failures"] == before + 1

    def test_record_failure_opens_at_threshold(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_record_failure
        isolated_provider_circuits["openai"]["failures"] = 0
        isolated_provider_circuits["openai"]["open"] = False
        isolated_provider_circuits["openai"]["threshold"] = 3
        _circuit_record_failure("openai")
        _circuit_record_failure("openai")
        _circuit_record_failure("openai")
        assert isolated_provider_circuits["openai"]["open"] is True

    def test_record_failure_unknown_provider_noop(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_record_failure
        _circuit_record_failure("nonexistent")  # must not raise

    def test_record_success_resets_failures_and_closes(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_record_success
        isolated_provider_circuits["google"]["failures"] = 4
        isolated_provider_circuits["google"]["open"] = True
        _circuit_record_success("google")
        assert isolated_provider_circuits["google"]["failures"] == 0
        assert isolated_provider_circuits["google"]["open"] is False

    def test_record_success_unknown_provider_noop(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_record_success
        _circuit_record_success("nonexistent")  # must not raise

    def test_record_failure_empty_provider_noop(self, isolated_provider_circuits):
        from tokenpak.proxy.circuit_breaker import _circuit_record_failure
        _circuit_record_failure("")  # must not raise


# ===========================================================================
# H. _sanitize_headers
# ===========================================================================

class TestSanitizeHeaders:
    def test_strips_blocked_hop_by_hop_headers(self):
        raw = {
            "host": "api.anthropic.com",
            "connection": "keep-alive",
            "transfer-encoding": "chunked",
            "x-api-key": "sk-abc",
        }
        result = _sanitize_headers(raw)
        assert "host" not in result
        assert "connection" not in result
        assert "transfer-encoding" not in result
        assert result["x-api-key"] == "sk-abc"

    def test_passes_through_safe_headers(self):
        raw = {
            "content-type": "application/json",
            "authorization": "Bearer token",
            "anthropic-version": "2023-06-01",
        }
        result = _sanitize_headers(raw)
        assert result["content-type"] == "application/json"
        assert result["authorization"] == "Bearer token"
        assert result["anthropic-version"] == "2023-06-01"

    def test_strips_proxy_headers(self):
        raw = {
            "proxy-authorization": "Basic abc",
            "proxy-connection": "keep-alive",
            "x-forwarded-for": "1.2.3.4",
            "x-tokenpak-bypass": "1",
        }
        result = _sanitize_headers(raw)
        assert not result

    def test_empty_headers_returns_empty_dict(self):
        assert _sanitize_headers({}) == {}

    def test_case_insensitive_blocking(self):
        raw = {"Host": "example.com", "Content-Type": "application/json"}
        result = _sanitize_headers(raw)
        # The dict uses lower() comparison — keyed by original case
        # "Host" lower → "host" which is blocked
        assert "Host" not in result
        # "Content-Type" lower → "content-type" which is not blocked
        assert "Content-Type" in result


# ===========================================================================
# I. _make_structured_error / _enrich_upstream_error
# ===========================================================================

class TestMakeStructuredError:
    def test_returns_correct_shape(self):
        result = _make_structured_error("budget_exceeded", "Over budget", "Reduce spend")
        assert result["error"] == "budget_exceeded"
        assert result["message"] == "Over budget"
        assert result["suggestion"] == "Reduce spend"

    def test_extra_kwargs_included(self):
        result = _make_structured_error("test_type", "msg", "hint", limit_usd=10.0)
        assert result["limit_usd"] == 10.0

    def test_status_not_in_result(self):
        # status is a kwarg to the function but is not stored in the returned dict
        result = _make_structured_error("t", "m", "s", status=400)
        assert "status" not in result


class TestEnrichUpstreamError:
    def _wrap(self, err_type: str, message: str = "") -> dict:
        return {"error": {"type": err_type, "message": message}}

    def test_401_adds_hint(self):
        normalized = self._wrap("authentication_error")
        result = _enrich_upstream_error(normalized, 401)
        assert "hint" in result["error"]
        assert "API key" in result["error"]["hint"]

    def test_404_model_not_found_adds_suggestion(self):
        normalized = self._wrap("model_not_found")
        result = _enrich_upstream_error(normalized, 404)
        assert "hint" in result["error"]

    def test_429_rate_limit_with_retry_after_header(self):
        normalized = self._wrap("rate_limit_error")
        result = _enrich_upstream_error(normalized, 429, retry_after_header="30")
        assert result["error"]["retry_after"] == 30
        assert "hint" in result["error"]

    def test_429_rate_limit_invalid_retry_after_stored_as_string(self):
        normalized = self._wrap("rate_limit_error")
        result = _enrich_upstream_error(normalized, 429, retry_after_header="not-a-number")
        # Stored as-is when not parseable as float
        assert result["error"]["retry_after"] == "not-a-number"

    def test_400_invalid_request_messages_field(self):
        normalized = self._wrap("invalid_request_error", "messages field missing")
        result = _enrich_upstream_error(normalized, 400)
        assert result["error"]["field"] == "messages"

    def test_400_invalid_request_model_field(self):
        normalized = self._wrap("invalid_request_error", "model is required")
        result = _enrich_upstream_error(normalized, 400)
        assert result["error"]["field"] == "model"

    def test_400_invalid_json_type(self):
        normalized = self._wrap("invalid_json")
        result = _enrich_upstream_error(normalized, 400)
        assert "JSON" in result["error"]["hint"]

    def test_502_provider_unavailable(self):
        normalized = self._wrap("provider_unavailable")
        result = _enrich_upstream_error(normalized, 502)
        assert "hint" in result["error"]

    def test_503_adds_type_if_missing(self):
        normalized = {"error": {"message": "down"}}
        result = _enrich_upstream_error(normalized, 503)
        assert result["error"]["type"] == "provider_unavailable"

    def test_existing_hint_not_overwritten(self):
        normalized = {"error": {"type": "authentication_error", "hint": "my-existing-hint"}}
        result = _enrich_upstream_error(normalized, 401)
        assert result["error"]["hint"] == "my-existing-hint"

    def test_returns_normalized_dict(self):
        normalized = self._wrap("authentication_error")
        result = _enrich_upstream_error(normalized, 401)
        assert result is normalized  # mutates and returns same dict


# ===========================================================================
# J. _rate_limit_check
# ===========================================================================

@pytest.fixture()
def isolated_rate_buckets():
    """Clear _rate_buckets before and after each test to prevent bleed."""
    _cb_mod._rate_buckets.clear()
    yield _cb_mod._rate_buckets
    _cb_mod._rate_buckets.clear()


class TestRateLimitCheck:
    def test_allows_first_request(self, isolated_rate_buckets):
        from tokenpak.proxy.circuit_breaker import _rate_limit_check
        assert _rate_limit_check("192.168.1.1") is True

    def test_zero_rpm_always_allows(self, isolated_rate_buckets, monkeypatch):
        monkeypatch.setattr(_cb_mod, "_RATE_LIMIT_RPM", 0)
        from tokenpak.proxy.circuit_breaker import _rate_limit_check
        for _ in range(200):
            assert _rate_limit_check("10.0.0.1") is True

    def test_throttles_after_token_exhaustion(self, isolated_rate_buckets, monkeypatch):
        monkeypatch.setattr(_cb_mod, "_RATE_LIMIT_RPM", 3)
        from tokenpak.proxy.circuit_breaker import _rate_limit_check
        ip = "10.0.0.2"
        # First 3 requests — within token budget
        results = [_rate_limit_check(ip) for _ in range(3)]
        assert all(results), "First 3 requests should be allowed"
        # 4th request — token bucket empty
        assert _rate_limit_check(ip) is False

    def test_independent_buckets_per_ip(self, isolated_rate_buckets, monkeypatch):
        monkeypatch.setattr(_cb_mod, "_RATE_LIMIT_RPM", 1)
        from tokenpak.proxy.circuit_breaker import _rate_limit_check
        assert _rate_limit_check("10.1.1.1") is True
        assert _rate_limit_check("10.1.1.1") is False  # exhausted
        assert _rate_limit_check("10.1.1.2") is True   # different IP, fresh bucket


# ===========================================================================
# K. provider_from_url (OOP version)
# ===========================================================================

class TestProviderFromUrl:
    def test_anthropic(self):
        assert provider_from_url("https://api.anthropic.com/v1/messages") == "anthropic"

    def test_openai(self):
        assert provider_from_url("https://api.openai.com/v1/chat") == "openai"

    def test_google_googleapis(self):
        assert provider_from_url("https://generativelanguage.googleapis.com/v1") == "google"

    def test_google_generativelanguage(self):
        assert provider_from_url("https://generativelanguage.google.com/v1") == "google"

    def test_azure(self):
        assert provider_from_url("https://myinstance.azure.com/openai/deployments") == "azure"

    def test_ollama(self):
        assert provider_from_url("http://localhost:11434/api/generate?ollama=1") == "ollama"

    def test_groq(self):
        assert provider_from_url("https://api.groq.com/openai/v1") == "groq"

    def test_together(self):
        assert provider_from_url("https://api.together.xyz/v1") == "together"

    def test_cohere(self):
        assert provider_from_url("https://api.cohere.com/v1") == "cohere"

    def test_unknown_extracts_hostname(self):
        result = provider_from_url("https://custom.myhost.io/api")
        assert result == "custom.myhost.io"

    def test_case_insensitive(self):
        assert provider_from_url("HTTPS://API.ANTHROPIC.COM/V1") == "anthropic"


# ===========================================================================
# L. RateLimitCircuitBreaker unit tests
# ===========================================================================

class TestRateLimitCircuitBreakerUnit:
    def test_initial_state_closed(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)
        assert cb.is_open() is False

    def test_opens_at_threshold(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=3, cooldown_sec=30)
        for _ in range(3):
            cb.record_429()
        assert cb.is_open() is True

    def test_stays_closed_below_threshold(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=3, cooldown_sec=30)
        cb.record_429()
        cb.record_429()
        assert cb.is_open() is False

    def test_old_429s_expire_from_window(self):
        cb = RateLimitCircuitBreaker(window_sec=0.05, threshold=3, cooldown_sec=30)
        cb.record_429()
        cb.record_429()
        time.sleep(0.07)
        # Old entries expired; adding one more should not trip (count = 1, threshold = 3)
        cb.record_429()
        assert cb.is_open() is False

    def test_auto_closes_after_cooldown(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=2, cooldown_sec=0.05)
        cb.record_429()
        cb.record_429()
        assert cb.is_open() is True
        time.sleep(0.07)
        assert cb.is_open() is False

    def test_reset_closes_open_circuit(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=1, cooldown_sec=30)
        cb.record_429()
        assert cb.is_open() is True
        cb.reset()
        assert cb.is_open() is False

    def test_reset_clears_429_times(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=30)
        cb.record_429()
        cb.record_429()
        cb.reset()
        assert cb.status()["recent_429s_in_window"] == 0

    def test_status_dict_shape(self):
        cb = RateLimitCircuitBreaker(window_sec=10, threshold=3, cooldown_sec=5)
        s = cb.status()
        assert "is_open" in s
        assert "window_sec" in s
        assert "threshold" in s
        assert "cooldown_sec" in s
        assert "recent_429s_in_window" in s
        assert "cooldown_remaining_sec" in s

    def test_status_cooldown_remaining_when_open(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=1, cooldown_sec=10.0)
        cb.record_429()
        s = cb.status()
        assert s["cooldown_remaining_sec"] is not None
        assert 0.0 < s["cooldown_remaining_sec"] <= 10.0

    def test_status_cooldown_remaining_none_when_closed(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=5, cooldown_sec=10.0)
        s = cb.status()
        assert s["cooldown_remaining_sec"] is None

    def test_zero_threshold_opens_immediately(self):
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=0, cooldown_sec=30)
        cb.record_429()
        # With threshold=0, len(deque) >= 0 is always True after first record
        assert cb.is_open() is True


# ===========================================================================
# M. RateLimitCircuitBreakerRegistry unit tests
# ===========================================================================

class TestRateLimitCircuitBreakerRegistryUnit:
    def test_creates_breaker_per_provider(self):
        reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=5, cooldown_sec=30)
        reg.record_429("anthropic")
        assert "anthropic" in reg._breakers
        assert "openai" not in reg._breakers

    def test_separate_state_per_provider(self):
        reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=1, cooldown_sec=30)
        reg.record_429("anthropic")
        assert reg.is_open("anthropic") is True
        assert reg.is_open("openai") is False

    def test_record_429_opens_circuit(self):
        reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=1, cooldown_sec=30)
        reg.record_429("openai")
        assert reg.is_open("openai") is True

    def test_reset_single_closes_circuit(self):
        reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=1, cooldown_sec=30)
        reg.record_429("google")
        assert reg.is_open("google") is True
        reg.reset("google")
        assert reg.is_open("google") is False

    def test_reset_all_closes_all_circuits(self):
        reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=1, cooldown_sec=30)
        for p in ("anthropic", "openai", "google"):
            reg.record_429(p)
        assert all(reg.is_open(p) for p in ("anthropic", "openai", "google"))
        reg.reset_all()
        assert all(not reg.is_open(p) for p in ("anthropic", "openai", "google"))

    def test_all_statuses_returns_dict_per_provider(self):
        reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=5, cooldown_sec=30)
        reg.record_429("anthropic")
        reg.record_429("openai")
        statuses = reg.all_statuses()
        assert "anthropic" in statuses
        assert "openai" in statuses
        assert "is_open" in statuses["anthropic"]

    def test_all_statuses_empty_when_no_providers(self):
        reg = RateLimitCircuitBreakerRegistry()
        assert reg.all_statuses() == {}

    def test_reuses_existing_breaker_instance(self):
        reg = RateLimitCircuitBreakerRegistry(window_sec=60, threshold=5, cooldown_sec=30)
        b1 = reg._get_or_create("anthropic")
        b2 = reg._get_or_create("anthropic")
        assert b1 is b2


# ===========================================================================
# N. Rate-limit singleton helpers
# ===========================================================================

class TestRateLimitSingleton:
    def test_get_rate_limit_registry_returns_same_instance(self):
        r1 = get_rate_limit_registry()
        r2 = get_rate_limit_registry()
        assert r1 is r2

    def test_reset_rl_registry_for_testing_creates_new_instance(self):
        original = get_rate_limit_registry()
        new_reg = _reset_rl_registry_for_testing()
        assert new_reg is not original
        assert get_rate_limit_registry() is new_reg

    def test_reset_rl_registry_accepts_custom_params(self):
        reg = _reset_rl_registry_for_testing(window_sec=10, threshold=2, cooldown_sec=5)
        assert reg._window_sec == 10
        assert reg._threshold == 2
        assert reg._cooldown_sec == 5


# ===========================================================================
# O. Concurrent-access edge cases
# ===========================================================================

class TestConcurrentAccess:
    def test_circuit_breaker_concurrent_failures_no_data_race(self):
        """Rapid concurrent record_failure() calls must not corrupt state."""
        cb = _make_cb(threshold=100, recovery_timeout=60.0, window=60.0)
        errors: List[Exception] = []

        def spam_failures():
            try:
                for _ in range(50):
                    cb.record_failure()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=spam_failures) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        # 10*50 = 500 total failures — all should be counted
        assert cb.status()["total_failures"] == 500

    def test_circuit_breaker_concurrent_reset_no_deadlock(self):
        """Concurrent reset() + record_failure() must not deadlock."""
        cb = _make_cb(threshold=5)
        done = threading.Event()
        errors: List[Exception] = []

        def record_loop():
            try:
                for _ in range(200):
                    cb.record_failure()
                    cb.record_success()
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        def reset_loop():
            while not done.is_set():
                cb.reset()

        t1 = threading.Thread(target=record_loop)
        t2 = threading.Thread(target=reset_loop, daemon=True)
        t2.start()
        t1.start()
        t1.join(timeout=5)
        done.set()
        t2.join(timeout=2)
        assert not errors

    def test_rate_limit_circuit_breaker_concurrent_record_429(self):
        """Concurrent record_429() calls must not corrupt window deque."""
        cb = RateLimitCircuitBreaker(window_sec=60, threshold=1000, cooldown_sec=30)
        errors: List[Exception] = []

        def spam():
            try:
                for _ in range(100):
                    cb.record_429()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=spam) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert cb.status()["recent_429s_in_window"] == 500
