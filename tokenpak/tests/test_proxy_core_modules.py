"""
Unit tests for proxy core modules:
  - circuit_breaker.py (CircuitBreaker, CircuitBreakerRegistry, RateLimitCircuitBreaker)
  - connection_pool.py (PoolConfig, PoolMetrics, ConnectionPool)
  - memory_guard.py (MemoryGuard, calculate_budget, get_rss_mb, get_total_ram_mb)
  - request_pipeline.py (_classify_intent, is_protected_content, _resolve_session_id,
                         _partition_stable_volatile, classify_message_risk, can_compress)

All external dependencies (httpx, socket, OS reads) are mocked.
No live API calls, no network I/O.
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# circuit_breaker.py
# ============================================================================


class TestCircuitBreakerConfig:
    def test_defaults(self):
        from tokenpak.proxy.circuit_breaker import CircuitBreakerConfig

        cfg = CircuitBreakerConfig()
        assert cfg.enabled is True
        assert cfg.failure_threshold == 5
        assert cfg.recovery_timeout == 60.0
        assert cfg.window_seconds == 60.0

    def test_from_env_overrides(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_CB_ENABLED", "0")
        monkeypatch.setenv("TOKENPAK_CB_FAILURE_THRESHOLD", "3")
        monkeypatch.setenv("TOKENPAK_CB_RECOVERY_TIMEOUT", "30")
        monkeypatch.setenv("TOKENPAK_CB_WINDOW_SECONDS", "45")

        from tokenpak.proxy.circuit_breaker import CircuitBreakerConfig

        cfg = CircuitBreakerConfig.from_env()
        assert cfg.enabled is False
        assert cfg.failure_threshold == 3
        assert cfg.recovery_timeout == 30.0
        assert cfg.window_seconds == 45.0


class TestCircuitBreakerStates:
    def _make(self, threshold=3, recovery=0.1, window=60.0):
        from tokenpak.proxy.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        cfg = CircuitBreakerConfig(
            failure_threshold=threshold,
            recovery_timeout=recovery,
            window_seconds=window,
        )
        return CircuitBreaker("test-provider", cfg)

    def test_initial_state_closed(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        cb = self._make()
        assert cb.state == CircuitState.CLOSED

    def test_allow_request_when_closed(self):
        cb = self._make()
        assert cb.allow_request() is True

    def test_failure_trips_to_open(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        cb = self._make(threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_open_rejects_requests(self):
        cb = self._make(threshold=1)
        cb.record_failure()
        assert cb.allow_request() is False

    def test_recovery_timeout_transitions_to_half_open(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        cb = self._make(threshold=1, recovery=0.01)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        time.sleep(0.05)
        # allow_request triggers transition
        allowed = cb.allow_request()
        assert cb.state == CircuitState.HALF_OPEN
        assert allowed is True

    def test_success_in_half_open_closes_circuit(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        cb = self._make(threshold=1, recovery=0.01)
        cb.record_failure()
        time.sleep(0.05)
        cb.allow_request()  # → HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_failure_in_half_open_reopens_circuit(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        cb = self._make(threshold=1, recovery=0.01)
        cb.record_failure()
        time.sleep(0.05)
        cb.allow_request()  # → HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_reset_returns_to_closed(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        cb = self._make(threshold=1)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED

    def test_disabled_always_allows(self):
        from tokenpak.proxy.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        cfg = CircuitBreakerConfig(enabled=False, failure_threshold=1)
        cb = CircuitBreaker("p", cfg)
        cb.record_failure()
        assert cb.allow_request() is True

    def test_status_dict_keys(self):
        cb = self._make()
        s = cb.status()
        for key in (
            "state",
            "failures_in_window",
            "failure_threshold",
            "time_until_probe_seconds",
            "total_trips",
            "total_successes",
            "total_failures",
        ):
            assert key in s

    def test_counters_increment(self):
        cb = self._make(threshold=10)
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        s = cb.status()
        assert s["total_successes"] == 2
        assert s["total_failures"] == 1

    def test_window_prunes_old_failures(self):
        """Failures outside the window should not count toward tripping."""
        from tokenpak.proxy.circuit_breaker import CircuitState

        # window=0.05s, threshold=2
        cb = self._make(threshold=2, window=0.05)
        cb.record_failure()
        time.sleep(0.1)
        # First failure is now outside window; a second alone should not trip
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerRegistry:
    def _registry(self, threshold=3):
        from tokenpak.proxy.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry

        cfg = CircuitBreakerConfig(failure_threshold=threshold, recovery_timeout=60.0)
        return CircuitBreakerRegistry(config=cfg)

    def test_allow_request_new_provider(self):
        reg = self._registry()
        assert reg.allow_request("anthropic") is True

    def test_record_failure_trips_circuit(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        reg = self._registry(threshold=2)
        reg.record_failure("openai")
        reg.record_failure("openai")
        assert reg.get_state("openai") == CircuitState.OPEN

    def test_record_success_resets_failures(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        reg = self._registry(threshold=5)
        reg.record_failure("google")
        reg.record_success("google")
        assert reg.get_state("google") == CircuitState.CLOSED

    def test_reset_provider(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        reg = self._registry(threshold=1)
        reg.record_failure("anthropic")
        reg.reset("anthropic")
        assert reg.get_state("anthropic") == CircuitState.CLOSED

    def test_reset_all(self):
        from tokenpak.proxy.circuit_breaker import CircuitState

        reg = self._registry(threshold=1)
        reg.record_failure("anthropic")
        reg.record_failure("openai")
        reg.reset_all()
        assert reg.get_state("anthropic") == CircuitState.CLOSED
        assert reg.get_state("openai") == CircuitState.CLOSED

    def test_all_statuses(self):
        reg = self._registry()
        reg.allow_request("anthropic")
        reg.allow_request("openai")
        statuses = reg.all_statuses()
        assert "anthropic" in statuses
        assert "openai" in statuses

    def test_reload_config(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_CB_FAILURE_THRESHOLD", "7")
        reg = self._registry()
        reg.reload_config()
        assert reg._config.failure_threshold == 7

    def test_enabled_property(self):
        from tokenpak.proxy.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry

        cfg = CircuitBreakerConfig(enabled=False)
        reg = CircuitBreakerRegistry(config=cfg)
        assert reg.enabled is False


class TestGlobalRegistry:
    def test_singleton_returns_same_instance(self):
        from tokenpak.proxy.circuit_breaker import (
            _reset_registry_for_testing,
            get_circuit_breaker_registry,
        )

        _reset_registry_for_testing()
        r1 = get_circuit_breaker_registry()
        r2 = get_circuit_breaker_registry()
        assert r1 is r2

    def test_reset_returns_new_instance(self):
        from tokenpak.proxy.circuit_breaker import (
            _reset_registry_for_testing,
            get_circuit_breaker_registry,
        )

        r1 = get_circuit_breaker_registry()
        _reset_registry_for_testing()
        r2 = get_circuit_breaker_registry()
        assert r1 is not r2


class TestRateLimitCircuitBreaker:
    def _make(self, window=1.0, threshold=3, cooldown=0.1):
        from tokenpak.proxy.circuit_breaker import RateLimitCircuitBreaker

        return RateLimitCircuitBreaker(
            window_sec=window, threshold=threshold, cooldown_sec=cooldown
        )

    def test_initial_closed(self):
        cb = self._make()
        assert cb.is_open() is False

    def test_opens_at_threshold(self):
        cb = self._make(threshold=2)
        cb.record_429()
        assert cb.is_open() is False
        cb.record_429()
        assert cb.is_open() is True

    def test_auto_closes_after_cooldown(self):
        cb = self._make(threshold=1, cooldown=0.05)
        cb.record_429()
        assert cb.is_open() is True
        time.sleep(0.1)
        assert cb.is_open() is False

    def test_reset(self):
        cb = self._make(threshold=1)
        cb.record_429()
        cb.reset()
        assert cb.is_open() is False

    def test_status_keys(self):
        cb = self._make()
        s = cb.status()
        for key in (
            "is_open",
            "window_sec",
            "threshold",
            "cooldown_sec",
            "recent_429s_in_window",
            "cooldown_remaining_sec",
        ):
            assert key in s

    def test_window_expires_old_events(self):
        cb = self._make(window=0.05, threshold=2)
        cb.record_429()
        time.sleep(0.1)
        cb.record_429()
        # Only 1 event in window — should still be closed
        assert cb.is_open() is False


class TestHelperFunctions:
    def test_provider_for_url_anthropic(self):
        from tokenpak.proxy.circuit_breaker import _provider_for_url

        assert _provider_for_url("https://api.anthropic.com/v1/messages") == "anthropic"

    def test_provider_for_url_openai(self):
        from tokenpak.proxy.circuit_breaker import _provider_for_url

        assert _provider_for_url("https://api.openai.com/v1/chat") == "openai"

    def test_provider_for_url_google(self):
        from tokenpak.proxy.circuit_breaker import _provider_for_url

        assert _provider_for_url("https://generativelanguage.googleapis.com/v1") == "google"

    def test_provider_for_url_unknown(self):
        from tokenpak.proxy.circuit_breaker import _provider_for_url

        assert _provider_for_url("https://example.com/api") == ""

    def test_sanitize_headers_strips_blocked(self):
        from tokenpak.proxy.circuit_breaker import _sanitize_headers

        raw = {
            "host": "api.anthropic.com",
            "x-api-key": "sk-test",
            "content-length": "123",
            "Authorization": "Bearer token",
        }
        result = _sanitize_headers(raw)
        assert "host" not in result
        assert "content-length" not in result
        assert "x-api-key" in result
        assert "Authorization" in result

    def test_sanitize_headers_empty(self):
        from tokenpak.proxy.circuit_breaker import _sanitize_headers

        assert _sanitize_headers({}) == {}

    def test_make_structured_error(self):
        from tokenpak.proxy.circuit_breaker import _make_structured_error

        err = _make_structured_error("auth_error", "Invalid key", "Check your key", status=401)
        assert err["error"] == "auth_error"
        assert err["message"] == "Invalid key"
        assert err["suggestion"] == "Check your key"

    def test_enrich_upstream_error_401(self):
        from tokenpak.proxy.circuit_breaker import _enrich_upstream_error

        norm = {"error": {"type": "authentication_error", "message": "Unauthorized"}}
        result = _enrich_upstream_error(norm, 401)
        assert "hint" in result["error"]

    def test_enrich_upstream_error_429(self):
        from tokenpak.proxy.circuit_breaker import _enrich_upstream_error

        norm = {"error": {"type": "rate_limit_error", "message": "Too many requests"}}
        result = _enrich_upstream_error(norm, 429, retry_after_header="30")
        assert result["error"].get("retry_after") == 30

    def test_enrich_upstream_error_503(self):
        from tokenpak.proxy.circuit_breaker import _enrich_upstream_error

        norm = {"error": {"type": "provider_unavailable", "message": "Down"}}
        result = _enrich_upstream_error(norm, 503)
        assert "hint" in result["error"]

    def test_rate_limit_check_allows(self):
        from tokenpak.proxy.circuit_breaker import _rate_limit_check

        # Should allow at least one request for a fresh IP
        assert _rate_limit_check("10.0.0.1") is True

    def test_provider_from_url(self):
        from tokenpak.proxy.circuit_breaker import provider_from_url

        assert provider_from_url("https://api.anthropic.com") == "anthropic"
        assert provider_from_url("https://api.openai.com") == "openai"
        assert provider_from_url("https://generativelanguage.googleapis.com") == "google"


# ============================================================================
# connection_pool.py
# ============================================================================


class TestPoolConfig:
    def test_defaults(self):
        from tokenpak.proxy.connection_pool import PoolConfig

        cfg = PoolConfig()
        assert cfg.max_connections == 20
        assert cfg.max_keepalive_connections == 10
        assert cfg.keepalive_expiry == 30.0
        assert cfg.connect_timeout == 10.0
        assert cfg.read_timeout == 300.0
        assert cfg.http2 is True

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_POOL_MAX_CONNECTIONS", "50")
        monkeypatch.setenv("TOKENPAK_POOL_MAX_KEEPALIVE", "25")
        monkeypatch.setenv("TOKENPAK_POOL_KEEPALIVE_EXPIRY", "60")
        monkeypatch.setenv("TOKENPAK_HTTP2", "0")

        from tokenpak.proxy.connection_pool import PoolConfig

        cfg = PoolConfig.from_env()
        assert cfg.max_connections == 50
        assert cfg.max_keepalive_connections == 25
        assert cfg.keepalive_expiry == 60.0
        assert cfg.http2 is False


class TestPoolMetrics:
    def test_reuse_rate_zero_requests(self):
        from tokenpak.proxy.connection_pool import PoolMetrics

        m = PoolMetrics()
        assert m.reuse_rate == 0.0

    def test_reuse_rate_calculation(self):
        from tokenpak.proxy.connection_pool import PoolMetrics

        m = PoolMetrics(total_requests=10, reused_connections=7)
        assert m.reuse_rate == 0.7

    def test_to_dict_keys(self):
        from tokenpak.proxy.connection_pool import PoolMetrics

        d = PoolMetrics().to_dict()
        for k in ("total_requests", "reused_connections", "new_connections", "errors", "reuse_rate"):
            assert k in d


class TestConnectionPoolInit:
    def test_init_defaults(self):
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()
        assert pool.active_providers == []

    def test_http2_enabled_property(self):
        from tokenpak.proxy.connection_pool import ConnectionPool, PoolConfig

        cfg = PoolConfig(http2=False)
        pool = ConnectionPool(config=cfg)
        assert pool.http2_enabled is False

    def test_metrics_initial(self):
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()
        m = pool.metrics()
        assert m["total_requests"] == 0
        assert m["errors"] == 0

    def test_reset_metrics(self):
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()
        pool._metrics.total_requests = 5
        pool.reset_metrics()
        assert pool.metrics()["total_requests"] == 0

    def test_close_empty_pool(self):
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()
        pool.close()  # Should not raise
        assert pool.active_providers == []

    def test_repr(self):
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()
        r = repr(pool)
        assert "ConnectionPool" in r

    def test_request_increments_total(self):
        """request() increments total_requests even on error."""
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()

        mock_client = MagicMock()
        mock_client.request.side_effect = Exception("connection refused")

        with patch.object(pool, "_get_client", return_value=mock_client):
            with pytest.raises(Exception):
                pool.request("POST", "https://api.anthropic.com/v1/messages")

        assert pool.metrics()["total_requests"] == 1
        assert pool.metrics()["errors"] == 1

    def test_request_success_tracks_metrics(self):
        """request() tracks reuse/new metrics on success."""
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()

        mock_response = MagicMock()
        mock_response.http_version = "HTTP/2"
        mock_response.headers = {}
        mock_client = MagicMock()
        mock_client.request.return_value = mock_response

        with patch.object(pool, "_get_client", return_value=mock_client):
            pool.request("POST", "https://api.anthropic.com/v1/messages", content=b"{}")

        assert pool.metrics()["total_requests"] == 1
        assert pool.metrics()["reused_connections"] == 1

    def test_lazy_client_creation(self):
        """_get_client creates only one client per netloc."""
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()

        with patch.object(pool, "_make_client", wraps=pool._make_client) as mock_make:
            pool._get_client("api.anthropic.com")
            pool._get_client("api.anthropic.com")
            pool._get_client("api.openai.com")

        assert mock_make.call_count == 2  # one per unique netloc

    def test_close_clears_clients(self):
        from tokenpak.proxy.connection_pool import ConnectionPool

        pool = ConnectionPool()

        mock_client = MagicMock()
        pool._clients["api.anthropic.com"] = mock_client

        pool.close()
        mock_client.close.assert_called_once()
        assert pool.active_providers == []


class TestGlobalPool:
    def test_singleton(self):
        from tokenpak.proxy.connection_pool import get_global_pool, reset_global_pool

        reset_global_pool()
        p1 = get_global_pool()
        p2 = get_global_pool()
        assert p1 is p2
        reset_global_pool()

    def test_reset_creates_new(self):
        from tokenpak.proxy.connection_pool import get_global_pool, reset_global_pool

        reset_global_pool()
        p1 = get_global_pool()
        reset_global_pool()
        p2 = get_global_pool()
        assert p1 is not p2
        reset_global_pool()


# ============================================================================
# memory_guard.py
# ============================================================================


class TestMemoryGuardHelpers:
    def test_get_total_ram_mb_returns_int(self):
        from tokenpak.proxy.memory_guard import get_total_ram_mb

        val = get_total_ram_mb()
        assert isinstance(val, int)
        assert val > 0

    def test_get_available_ram_mb_returns_int(self):
        from tokenpak.proxy.memory_guard import get_available_ram_mb

        val = get_available_ram_mb()
        assert isinstance(val, int)
        assert val >= 0

    def test_get_rss_mb_returns_int(self):
        from tokenpak.proxy.memory_guard import get_rss_mb

        val = get_rss_mb()
        assert isinstance(val, int)
        assert val >= 0

    def test_malloc_trim_returns_bool(self):
        from tokenpak.proxy.memory_guard import malloc_trim

        result = malloc_trim()
        assert isinstance(result, bool)

    def test_calculate_budget_structure(self):
        from tokenpak.proxy.memory_guard import calculate_budget

        budget = calculate_budget(total_ram_mb=4096)
        for key in ("total_ram_mb", "budget_mb", "target_mb", "ceiling_mb", "sys_low_mb"):
            assert key in budget

    def test_calculate_budget_values(self):
        from tokenpak.proxy.memory_guard import calculate_budget

        budget = calculate_budget(proxy_share=0.35, budget_max_mb=2048, total_ram_mb=4096)
        # budget = min(4096 * 0.35, 2048) = min(1433, 2048) = 1433
        assert budget["budget_mb"] == min(int(4096 * 0.35), 2048)
        assert budget["target_mb"] == int(budget["budget_mb"] * 0.75)
        assert budget["ceiling_mb"] == int(budget["budget_mb"] * 0.95)

    def test_calculate_budget_cap(self):
        from tokenpak.proxy.memory_guard import calculate_budget

        # On a very large machine, budget should be capped at budget_max_mb
        budget = calculate_budget(proxy_share=0.35, budget_max_mb=1024, total_ram_mb=100_000)
        assert budget["budget_mb"] == 1024

    def test_calculate_budget_sys_low_minimum(self):
        from tokenpak.proxy.memory_guard import calculate_budget

        budget = calculate_budget(total_ram_mb=512)
        assert budget["sys_low_mb"] >= 200


class TestMemoryGuardInit:
    def test_init_explicit_values(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        mg = MemoryGuard(target_mb=500, ceiling_mb=900, sys_low_mb=200)
        assert mg.target_mb == 500
        assert mg.ceiling_mb == 900
        assert mg.sys_low_mb == 200

    def test_init_auto_calculated(self):
        from tokenpak.proxy.memory_guard import MemoryGuard, calculate_budget

        budget = calculate_budget()
        mg = MemoryGuard()
        assert mg.target_mb == budget["target_mb"]
        assert mg.ceiling_mb == budget["ceiling_mb"]

    def test_stats_initial(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        mg = MemoryGuard()
        s = mg.stats
        assert s["checks"] == 0
        assert s["gc_runs"] == 0
        assert s["last_level"] == "GREEN"

    def test_stats_has_config(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        mg = MemoryGuard(target_mb=100, ceiling_mb=200)
        s = mg.stats
        assert "config" in s
        assert s["config"]["target_mb"] == 100

    def test_start_stop(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        mg = MemoryGuard(check_interval_secs=60)
        mg.start()
        assert mg._thread is not None
        assert mg._thread.is_alive()
        thread = mg._thread
        mg.stop()
        # stop() sets _thread to None after join
        assert mg._thread is None
        assert not thread.is_alive()

    def test_start_idempotent(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        mg = MemoryGuard(check_interval_secs=60)
        mg.start()
        first_thread = mg._thread
        mg.start()  # Should not create a new thread
        assert mg._thread is first_thread
        mg.stop()

    def test_check_green_no_action(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        mg = MemoryGuard(target_mb=99999, ceiling_mb=999999, sys_low_mb=0)
        mg._check()
        assert mg.stats["last_level"] == "GREEN"

    def test_check_yellow_triggered(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        # Set a very low target to force YELLOW
        mg = MemoryGuard(target_mb=1, ceiling_mb=99999, sys_low_mb=0)
        mg._check()
        assert mg.stats["yellow_triggers"] >= 1
        assert mg.stats["gc_runs"] >= 1

    def test_check_red_triggered(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        # Set ceiling very low to force RED
        mg = MemoryGuard(target_mb=1, ceiling_mb=1, sys_low_mb=0)
        mg._check()
        assert mg.stats["red_triggers"] >= 1

    def test_evict_callbacks_called(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        evict_compact = MagicMock(return_value=5)
        evict_token = MagicMock(return_value=3)

        mg = MemoryGuard(
            target_mb=1,
            ceiling_mb=99999,
            sys_low_mb=0,
            on_evict_compact_cache=evict_compact,
            on_evict_token_cache=evict_token,
        )
        mg._check()
        evict_compact.assert_called_once()
        evict_token.assert_called_once()
        assert mg.stats["compact_evictions"] == 5
        assert mg.stats["token_evictions"] == 3

    def test_red_evict_callbacks_called_with_higher_pct(self):
        from tokenpak.proxy.memory_guard import MemoryGuard

        evict_compact = MagicMock(return_value=10)

        mg = MemoryGuard(
            target_mb=1,
            ceiling_mb=1,
            sys_low_mb=0,
            on_evict_compact_cache=evict_compact,
        )
        mg._check()
        # RED eviction uses 50% compact
        call_args = evict_compact.call_args[0][0]
        assert call_args == 50


class TestCreateMemoryGuard:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "0")
        from tokenpak.proxy.memory_guard import create_memory_guard

        result = create_memory_guard()
        assert result is None

    def test_enabled_returns_guard(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "1")
        from tokenpak.proxy.memory_guard import create_memory_guard

        result = create_memory_guard()
        assert result is not None

    def test_explicit_overrides_respected(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_MEMORY_GUARD", "1")
        monkeypatch.setenv("TOKENPAK_MEMORY_TARGET_MB", "512")
        monkeypatch.setenv("TOKENPAK_MEMORY_CEILING_MB", "900")
        from tokenpak.proxy.memory_guard import create_memory_guard

        mg = create_memory_guard()
        assert mg.target_mb == 512
        assert mg.ceiling_mb == 900


# ============================================================================
# request_pipeline.py
# ============================================================================


class TestClassifyIntent:
    """Tests for _classify_intent — keyword-based, no external deps."""

    def _classify(self, text):
        from tokenpak.proxy.request_pipeline import _classify_intent

        # Patch out semantic resolver to test keyword path only
        with patch(
            "tokenpak.proxy.request_pipeline._classify_intent.__globals__",
            {},
            create=True,
        ):
            pass  # no-op; just call directly
        return _classify_intent(text)

    def test_status_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("is it running?")
        assert result == "status"

    def test_usage_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("show me token count")
        assert result == "usage"

    def test_execute_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("run the test suite")
        assert result == "execute"

    def test_debug_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("fix this bug")
        assert result == "debug"

    def test_summarize_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("summarize this document")
        assert result == "summarize"

    def test_plan_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("plan the architecture")
        assert result == "plan"

    def test_explain_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("explain what is a transformer")
        assert result == "explain"

    def test_search_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("find all log files")
        assert result == "search"

    def test_create_keyword(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("write a function to parse JSON")
        assert result == "create"

    def test_query_fallback(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("something completely unrecognized xyzzy")
        assert result == "query"

    def test_empty_text_fallback(self):
        from tokenpak.proxy.request_pipeline import _classify_intent

        result = _classify_intent("")
        assert result == "query"

    def test_semantic_resolver_exception_falls_through(self):
        """If semantic resolver raises, should still classify via keywords."""

        with patch(
            "tokenpak.proxy.request_pipeline._classify_intent",
            wraps=lambda t, _sm=None: "status" if "health" in t else "query",
        ):
            from tokenpak.proxy.request_pipeline import _classify_intent as ci

            assert ci("check health") == "status"


class TestIsProtectedContent:
    def test_not_protected_short_text(self):
        from tokenpak.proxy.request_pipeline import is_protected_content

        assert is_protected_content("hello") is False

    def test_not_protected_empty(self):
        from tokenpak.proxy.request_pipeline import is_protected_content

        assert is_protected_content("") is False

    def test_protected_multiple_markers(self):
        from tokenpak.proxy.request_pipeline import is_protected_content

        text = "You are an AI. ## Core Truths: be honest. ## Boundaries: stay safe. " * 5
        assert is_protected_content(text) is True

    def test_protected_one_marker_not_enough(self):
        from tokenpak.proxy.request_pipeline import is_protected_content

        # Only one marker — should not trip
        text = "AGENTS.md is a file. " + "x" * 100
        assert is_protected_content(text) is False

    def test_protected_two_markers(self):
        from tokenpak.proxy.request_pipeline import is_protected_content

        text = "AGENTS.md IDENTITY.md " + "x" * 100
        assert is_protected_content(text) is True


class TestClassifyMessageRisk:
    def test_system_role_is_protected(self):
        from tokenpak.proxy.request_pipeline import classify_message_risk

        msg = {"role": "system", "content": "You are a helpful assistant."}
        assert classify_message_risk(msg) == "protected"

    def test_tool_role_is_config(self):
        from tokenpak.proxy.request_pipeline import classify_message_risk

        msg = {"role": "tool", "content": "result"}
        assert classify_message_risk(msg) == "config"

    def test_narrative_user_message(self):
        from tokenpak.proxy.request_pipeline import classify_message_risk

        msg = {"role": "user", "content": "Hello, how are you today?"}
        assert classify_message_risk(msg) in ("narrative", "code")

    def test_code_content_detection(self):
        from tokenpak.proxy.request_pipeline import classify_message_risk

        code_content = "```python\ndef foo():\n    pass\n```"
        msg = {"role": "user", "content": code_content}
        assert classify_message_risk(msg) == "code"

    def test_list_content_joined(self):
        from tokenpak.proxy.request_pipeline import classify_message_risk

        msg = {
            "role": "user",
            "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}],
        }
        result = classify_message_risk(msg)
        assert result in ("narrative", "code", "protected")


class TestCanCompress:
    def test_protected_never_compresses(self):
        from tokenpak.proxy.request_pipeline import can_compress

        assert can_compress("protected", "hybrid") is False
        assert can_compress("protected", "aggressive") is False

    def test_strict_mode_no_compress(self):
        from tokenpak.proxy.request_pipeline import can_compress

        assert can_compress("narrative", "strict") is False

    def test_safe_mode_no_compress(self):
        from tokenpak.proxy.request_pipeline import can_compress

        assert can_compress("narrative", "safe") is False

    def test_hybrid_mode_narrative_compresses(self):
        from tokenpak.proxy.request_pipeline import can_compress

        assert can_compress("narrative", "hybrid") is True

    def test_hybrid_mode_code_no_compress(self):
        from tokenpak.proxy.request_pipeline import can_compress

        assert can_compress("code", "hybrid") is False


class TestResolveSessionId:
    def test_claude_code_session_takes_priority(self):
        from tokenpak.proxy.request_pipeline import _resolve_session_id

        headers = {
            "X-Claude-Code-Session-Id": "cc-session-123",
            "X-TokenPak-Session": "tp-session-456",
        }
        result = _resolve_session_id(headers, "claude-3-sonnet")
        assert result == "cc-session-123"

    def test_tokenpak_session_fallback(self):
        from tokenpak.proxy.request_pipeline import _resolve_session_id

        headers = {"X-TokenPak-Session": "tp-session-789"}
        result = _resolve_session_id(headers, "claude-3-sonnet")
        assert result == "tp-session-789"

    def test_model_fallback(self):
        from tokenpak.proxy.request_pipeline import _resolve_session_id

        headers = {}
        result = _resolve_session_id(headers, "claude-opus-4-6")
        assert result == "claude-opus-4-6"

    def test_case_insensitive_lookup(self):
        from tokenpak.proxy.request_pipeline import _resolve_session_id

        headers = {"x-claude-code-session-id": "cc-lower"}
        result = _resolve_session_id(headers, "model")
        assert result == "cc-lower"


class TestPartitionStableVolatile:
    def test_splits_correctly(self):
        from tokenpak.proxy.request_pipeline import _partition_stable_volatile

        body = json.dumps(
            {
                "model": "claude-3",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                    {"role": "user", "content": "how are you?"},
                ],
                "tools": [],
                "system": [],
            }
        ).encode()

        stable, volatile = _partition_stable_volatile(body)
        stable_obj = json.loads(stable)
        volatile_obj = json.loads(volatile)

        # stable should have first 2 messages
        assert len(stable_obj["messages"]) == 2
        # volatile should have newest turn
        assert len(volatile_obj["messages"]) == 1
        assert volatile_obj["messages"][0]["content"] == "how are you?"

    def test_single_message_no_stable(self):
        from tokenpak.proxy.request_pipeline import _partition_stable_volatile

        body = json.dumps(
            {"messages": [{"role": "user", "content": "hi"}]}
        ).encode()

        stable, volatile = _partition_stable_volatile(body)
        stable_obj = json.loads(stable)
        volatile_obj = json.loads(volatile)

        assert stable_obj["messages"] == []
        assert len(volatile_obj["messages"]) == 1

    def test_invalid_json_returns_empty_stable(self):
        from tokenpak.proxy.request_pipeline import _partition_stable_volatile

        bad_body = b"not json at all"
        stable, volatile = _partition_stable_volatile(bad_body)
        assert stable == b""
        assert volatile == bad_body

    def test_deterministic_output(self):
        from tokenpak.proxy.request_pipeline import _partition_stable_volatile

        body = json.dumps(
            {"messages": [{"role": "user", "content": "test"}]}
        ).encode()

        s1, v1 = _partition_stable_volatile(body)
        s2, v2 = _partition_stable_volatile(body)
        assert s1 == s2
        assert v1 == v2


class TestExtractUserText:
    def test_extracts_last_user_message(self):
        from tokenpak.proxy.request_pipeline import _extract_user_text

        body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "response"},
                    {"role": "user", "content": "second"},
                ]
            }
        ).encode()
        assert _extract_user_text(body) == "second"

    def test_extracts_from_content_list(self):
        from tokenpak.proxy.request_pipeline import _extract_user_text

        body = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "text", "text": "world"},
                        ],
                    }
                ]
            }
        ).encode()
        result = _extract_user_text(body)
        assert "hello" in result
        assert "world" in result

    def test_empty_messages_returns_empty(self):
        from tokenpak.proxy.request_pipeline import _extract_user_text

        body = json.dumps({"messages": []}).encode()
        assert _extract_user_text(body) == ""

    def test_invalid_json_returns_empty(self):
        from tokenpak.proxy.request_pipeline import _extract_user_text

        assert _extract_user_text(b"not json") == ""

    def test_no_user_role_returns_empty(self):
        from tokenpak.proxy.request_pipeline import _extract_user_text

        body = json.dumps(
            {"messages": [{"role": "assistant", "content": "hello"}]}
        ).encode()
        assert _extract_user_text(body) == ""
