"""
Tests for TokenPak Failover Engine (F.4, F.5, F.6)

Covers:
  - classify_error: HTTP 429, 500+, 401/403, timeout, unknown
  - decide: rate_limit → switch+wait, auth → alert_abort, server → switch
  - CircuitBreaker: threshold, cool-down, half-open probe, reset, success
  - FailoverEventLog: record, get_recent, get_footer_indicator
  - FailoverEngine: iter_attempts (primary only, with chain, circuit skip)
  - FailoverEngine: handle_error (switch, alert_abort), record_success
  - normalize_response: same provider passthrough, cross-provider translation
  - render_failover_footer: format correctness
  - Integration: anthropic 429 → openai succeed → anthropic-format response
  - Integration: all providers fail → exhausted
  - Integration: circuit breaker opens → provider skipped in iter_attempts
"""

from __future__ import annotations

import time

from tokenpak.proxy.failover import (
    FailoverConfig,
    ProviderEntry,
)
from tokenpak.proxy.failover_engine import (
    CIRCUIT_FAILURE_THRESHOLD,
    RATE_LIMIT_WAIT_SECONDS,
    CircuitBreaker,
    ErrorType,
    FailoverEngine,
    FailoverEvent,
    FailoverEventLog,
    ProviderAttempt,
    classify_error,
    decide,
    get_event_log,
    normalize_response,
    render_failover_footer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(*providers: str) -> FailoverConfig:
    """Build a minimal FailoverConfig with given providers (credentials mocked)."""
    import os

    chain = []
    for p in providers:
        env_var = f"MOCK_{p.upper()}_KEY"
        os.environ[env_var] = "mock-key"
        chain.append(
            ProviderEntry(
                provider=p,
                model_map={"claude-sonnet-4-5": "gpt-4o" if p == "openai" else "claude-sonnet-4-5"},
                credential_env=env_var,
            )
        )
    return FailoverConfig(enabled=True, chain=chain)


def _make_engine(*providers: str) -> FailoverEngine:
    """Build a FailoverEngine with a fresh event log and circuit breaker."""
    config = _make_config(*providers)
    return FailoverEngine(
        config=config,
        circuit_breaker=CircuitBreaker(failure_threshold=CIRCUIT_FAILURE_THRESHOLD),
        event_log=FailoverEventLog(),
    )


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------


class TestClassifyError:
    def test_429_rate_limit(self):
        err = classify_error(http_status=429)
        assert err.error_type == ErrorType.RATE_LIMIT
        assert err.http_status == 429
        assert err.is_rate_limit

    def test_401_auth(self):
        err = classify_error(http_status=401)
        assert err.error_type == ErrorType.AUTH_ERROR
        assert err.is_auth_error
        assert not err.should_switch

    def test_403_auth(self):
        err = classify_error(http_status=403)
        assert err.error_type == ErrorType.AUTH_ERROR
        assert err.is_auth_error

    def test_500_server_error(self):
        err = classify_error(http_status=500)
        assert err.error_type == ErrorType.SERVER_ERROR
        assert err.should_switch

    def test_503_server_error(self):
        err = classify_error(http_status=503)
        assert err.error_type == ErrorType.SERVER_ERROR

    def test_timeout_exception(self):
        err = classify_error(exception=TimeoutError("connection timed out"))
        assert err.error_type == ErrorType.TIMEOUT
        assert err.should_switch

    def test_timeout_name_match(self):
        class ReadTimeoutError(Exception):
            pass

        err = classify_error(exception=ReadTimeoutError("read timeout"))
        assert err.error_type == ErrorType.TIMEOUT

    def test_unknown(self):
        err = classify_error()
        assert err.error_type == ErrorType.UNKNOWN

    def test_unknown_with_exception(self):
        err = classify_error(exception=ValueError("weird error"))
        assert err.error_type == ErrorType.UNKNOWN


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------


class TestDecide:
    def test_rate_limit_switch_with_wait(self):
        err = classify_error(http_status=429)
        d = decide(err)
        assert d.action == "switch"
        assert d.wait_seconds == RATE_LIMIT_WAIT_SECONDS

    def test_server_error_switch_no_wait(self):
        err = classify_error(http_status=500)
        d = decide(err)
        assert d.action == "switch"
        assert d.wait_seconds == 0.0

    def test_timeout_switch(self):
        err = classify_error(exception=TimeoutError("timeout"))
        d = decide(err)
        assert d.action == "switch"

    def test_auth_alert_abort(self):
        err = classify_error(http_status=401)
        d = decide(err)
        assert d.action == "alert_abort"
        assert d.wait_seconds == 0.0

    def test_auth_403_alert_abort(self):
        err = classify_error(http_status=403)
        d = decide(err)
        assert d.action == "alert_abort"


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_starts_available(self):
        cb = CircuitBreaker()
        assert cb.is_available("anthropic")

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=60)
        for _ in range(3):
            cb.record_failure("anthropic")
        assert not cb.is_available("anthropic")

    def test_does_not_open_before_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=60)
        for _ in range(2):
            cb.record_failure("anthropic")
        assert cb.is_available("anthropic")

    def test_third_failure_opens_circuit(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=60)
        for _ in range(2):
            cb.record_failure("anthropic")
        opened = cb.record_failure("anthropic")
        assert opened is True
        assert not cb.is_available("anthropic")

    def test_success_resets_circuit(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=60)
        for _ in range(3):
            cb.record_failure("anthropic")
        cb.record_success("anthropic")
        assert cb.is_available("anthropic")

    def test_half_open_probe_after_cooldown(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=0.1)
        for _ in range(3):
            cb.record_failure("anthropic")
        assert not cb.is_available("anthropic")
        time.sleep(0.15)
        # Should allow one probe
        assert cb.is_available("anthropic")
        # But not a second one immediately
        assert not cb.is_available("anthropic")

    def test_probe_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=0.1)
        for _ in range(3):
            cb.record_failure("anthropic")
        time.sleep(0.15)
        cb.is_available("anthropic")  # trigger half-open
        cb.record_success("anthropic")
        assert cb.is_available("anthropic")

    def test_different_providers_isolated(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=60)
        for _ in range(3):
            cb.record_failure("anthropic")
        assert not cb.is_available("anthropic")
        assert cb.is_available("openai")

    def test_reset_force_closes(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=60)
        for _ in range(3):
            cb.record_failure("anthropic")
        cb.reset("anthropic")
        assert cb.is_available("anthropic")

    def test_get_state_returns_dict(self):
        cb = CircuitBreaker(failure_threshold=3, cool_down_seconds=60)
        cb.record_failure("anthropic")
        state = cb.get_state("anthropic")
        assert state["provider"] == "anthropic"
        assert state["failure_count"] == 1
        assert not state["is_open"]
        assert state["seconds_until_retry"] == 0


# ---------------------------------------------------------------------------
# FailoverEventLog
# ---------------------------------------------------------------------------


class TestFailoverEventLog:
    def _event(self, orig="anthropic", failover="openai", succeeded=True) -> FailoverEvent:
        from datetime import datetime, timezone

        return FailoverEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            original_provider=orig,
            failover_provider=failover,
            error_type=ErrorType.RATE_LIMIT,
            http_status=429,
            model="claude-sonnet-4-5",
            succeeded=succeeded,
            message="test",
        )

    def test_empty_log_returns_empty(self):
        log = FailoverEventLog()
        assert log.get_recent() == []
        assert log.get_footer_indicator() is None

    def test_record_and_retrieve(self):
        log = FailoverEventLog()
        log.record(self._event())
        events = log.get_recent()
        assert len(events) == 1
        assert events[0]["original_provider"] == "anthropic"
        assert events[0]["failover_provider"] == "openai"

    def test_get_recent_limit(self):
        log = FailoverEventLog()
        for _ in range(5):
            log.record(self._event())
        assert len(log.get_recent(limit=3)) == 3

    def test_footer_indicator_format(self):
        log = FailoverEventLog()
        log.record(self._event(orig="anthropic", failover="openai"))
        indicator = log.get_footer_indicator()
        assert indicator is not None
        assert "failover:openai" in indicator
        assert "anthropic" in indicator
        assert "429" in indicator

    def test_footer_shows_most_recent(self):
        log = FailoverEventLog()
        log.record(self._event(orig="anthropic", failover="openai"))
        log.record(self._event(orig="anthropic", failover="google"))
        indicator = log.get_footer_indicator()
        assert "failover:google" in indicator


# ---------------------------------------------------------------------------
# FailoverEngine — iter_attempts
# ---------------------------------------------------------------------------


class TestFailoverEngineIterAttempts:
    def test_no_failover_config_yields_primary_only(self):
        engine = FailoverEngine(
            config=FailoverConfig(enabled=False),
            circuit_breaker=CircuitBreaker(),
            event_log=FailoverEventLog(),
        )
        attempts = list(engine.iter_attempts("claude-sonnet-4-5", "anthropic"))
        assert len(attempts) == 1
        assert attempts[0].is_primary

    def test_yields_primary_first(self):
        engine = _make_engine("anthropic", "openai")
        attempts = list(engine.iter_attempts("claude-sonnet-4-5", "anthropic"))
        assert attempts[0].provider == "anthropic"
        assert attempts[0].is_primary

    def test_yields_all_providers_in_chain(self):
        engine = _make_engine("anthropic", "openai")
        attempts = list(engine.iter_attempts("claude-sonnet-4-5", "anthropic"))
        providers = [a.provider for a in attempts]
        assert "anthropic" in providers
        assert "openai" in providers

    def test_skips_open_circuit(self):
        engine = _make_engine("anthropic", "openai")
        # Open circuit for anthropic
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            engine._circuit.record_failure("anthropic")
        attempts = list(engine.iter_attempts("claude-sonnet-4-5", "anthropic"))
        providers = [a.provider for a in attempts]
        assert "anthropic" not in providers
        assert "openai" in providers

    def test_all_circuits_open_yields_nothing(self):
        engine = _make_engine("anthropic", "openai")
        for p in ["anthropic", "openai"]:
            for _ in range(CIRCUIT_FAILURE_THRESHOLD):
                engine._circuit.record_failure(p)
        attempts = list(engine.iter_attempts("claude-sonnet-4-5", "anthropic"))
        assert attempts == []

    def test_preferred_provider_first(self):
        engine = _make_engine("openai", "anthropic")
        attempts = list(engine.iter_attempts("claude-sonnet-4-5", "anthropic"))
        assert attempts[0].provider == "anthropic"


# ---------------------------------------------------------------------------
# FailoverEngine — handle_error + record_success
# ---------------------------------------------------------------------------


class TestFailoverEngineHandleError:
    def test_rate_limit_returns_continue_with_wait(self):
        engine = _make_engine("anthropic", "openai")
        attempt = ProviderAttempt(
            provider="anthropic",
            model="claude-sonnet-4-5",
            credential_env="MOCK_ANTHROPIC_KEY",
            is_primary=True,
        )
        err = classify_error(http_status=429)
        should_continue, wait = engine.handle_error(attempt, err, "anthropic", "claude-sonnet-4-5")
        assert should_continue
        assert wait == RATE_LIMIT_WAIT_SECONDS

    def test_server_error_returns_continue_no_wait(self):
        engine = _make_engine("anthropic", "openai")
        attempt = ProviderAttempt(
            provider="anthropic",
            model="claude-sonnet-4-5",
            credential_env="MOCK_ANTHROPIC_KEY",
            is_primary=True,
        )
        err = classify_error(http_status=500)
        should_continue, wait = engine.handle_error(attempt, err, "anthropic", "claude-sonnet-4-5")
        assert should_continue
        assert wait == 0.0

    def test_auth_error_returns_no_continue(self):
        engine = _make_engine("anthropic", "openai")
        attempt = ProviderAttempt(
            provider="anthropic",
            model="claude-sonnet-4-5",
            credential_env="MOCK_ANTHROPIC_KEY",
            is_primary=True,
        )
        err = classify_error(http_status=401)
        should_continue, wait = engine.handle_error(attempt, err, "anthropic", "claude-sonnet-4-5")
        assert not should_continue

    def test_failure_increments_circuit(self):
        engine = _make_engine("anthropic", "openai")
        attempt = ProviderAttempt(
            provider="anthropic",
            model="claude-sonnet-4-5",
            credential_env="MOCK_ANTHROPIC_KEY",
            is_primary=True,
        )
        err = classify_error(http_status=500)
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            engine.handle_error(attempt, err, "anthropic", "claude-sonnet-4-5")
        assert not engine._circuit.is_available("anthropic")

    def test_record_success_primary_no_footer(self):
        engine = _make_engine("anthropic", "openai")
        footer = engine.record_success(
            "anthropic", "anthropic", "claude-sonnet-4-5", was_failover=False
        )
        assert footer is None

    def test_record_success_failover_returns_footer(self):
        engine = _make_engine("anthropic", "openai")
        footer = engine.record_success(
            "openai", "anthropic", "claude-sonnet-4-5", was_failover=True
        )
        assert footer is not None
        assert "failover:openai" in footer

    def test_record_success_resets_circuit(self):
        engine = _make_engine("anthropic", "openai")
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            engine._circuit.record_failure("openai")
        engine.record_success("openai", "anthropic", "claude-sonnet-4-5", was_failover=True)
        assert engine._circuit.is_available("openai")


# ---------------------------------------------------------------------------
# normalize_response
# ---------------------------------------------------------------------------


class TestNormalizeResponse:
    def test_same_provider_passthrough(self):
        body = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": "claude-sonnet-4-5",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        result = normalize_response(body, "anthropic", "anthropic")
        assert result == body

    def test_openai_to_anthropic_normalization(self):
        openai_response = {
            "id": "chatcmpl-abc",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        result = normalize_response(openai_response, "openai", "anthropic")
        # Should be in Anthropic format (have content blocks)
        assert "content" in result
        assert isinstance(result["content"], list)

    def test_translation_error_returns_raw(self):
        body = {"garbage": "data"}
        # Should not raise — returns raw on error
        result = normalize_response(body, "openai", "anthropic")
        # Either translated (may be malformed) or passthrough — no exception
        assert result is not None


# ---------------------------------------------------------------------------
# render_failover_footer
# ---------------------------------------------------------------------------


class TestRenderFailoverFooter:
    def test_basic_format(self):
        footer = render_failover_footer("anthropic", 429, "rate_limit", "openai")
        assert "⚠️" in footer
        assert "failover:openai" in footer
        assert "anthropic" in footer
        assert "429" in footer
        assert "rate_limit" in footer

    def test_no_status_code(self):
        footer = render_failover_footer("anthropic", None, "timeout", "openai")
        assert "failover:openai" in footer
        assert "timeout" in footer

    def test_server_error_format(self):
        footer = render_failover_footer("openai", 503, "server_error", "google")
        assert "failover:google" in footer
        assert "503" in footer


# ---------------------------------------------------------------------------
# Integration: anthropic 429 → openai succeed → anthropic-format response
# ---------------------------------------------------------------------------


class TestIntegrationAnthropicFailover:
    def test_anthropic_429_openai_succeeds(self):
        """
        Simulate: Anthropic returns 429, engine switches to OpenAI,
        OpenAI succeeds, response is normalized to Anthropic format.
        """
        engine = _make_engine("anthropic", "openai")
        model = "claude-sonnet-4-5"
        provider = "anthropic"

        openai_response = {
            "id": "chatcmpl-xyz",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello from OpenAI!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        final_response = None
        failover_provider = None

        attempts = list(engine.iter_attempts(model, provider))
        assert len(attempts) >= 2

        # Simulate first attempt (anthropic) failing with 429
        first_attempt = attempts[0]
        assert first_attempt.provider == "anthropic"
        err = classify_error(http_status=429)
        should_continue, wait = engine.handle_error(first_attempt, err, provider, model)
        assert should_continue

        # Second attempt (openai) succeeds
        second_attempt = attempts[1]
        assert second_attempt.provider == "openai"
        failover_provider = second_attempt.provider
        final_response = normalize_response(openai_response, "openai", "anthropic")
        footer = engine.record_success(failover_provider, provider, model, was_failover=True)
        assert footer is not None
        assert "failover:openai" in footer

        # Response should be in Anthropic format
        assert "content" in final_response
        assert isinstance(final_response["content"], list)
        assert final_response["content"][0]["type"] == "text"
        assert "Hello from OpenAI!" in final_response["content"][0]["text"]

    def test_all_providers_fail_exhausted(self):
        """When all providers fail, iter_attempts is exhausted."""
        engine = _make_engine("anthropic", "openai")
        model = "claude-sonnet-4-5"
        provider = "anthropic"
        err = classify_error(http_status=500)

        attempts = list(engine.iter_attempts(model, provider))
        all_failed = True

        for attempt in attempts:
            should_continue, _ = engine.handle_error(attempt, err, provider, model)
            if not should_continue:
                break
        # After exhausting all attempts with server_error, all circuits should be tracking failures
        state_a = engine._circuit.get_state("anthropic")
        state_o = engine._circuit.get_state("openai")
        assert state_a["failure_count"] > 0
        assert state_o["failure_count"] > 0

    def test_circuit_breaker_skips_provider(self):
        """After threshold failures, anthropic is skipped in iter_attempts."""
        engine = _make_engine("anthropic", "openai")
        model = "claude-sonnet-4-5"
        provider = "anthropic"
        err = classify_error(http_status=500)

        # Fail anthropic CIRCUIT_FAILURE_THRESHOLD times
        attempt = ProviderAttempt(
            provider="anthropic",
            model=model,
            credential_env="MOCK_ANTHROPIC_KEY",
            is_primary=True,
        )
        for _ in range(CIRCUIT_FAILURE_THRESHOLD):
            engine.handle_error(attempt, err, provider, model)

        assert not engine._circuit.is_available("anthropic")

        # iter_attempts should skip anthropic now
        attempts = list(engine.iter_attempts(model, provider))
        providers = [a.provider for a in attempts]
        assert "anthropic" not in providers
        assert "openai" in providers


# ---------------------------------------------------------------------------
# Global event log singleton
# ---------------------------------------------------------------------------


class TestGlobalEventLog:
    def test_get_event_log_returns_singleton(self):
        log1 = get_event_log()
        log2 = get_event_log()
        assert log1 is log2
