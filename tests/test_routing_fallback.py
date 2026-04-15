"""
tests/test_routing_fallback.py
──────────────────────────────
Tests for tokenpak.routing.fallback — proxy-layer fallback bridge.

Coverage:
  1.  FallbackRouter: success on first try
  2.  FallbackRouter: 429 → backoff → success
  3.  FallbackRouter: all retries fail → FallbackExhaustedError
  4.  FallbackRouter: failover disabled → RetryEngine default provider switch
  5.  FallbackRouter: failover enabled → FailoverManager drives provider switch
  6.  FallbackRouter: on_handoff accepted
  7.  FallbackRouter: on_human_alert called on exhaustion
  8.  FallbackRouter: 401 immediate alert → FallbackExhaustedError fast path
  9.  fallback_call: functional API success
  10. fallback_call: functional API exhaustion
  11. get_recent_fallback_events: delegates to load_recent_retry_events
  12. FallbackExhaustedError: message and attributes
  13. FallbackRouter: state_dir override propagated to engine
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.agentic", reason="module not available in current build")
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call as mock_call

from tokenpak.routing.fallback import (
    FallbackRouter,
    FallbackExhaustedError,
    fallback_call,
    get_recent_fallback_events,
)
from tokenpak.agentic.retry import RetryExhaustedError


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ctx(**kw):
    """Minimal context dict."""
    base = {"task": "test-task", "task_id": "t-001", "model": "claude-opus-4-5", "provider": "anthropic"}
    base.update(kw)
    return base


def _make_fn(succeed_on_attempt: int = 1, error_code: str = "500"):
    """Return a callable that fails *succeed_on_attempt - 1* times then succeeds."""
    calls = {"n": 0}

    def fn(ctx, state):
        calls["n"] += 1
        if calls["n"] < succeed_on_attempt:
            raise ValueError(f"HTTP {error_code} server error (attempt {calls['n']})")
        return f"ok-{calls['n']}"

    fn.call_count_ref = calls
    return fn


def _always_fail(error_msg: str = "HTTP 500 server error"):
    def fn(ctx, state):
        raise ValueError(error_msg)
    return fn


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestFallbackRouterSuccess:
    def test_success_first_try(self, tmp_path):
        fn = lambda ctx, state: "result"
        router = FallbackRouter(state_dir=tmp_path)
        assert router.call(fn=fn, context=_ctx()) == "result"

    def test_partial_state_passed_through(self, tmp_path):
        received = {}

        def fn(ctx, state):
            received.update(state)
            return "ok"

        router = FallbackRouter(state_dir=tmp_path)
        router.call(fn=fn, context=_ctx(), partial_state={"progress": 42})
        assert received["progress"] == 42


class TestFallbackRouterRetry:
    def test_429_backoff_then_succeed(self, tmp_path):
        """Level 0: 429 backoff → eventually succeeds."""
        fn = _make_fn(succeed_on_attempt=3, error_code="429")
        router = FallbackRouter(state_dir=tmp_path)
        with patch("time.sleep"):  # don't actually sleep
            result = router.call(fn=fn, context=_ctx())
        assert result.startswith("ok-")

    def test_500_immediate_retry_no_sleep(self, tmp_path):
        """Level 0: 500 → retry behavior (no sleep)."""
        fn = _make_fn(succeed_on_attempt=2, error_code="500")
        router = FallbackRouter(state_dir=tmp_path)
        with patch("time.sleep") as mock_sleep:
            result = router.call(fn=fn, context=_ctx())
        # 500 mapped to "retry" behavior → sleep should NOT be called with positive arg
        for c in mock_sleep.call_args_list:
            assert c.args[0] == 0 or c.args[0] is None  # actual_wait == 0
        assert result.startswith("ok-")


class TestFallbackRouterExhaustion:
    def test_all_fail_raises_fallback_exhausted(self, tmp_path):
        fn = _always_fail()
        alert_calls = []
        router = FallbackRouter(
            state_dir=tmp_path,
            on_human_alert=lambda a: alert_calls.append(a),
        )
        with patch("time.sleep"):
            with pytest.raises(FallbackExhaustedError) as exc_info:
                router.call(fn=fn, context=_ctx())
        err = exc_info.value
        assert "exhausted" in str(err).lower()
        assert err.context["task"] == "test-task"
        assert isinstance(err.cause, RetryExhaustedError)
        assert len(alert_calls) == 1

    def test_401_immediate_alert(self, tmp_path):
        """401 → skip escalation, go straight to FallbackExhaustedError."""
        fn = _always_fail("HTTP 401 unauthorized")
        alert_calls = []
        router = FallbackRouter(
            state_dir=tmp_path,
            on_human_alert=lambda a: alert_calls.append(a),
        )
        with pytest.raises(FallbackExhaustedError):
            router.call(fn=fn, context=_ctx())
        assert len(alert_calls) == 1
        assert alert_calls[0]["severity"] == "critical"


class TestFallbackRouterFailover:
    def _make_failover_manager(self, providers=("anthropic", "openai", "google")):
        """Build a FailoverManager with given providers all credential-available."""
        from tokenpak.proxy.failover import FailoverConfig, ProviderEntry
        chain = [
            ProviderEntry(
                provider=p,
                model_map={"claude-opus-4-5": "gpt-4o" if p == "openai" else "gemini-1.5-pro" if p == "google" else "claude-opus-4-5"},
                credential_env=f"{p.upper()}_API_KEY",
            )
            for p in providers
        ]
        cfg = FailoverConfig(enabled=True, chain=chain)
        mgr = MagicMock()
        mgr.enabled = True

        # Build real iteration from config
        from tokenpak.proxy.failover import FailoverManager
        real_mgr = FailoverManager(config=cfg)
        # Patch env so credentials appear available
        with patch.dict("os.environ", {f"{p.upper()}_API_KEY": "test-key" for p in providers}):
            results = list(real_mgr.iter_providers("claude-opus-4-5", preferred="anthropic"))
        mgr.iter_providers.return_value = iter(results)
        mgr.enabled = True
        return mgr

    def test_failover_disabled_uses_default_switch(self, tmp_path):
        """When failover disabled, provider switch hook is None → RetryEngine uses its own default."""
        from tokenpak.proxy.failover import FailoverConfig
        cfg = FailoverConfig(enabled=False, chain=[])
        from tokenpak.proxy.failover import FailoverManager
        mgr = FailoverManager(config=cfg)

        providers_seen = []

        def fn(ctx, state):
            providers_seen.append(ctx.get("provider"))
            raise ValueError("HTTP 500 error")

        router = FallbackRouter(state_dir=tmp_path, failover_manager=mgr)
        alert_calls = []
        router.on_human_alert = lambda a: alert_calls.append(a)

        with patch("time.sleep"):
            with pytest.raises(FallbackExhaustedError):
                router.call(fn=fn, context=_ctx())

        # RetryEngine default chain: anthropic → openai → google
        assert "anthropic" in providers_seen

    def test_failover_enabled_drives_provider_switch(self, tmp_path):
        """When failover enabled, FailoverManager provides provider ordering."""
        from tokenpak.proxy.failover import FailoverConfig, ProviderEntry, FailoverManager
        chain = [
            ProviderEntry(provider="anthropic", model_map={}, credential_env="ANTHROPIC_API_KEY"),
            ProviderEntry(provider="openai", model_map={}, credential_env="OPENAI_API_KEY"),
        ]
        cfg = FailoverConfig(enabled=True, chain=chain)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k1", "OPENAI_API_KEY": "k2"}):
            mgr = FailoverManager(config=cfg)
            providers_seen = []

            def fn(ctx, state):
                providers_seen.append(ctx.get("provider"))
                if ctx.get("provider") == "anthropic":
                    raise ValueError("HTTP 500 error")
                return "ok-openai"

            router = FallbackRouter(state_dir=tmp_path, failover_manager=mgr)
            with patch("time.sleep"):
                result = router.call(fn=fn, context=_ctx())

        assert result == "ok-openai"
        assert "anthropic" in providers_seen


class TestFallbackRouterHandoff:
    def test_handoff_accepted(self, tmp_path):
        fn = _always_fail("HTTP 500 error")
        handoff_called = []

        def on_handoff(ctx, state):
            handoff_called.append(ctx)
            return True  # Accept handoff

        router = FallbackRouter(state_dir=tmp_path, on_handoff=on_handoff)
        with patch("time.sleep"):
            result = router.call(fn=fn, context=_ctx())

        assert result.get("_handoff") is True
        assert len(handoff_called) == 1

    def test_handoff_rejected_escalates_to_alert(self, tmp_path):
        fn = _always_fail("HTTP 500 error")
        alerts = []

        router = FallbackRouter(
            state_dir=tmp_path,
            on_handoff=lambda ctx, state: False,  # Reject
            on_human_alert=lambda a: alerts.append(a),
        )
        with patch("time.sleep"):
            with pytest.raises(FallbackExhaustedError):
                router.call(fn=fn, context=_ctx())

        assert len(alerts) == 1


class TestFunctionalAPI:
    def test_fallback_call_success(self, tmp_path):
        fn = lambda ctx, state: "functional-ok"
        result = fallback_call(fn=fn, context=_ctx(), state_dir=tmp_path)
        assert result == "functional-ok"

    def test_fallback_call_exhaustion(self, tmp_path):
        fn = _always_fail()
        alerts = []
        with patch("time.sleep"):
            with pytest.raises(FallbackExhaustedError):
                fallback_call(
                    fn=fn,
                    context=_ctx(),
                    state_dir=tmp_path,
                    on_human_alert=lambda a: alerts.append(a),
                )
        assert len(alerts) == 1

    def test_get_recent_fallback_events(self, tmp_path):
        with patch(
            "tokenpak.routing.fallback.load_recent_retry_events",
            return_value=[{"event": "run_start"}],
        ) as mock_fn:
            events = get_recent_fallback_events(n=5)
        mock_fn.assert_called_once_with(5)
        assert events == [{"event": "run_start"}]


class TestFallbackExhaustedError:
    def test_message_and_attributes(self, tmp_path):
        fn = _always_fail()
        ctx = _ctx(task="my-special-task")
        alerts = []
        router = FallbackRouter(
            state_dir=tmp_path,
            on_human_alert=lambda a: alerts.append(a),
        )
        with patch("time.sleep"):
            with pytest.raises(FallbackExhaustedError) as exc_info:
                router.call(fn=fn, context=ctx)

        err = exc_info.value
        assert "my-special-task" in str(err)
        assert err.context["task"] == "my-special-task"
        assert isinstance(err.cause, RetryExhaustedError)

    def test_state_dir_override(self, tmp_path):
        """State dir is passed to engine; state file should land under tmp_path."""
        fn = _always_fail()
        custom_dir = tmp_path / "custom_state"
        custom_dir.mkdir()
        router = FallbackRouter(
            state_dir=custom_dir,
            on_human_alert=lambda a: None,
        )
        with patch("time.sleep"):
            with pytest.raises(FallbackExhaustedError):
                router.call(fn=fn, context=_ctx())

        # State file should be written under custom_dir
        state_files = list(custom_dir.glob("*.json"))
        assert len(state_files) >= 1
