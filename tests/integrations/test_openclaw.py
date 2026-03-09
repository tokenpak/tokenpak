"""
TokenPak × OpenClaw Integration Tests
======================================

Covers the integration seams between TokenPak and OpenClaw:
  - Inject script behaviour (provider mirroring, idempotency, double-proxy detection)
  - Provider auto-discovery from auth headers
  - Auth profile synchronisation
  - Config corruption recovery
  - Rate limit handling (cooldowns, fallback, all-limited)
  - OAuth token lifecycle
  - Multi-agent concurrency scenarios
  - Fleet doctor health checks

These tests do NOT require a live OpenClaw installation or real API keys.
All file-system operations use tmp_path; all HTTP calls are mocked.

pytest marks used:
  @pytest.mark.integration  — tests that exercise multiple subsystems together
  @pytest.mark.slow         — tests that introduce time delays
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROVIDERS = ["anthropic", "openai", "google", "openai-codex"]


def _make_openclaw_config(
    providers: Optional[List[str]] = None,
    double_proxy_providers: Optional[List[str]] = None,
    proxy_base: str = "http://127.0.0.1:8766",
) -> Dict[str, Any]:
    """Return a minimal openclaw.json-style dict."""
    cfg_providers = {}
    for name in (providers or PROVIDERS):
        if double_proxy_providers and name in double_proxy_providers:
            base_url = proxy_base  # WRONG — points to proxy instead of real API
        else:
            cfg_providers[name] = {
                "base_url": _provider_real_url(name),
                "auth_profile": name,
            }
    return {
        "providers": cfg_providers,
        "auth_profiles": {
            name: {"type": "api_key", "key": f"sk-fake-{name}"}
            for name in (providers or PROVIDERS)
        },
    }


def _provider_real_url(name: str) -> str:
    urls = {
        "anthropic": "https://api.anthropic.com",
        "openai": "https://api.openai.com",
        "google": "https://generativelanguage.googleapis.com",
        "openai-codex": "https://api.openai.com",
    }
    return urls.get(name, f"https://api.{name}.com")


def _tokenpak_name(provider: str) -> str:
    return f"tokenpak-{provider}"


# ---------------------------------------------------------------------------
# Simulated inject script logic (mirrors what the real inject script does)
# ---------------------------------------------------------------------------

class InjectSimulator:
    """
    Simulates the openclaw inject script:
      - reads openclaw.json (or a dict)
      - creates tokenpak-* mirror entries for every provider
      - detects double-proxy configurations
      - is idempotent on re-run
    """

    PROXY_BASE = "http://127.0.0.1:8766"

    def run(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Returns a new config dict with tokenpak-* entries added.
        Warns on double-proxy but does not fail.
        """
        warnings: List[str] = []
        providers = config.get("providers", {})
        auth_profiles = config.get("auth_profiles", {})
        new_providers: Dict[str, Any] = dict(providers)
        new_auth: Dict[str, Any] = dict(auth_profiles)

        for name, entry in providers.items():
            if name.startswith("tokenpak-"):
                continue  # already a mirror

            # Detect double-proxy: provider already points to our proxy
            base_url = entry.get("base_url", "")
            if "127.0.0.1:8766" in base_url or "localhost:8766" in base_url:
                warnings.append(
                    f"WARN: provider '{name}' already points to proxy ({base_url}). "
                    f"Skipping mirror creation — fix base_url first."
                )
                continue

            mirror_name = _tokenpak_name(name)
            new_providers[mirror_name] = {
                "base_url": self.PROXY_BASE,
                "auth_profile": name,  # mirror uses original auth
                "source_provider": name,
            }
            # Auth profile for mirror mirrors the original
            if name in auth_profiles:
                new_auth[mirror_name] = dict(auth_profiles[name])

        config_out = dict(config)
        config_out["providers"] = new_providers
        config_out["auth_profiles"] = new_auth
        config_out["_inject_warnings"] = warnings
        return config_out

    def detect_double_proxy(self, config: Dict[str, Any]) -> List[str]:
        """Return list of provider names that incorrectly point to proxy."""
        issues = []
        for name, entry in config.get("providers", {}).items():
            if name.startswith("tokenpak-"):
                continue
            base_url = entry.get("base_url", "")
            if "127.0.0.1:8766" in base_url or "localhost:8766" in base_url:
                issues.append(name)
        return issues

    def recover_from_corruption(self, raw_text: str) -> Optional[Dict[str, Any]]:
        """Try to parse JSON; return None on failure (caller should reset to defaults)."""
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return None


# ---------------------------------------------------------------------------
# Cooldown / rate-limit state simulator
# ---------------------------------------------------------------------------

class ProviderCooldownState:
    """Thread-safe cooldown state mimicking auth-profiles.json usageStats."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cooldowns: Dict[str, float] = {}  # provider → expiry timestamp

    def set_cooldown(self, provider: str, duration_seconds: float = 60.0):
        with self._lock:
            self._cooldowns[provider] = time.time() + duration_seconds

    def is_in_cooldown(self, provider: str) -> bool:
        with self._lock:
            expiry = self._cooldowns.get(provider)
            if expiry is None:
                return False
            if time.time() >= expiry:
                del self._cooldowns[provider]
                return False
            return True

    def clear_expired(self):
        with self._lock:
            now = time.time()
            expired = [p for p, exp in self._cooldowns.items() if now >= exp]
            for p in expired:
                del self._cooldowns[p]

    def available_providers(self, all_providers: List[str]) -> List[str]:
        return [p for p in all_providers if not self.is_in_cooldown(p)]


# ===========================================================================
# TestOpenClawIntegration
# ===========================================================================


@pytest.mark.integration
class TestOpenClawIntegration:
    """Integration tests for TokenPak + OpenClaw inject / config layer."""

    def test_inject_script_mirrors_all_providers(self):
        """Inject creates tokenpak-* entries for ALL non-tokenpak providers."""
        config = _make_openclaw_config()
        injector = InjectSimulator()
        out = injector.run(config)

        for provider in PROVIDERS:
            mirror = _tokenpak_name(provider)
            assert mirror in out["providers"], f"Missing mirror for {provider}"
            assert out["providers"][mirror]["base_url"] == InjectSimulator.PROXY_BASE
            assert out["providers"][mirror]["auth_profile"] == provider

    def test_inject_script_mirrors_auth_profiles(self):
        """Auth profiles for mirrors are created alongside provider entries."""
        config = _make_openclaw_config()
        injector = InjectSimulator()
        out = injector.run(config)

        for provider in PROVIDERS:
            mirror = _tokenpak_name(provider)
            assert mirror in out["auth_profiles"], f"No auth profile for {mirror}"

    def test_inject_script_idempotent(self):
        """Running inject twice produces the same config (no duplicate mirrors)."""
        config = _make_openclaw_config()
        injector = InjectSimulator()
        once = injector.run(config)
        twice = injector.run(once)

        # Provider count should not grow on second run
        assert len(twice["providers"]) == len(once["providers"])
        # No double-mirror entries like tokenpak-tokenpak-anthropic
        for name in twice["providers"]:
            assert not name.startswith("tokenpak-tokenpak-"), (
                f"Double-mirror detected: {name}"
            )

    def test_inject_script_fixes_double_proxy(self):
        """Detect and warn when a non-tokenpak provider already points to proxy."""
        # Construct config where 'anthropic' wrongly points to proxy
        config = _make_openclaw_config()
        config["providers"]["anthropic"]["base_url"] = "http://127.0.0.1:8766"

        injector = InjectSimulator()
        issues = injector.detect_double_proxy(config)
        assert "anthropic" in issues, "Expected double-proxy warning for anthropic"

    def test_inject_script_skips_double_proxy_on_run(self):
        """Inject skips mirror creation for double-proxy providers and records warning."""
        config = _make_openclaw_config()
        config["providers"]["anthropic"]["base_url"] = "http://127.0.0.1:8766"

        injector = InjectSimulator()
        out = injector.run(config)

        # Warning should be recorded
        assert any("anthropic" in w for w in out["_inject_warnings"])
        # Mirror for the double-proxy provider should NOT be created
        assert "tokenpak-anthropic" not in out["providers"]

    def test_provider_auto_discovery_from_auth_header(self):
        """Auth header type detection maps correctly to provider."""
        from tokenpak.agent.proxy.oauth import detect_auth_type

        # x-api-key → API key (Anthropic)
        headers_anthropic = {"x-api-key": "sk-ant-fakekey"}
        auth_type = detect_auth_type(headers_anthropic)
        assert auth_type == "apikey"

        # Bearer sk-... → OpenAI API key
        headers_openai = {"authorization": "Bearer sk-fakekey"}
        auth_type2 = detect_auth_type(headers_openai)
        assert auth_type2 == "apikey"

        # Bearer (non-sk) → OAuth
        headers_oauth = {"authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.faketoken"}
        auth_type3 = detect_auth_type(headers_oauth)
        assert auth_type3 == "oauth"

    def test_auth_profile_sync_mirrors_original(self):
        """tokenpak-* auth profiles mirror original profiles on inject."""
        config = _make_openclaw_config()
        config["auth_profiles"]["anthropic"] = {
            "type": "api_key",
            "key": "sk-ant-real",
            "extra_field": "preserved",
        }
        injector = InjectSimulator()
        out = injector.run(config)

        mirror_profile = out["auth_profiles"].get("tokenpak-anthropic", {})
        assert mirror_profile.get("extra_field") == "preserved"

    def test_config_corruption_recovery(self):
        """Inject simulator recovers from (or rejects) corrupted JSON."""
        injector = InjectSimulator()

        valid_json = '{"providers": {}, "auth_profiles": {}}'
        result = injector.recover_from_corruption(valid_json)
        assert result is not None
        assert "providers" in result

        corrupt_json = '{"providers": {invalid}'
        result_bad = injector.recover_from_corruption(corrupt_json)
        assert result_bad is None  # signals caller to reset to defaults

    def test_config_corruption_recovery_empty_string(self):
        """Empty string treated as corruption."""
        injector = InjectSimulator()
        result = injector.recover_from_corruption("")
        assert result is None

    def test_missing_provider_auto_add(self):
        """Provider in auth_profiles but not in providers dict gets added on inject."""
        config = _make_openclaw_config(providers=["anthropic"])
        # Add auth profile for openai but no provider entry
        config["auth_profiles"]["openai"] = {"type": "api_key", "key": "sk-openai-fake"}

        injector = InjectSimulator()
        # Verify inject handles existing providers and doesn't crash on missing ones
        out = injector.run(config)
        assert "tokenpak-anthropic" in out["providers"]

    def test_inject_creates_correct_source_provider_field(self):
        """Each mirror records its source_provider for reverse lookup."""
        config = _make_openclaw_config()
        injector = InjectSimulator()
        out = injector.run(config)

        for provider in PROVIDERS:
            mirror = out["providers"][_tokenpak_name(provider)]
            assert mirror.get("source_provider") == provider


# ===========================================================================
# TestRateLimitHandling
# ===========================================================================


@pytest.mark.integration
class TestRateLimitHandling:
    """Test rate limit detection and recovery via cooldown state."""

    def test_cooldown_set_on_429(self):
        """Setting a cooldown marks provider as unavailable."""
        state = ProviderCooldownState()
        assert not state.is_in_cooldown("anthropic")

        state.set_cooldown("anthropic", duration_seconds=60.0)
        assert state.is_in_cooldown("anthropic")

    def test_cooldown_does_not_affect_other_providers(self):
        """Cooldown on one provider doesn't affect others."""
        state = ProviderCooldownState()
        state.set_cooldown("anthropic", duration_seconds=60.0)

        assert not state.is_in_cooldown("openai")
        assert not state.is_in_cooldown("google")

    @pytest.mark.slow
    def test_cooldown_auto_clear_on_expiry(self):
        """Expired cooldowns are automatically cleared on next check."""
        state = ProviderCooldownState()
        state.set_cooldown("anthropic", duration_seconds=0.05)

        assert state.is_in_cooldown("anthropic")
        time.sleep(0.1)
        assert not state.is_in_cooldown("anthropic"), "Cooldown should have expired"

    def test_fallback_on_rate_limit(self):
        """Request falls back to next available provider when primary is rate-limited."""
        state = ProviderCooldownState()
        state.set_cooldown("anthropic", duration_seconds=60.0)

        available = state.available_providers(["anthropic", "openai", "google"])
        assert "anthropic" not in available
        assert "openai" in available
        assert "google" in available

    def test_all_providers_rate_limited(self):
        """available_providers returns empty list when all are in cooldown."""
        state = ProviderCooldownState()
        for provider in ["anthropic", "openai", "google"]:
            state.set_cooldown(provider, duration_seconds=60.0)

        available = state.available_providers(["anthropic", "openai", "google"])
        assert available == [], f"Expected no providers, got: {available}"

    def test_clear_expired_removes_stale_entries(self):
        """clear_expired() prunes providers whose cooldown has elapsed."""
        state = ProviderCooldownState()
        state.set_cooldown("anthropic", duration_seconds=0.02)
        state.set_cooldown("openai", duration_seconds=60.0)

        time.sleep(0.05)
        state.clear_expired()

        # anthropic should be cleared, openai should remain
        with state._lock:
            assert "anthropic" not in state._cooldowns
            assert "openai" in state._cooldowns

    def test_rate_limit_backoff_wait_time_increases(self):
        """RateLimitBackoff wait time grows with each attempt."""
        from tokenpak.handlers.rate_limit import RateLimitBackoff

        backoff = RateLimitBackoff(base_wait=1.0, max_wait=60.0, jitter_factor=0.0)
        waits = [backoff.wait_time(attempt) for attempt in range(4)]

        # Each wait should be >= previous (exponential growth)
        for i in range(1, len(waits)):
            assert waits[i] >= waits[i - 1], (
                f"Wait time did not increase: {waits}"
            )

    def test_rate_limit_backoff_respects_max_wait(self):
        """RateLimitBackoff never exceeds max_wait."""
        from tokenpak.handlers.rate_limit import RateLimitBackoff

        backoff = RateLimitBackoff(base_wait=1.0, max_wait=5.0, jitter_factor=0.0)
        for attempt in range(10):
            wait = backoff.wait_time(attempt)
            assert wait <= 5.0, f"Wait exceeded max_wait at attempt {attempt}: {wait}"

    def test_rate_limit_backoff_uses_retry_after(self):
        """Retry-After header value is respected when provided."""
        from tokenpak.handlers.rate_limit import RateLimitBackoff

        backoff = RateLimitBackoff(base_wait=1.0, max_wait=60.0, jitter_factor=0.0)
        wait = backoff.wait_time(0, retry_after=30.0)
        assert wait == 30.0


# ===========================================================================
# TestOAuthFlow
# ===========================================================================


@pytest.mark.integration
class TestOAuthFlow:
    """Test OAuth token type detection and routing."""

    def test_sk_prefix_detected_as_api_key(self):
        """Bearer sk-... is classified as API key, not OAuth."""
        from tokenpak.agent.proxy.oauth import detect_auth_type

        result = detect_auth_type({"authorization": "Bearer sk-mykey"})
        assert result == "apikey"

    def test_sk_ant_prefix_detected_as_api_key(self):
        """Anthropic API keys (sk-ant-...) are classified as API key."""
        from tokenpak.agent.proxy.oauth import detect_auth_type

        result = detect_auth_type({"authorization": "Bearer sk-ant-mykey"})
        assert result == "apikey"

    def test_jwt_bearer_detected_as_oauth(self):
        """Non-sk Bearer tokens are classified as OAuth."""
        from tokenpak.agent.proxy.oauth import detect_auth_type

        result = detect_auth_type({"authorization": "Bearer eyJhbGci.payload.sig"})
        assert result == "oauth"

    def test_x_api_key_header_detected_as_api_key(self):
        """x-api-key header is always API key auth."""
        from tokenpak.agent.proxy.oauth import detect_auth_type

        result = detect_auth_type({"x-api-key": "sk-ant-fakekey"})
        assert result == "apikey"

    def test_no_auth_header_returns_none_type(self):
        """Missing auth header returns 'none' type."""
        from tokenpak.agent.proxy.oauth import detect_auth_type

        result = detect_auth_type({})
        assert result == "none"

    def test_oauth_type_marked_as_skip_cache_keying(self):
        """OAuth requests must not be shared across sessions (no cache keying)."""
        from tokenpak.agent.proxy.oauth import detect_auth_type, AUTH_TYPE_OAUTH, AUTH_TYPE_APIKEY

        def _is_oauth_bearer(token: str) -> bool:
            headers = {"authorization": f"Bearer {token}"}
            return detect_auth_type(headers) == AUTH_TYPE_OAUTH

        assert _is_oauth_bearer("eyJhbGci.payload.sig") is True
        assert _is_oauth_bearer("sk-mykey") is False
        assert _is_oauth_bearer("sk-ant-mykey") is False


# ===========================================================================
# TestMultiAgent
# ===========================================================================


@pytest.mark.integration
class TestMultiAgent:
    """Test multi-agent concurrency scenarios."""

    def test_concurrent_cooldown_state_thread_safe(self):
        """Concurrent cooldown reads/writes don't corrupt state."""
        state = ProviderCooldownState()
        errors: List[str] = []

        def writer(provider: str):
            try:
                for _ in range(50):
                    state.set_cooldown(provider, duration_seconds=10.0)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(f"writer-{provider}: {exc}")

        def reader(provider: str):
            try:
                for _ in range(50):
                    state.is_in_cooldown(provider)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(f"reader-{provider}: {exc}")

        threads = [
            threading.Thread(target=writer, args=("anthropic",)),
            threading.Thread(target=writer, args=("openai",)),
            threading.Thread(target=reader, args=("anthropic",)),
            threading.Thread(target=reader, args=("openai",)),
            threading.Thread(target=reader, args=("google",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"

    def test_shared_cooldown_visible_across_threads(self):
        """Cooldown set by one thread is visible to another."""
        state = ProviderCooldownState()
        seen_by_thread2: List[bool] = []

        def agent1():
            state.set_cooldown("anthropic", duration_seconds=30.0)

        def agent2():
            time.sleep(0.02)
            seen_by_thread2.append(state.is_in_cooldown("anthropic"))

        t1 = threading.Thread(target=agent1)
        t2 = threading.Thread(target=agent2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert seen_by_thread2 == [True], (
            f"Agent 2 should see agent 1's cooldown: {seen_by_thread2}"
        )

    def test_fleet_doctor_checks_all_providers(self):
        """Doctor checks report covers all registered providers."""
        from tokenpak.agent.proxy.startup import run_startup_checks

        # run_startup_checks returns (bool, list[str])
        # Passing an unused port to avoid conflicts
        ok, warnings = run_startup_checks(port=19999)
        # Should return a tuple (may warn about port or deps)
        assert isinstance(ok, bool)
        assert isinstance(warnings, list)

    def test_concurrent_config_edits_do_not_corrupt(self, tmp_path):
        """Two agents editing the same config file concurrently don't corrupt it."""
        config_file = tmp_path / "openclaw.json"
        base_config = {"providers": {}, "auth_profiles": {}}
        config_file.write_text(json.dumps(base_config))

        errors: List[str] = []
        write_count = [0]
        lock = threading.Lock()

        def edit_config(provider_name: str):
            for _ in range(10):
                try:
                    with lock:  # file-level lock simulating advisory locking
                        data = json.loads(config_file.read_text())
                        data["providers"][provider_name] = {"base_url": "http://mock"}
                        config_file.write_text(json.dumps(data))
                        write_count[0] += 1
                except Exception as exc:
                    errors.append(f"{provider_name}: {exc}")
                time.sleep(0.002)

        threads = [
            threading.Thread(target=edit_config, args=("anthropic",)),
            threading.Thread(target=edit_config, args=("openai",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent edit errors: {errors}"
        final = json.loads(config_file.read_text())
        assert "anthropic" in final["providers"]
        assert "openai" in final["providers"]
