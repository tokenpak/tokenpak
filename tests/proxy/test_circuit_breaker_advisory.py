# SPDX-License-Identifier: Apache-2.0
"""NCP-4 Phase A (B5) — circuit-breaker advisory-mode tests.

Verifies that providers in ``CircuitBreakerConfig.advisory_providers``
have their breaker state observed for telemetry but are not blocked
by ``CircuitBreakerRegistry.allow_request`` semantics. The proxy
caller checks ``registry.is_advisory(provider)`` and bypasses the
fast-fail path for advisory providers.
"""

from __future__ import annotations

from tokenpak.proxy.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
)

# ── Default advisory set ──────────────────────────────────────────────


class TestAdvisoryProvidersDefault:

    def test_default_includes_anthropic(self):
        cfg = CircuitBreakerConfig()
        assert "anthropic" in cfg.advisory_providers

    def test_default_excludes_other_providers(self):
        cfg = CircuitBreakerConfig()
        assert "openai" not in cfg.advisory_providers
        assert "google" not in cfg.advisory_providers
        assert "azure" not in cfg.advisory_providers


# ── Env override ──────────────────────────────────────────────────────


class TestAdvisoryProvidersEnv:

    def test_env_unset_defaults_to_anthropic(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_CB_ADVISORY_PROVIDERS", raising=False)
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.advisory_providers == frozenset({"anthropic"})

    def test_env_explicit_set(self, monkeypatch):
        monkeypatch.setenv(
            "TOKENPAK_CB_ADVISORY_PROVIDERS", "anthropic,openai"
        )
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.advisory_providers == frozenset({"anthropic", "openai"})

    def test_env_empty_disables_advisory(self, monkeypatch):
        # Escape hatch — operator can disable advisory mode entirely.
        monkeypatch.setenv("TOKENPAK_CB_ADVISORY_PROVIDERS", "")
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.advisory_providers == frozenset()

    def test_env_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv(
            "TOKENPAK_CB_ADVISORY_PROVIDERS",
            "  anthropic , openai  ,  google  ",
        )
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.advisory_providers == frozenset(
            {"anthropic", "openai", "google"}
        )

    def test_env_drops_empty_tokens(self, monkeypatch):
        monkeypatch.setenv(
            "TOKENPAK_CB_ADVISORY_PROVIDERS", "anthropic,,openai,"
        )
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.advisory_providers == frozenset({"anthropic", "openai"})


# ── Registry.is_advisory ──────────────────────────────────────────────


class TestRegistryIsAdvisory:

    def test_anthropic_is_advisory_by_default(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig())
        assert registry.is_advisory("anthropic") is True

    def test_openai_is_not_advisory_by_default(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig())
        assert registry.is_advisory("openai") is False

    def test_is_advisory_respects_custom_set(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig(
            advisory_providers=frozenset({"openai"})
        ))
        assert registry.is_advisory("anthropic") is False
        assert registry.is_advisory("openai") is True

    def test_is_advisory_empty_set_no_provider_advisory(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig(
            advisory_providers=frozenset()
        ))
        assert registry.is_advisory("anthropic") is False
        assert registry.is_advisory("openai") is False


# ── Telemetry still works for advisory providers ──────────────────────


class TestAdvisoryStillRecordsTelemetry:

    def test_record_failure_advances_breaker_state_for_advisory(self):
        # Advisory mode does NOT short-circuit record_failure — the
        # breaker still tracks state so we can see what would have
        # tripped. record_failure is called from server.py at the
        # upstream-exception emit site regardless of advisory status.
        registry = CircuitBreakerRegistry(CircuitBreakerConfig(
            failure_threshold=3, advisory_providers=frozenset({"anthropic"}),
        ))
        for _ in range(3):
            registry.record_failure("anthropic")
        # Breaker has internally tripped to OPEN (telemetry).
        assert registry.get_state("anthropic") == CircuitState.OPEN

    def test_advisory_provider_status_reports_failures(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig(
            failure_threshold=10, advisory_providers=frozenset({"anthropic"}),
        ))
        registry.record_failure("anthropic")
        registry.record_failure("anthropic")
        statuses = registry.all_statuses()
        assert statuses["anthropic"]["total_failures"] == 2
        assert statuses["anthropic"]["state"] == "closed"  # below threshold

    def test_record_success_still_resets_advisory(self):
        # If the breaker has internally tripped to OPEN-then-HALF_OPEN
        # for an advisory provider, a success still closes it.
        cfg = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=0.0,  # immediate recovery for the test
            advisory_providers=frozenset({"anthropic"}),
        )
        registry = CircuitBreakerRegistry(cfg)
        registry.record_failure("anthropic")
        registry.record_failure("anthropic")
        # Force HALF_OPEN by reading state via the breaker directly
        breaker = registry._get_or_create("anthropic")
        # Advance time by triggering allow_request after recovery
        breaker.allow_request()
        assert registry.get_state("anthropic") == CircuitState.HALF_OPEN
        registry.record_success("anthropic")
        assert registry.get_state("anthropic") == CircuitState.CLOSED


# ── Non-advisory providers still trip and fast-fail ──────────────────


class TestNonAdvisoryProvidersUnchanged:

    def test_openai_still_trips_at_threshold(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig(
            failure_threshold=3, advisory_providers=frozenset({"anthropic"}),
        ))
        for _ in range(3):
            registry.record_failure("openai")
        assert registry.get_state("openai") == CircuitState.OPEN
        # And allow_request() returns False for the non-advisory
        # provider — server.py would fast-fail.
        assert registry.allow_request("openai") is False

    def test_anthropic_advisory_does_not_affect_other_providers(self):
        registry = CircuitBreakerRegistry(CircuitBreakerConfig(
            advisory_providers=frozenset({"anthropic"}),
        ))
        # Switching anthropic to advisory must not change is_advisory
        # for any other provider.
        assert registry.is_advisory("openai") is False
        assert registry.is_advisory("google") is False
        assert registry.is_advisory("unknown") is False


# ── Defaults preserved for the non-advisory fields ────────────────────


class TestExistingConfigDefaultsPreserved:

    def test_failure_threshold_default(self):
        cfg = CircuitBreakerConfig()
        assert cfg.failure_threshold == 5

    def test_recovery_timeout_default(self):
        cfg = CircuitBreakerConfig()
        assert cfg.recovery_timeout == 60.0

    def test_window_seconds_default(self):
        cfg = CircuitBreakerConfig()
        assert cfg.window_seconds == 60.0

    def test_enabled_default(self):
        cfg = CircuitBreakerConfig()
        assert cfg.enabled is True

    def test_existing_env_vars_unaffected(self, monkeypatch):
        # Setting only the advisory env var must not disturb the
        # existing failure-threshold/recovery/window defaults.
        monkeypatch.setenv("TOKENPAK_CB_ADVISORY_PROVIDERS", "anthropic")
        monkeypatch.delenv("TOKENPAK_CB_FAILURE_THRESHOLD", raising=False)
        monkeypatch.delenv("TOKENPAK_CB_RECOVERY_TIMEOUT", raising=False)
        monkeypatch.delenv("TOKENPAK_CB_WINDOW_SECONDS", raising=False)
        cfg = CircuitBreakerConfig.from_env()
        assert cfg.failure_threshold == 5
        assert cfg.recovery_timeout == 60.0
        assert cfg.window_seconds == 60.0


# ── Sanity: the underlying CircuitBreaker class is unchanged ─────────


class TestCircuitBreakerClassIsUnchanged:

    def test_breaker_does_not_consult_advisory_set(self):
        # The CircuitBreaker class itself has no concept of advisory
        # mode — the advisory bypass is enforced by callers at the
        # registry level. The breaker continues to track state
        # exactly as before.
        cfg = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker("anthropic", cfg)
        assert breaker.allow_request() is True
        breaker.record_failure()
        breaker.record_failure()
        # State machine trips regardless of advisory status — this
        # is intentional, so registry callers can still see the
        # would-have-tripped signal in telemetry.
        assert breaker.state == CircuitState.OPEN
        assert breaker.allow_request() is False
