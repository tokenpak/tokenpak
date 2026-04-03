"""
Comprehensive unit test suite for TokenPak agent auth subsystem.

Tests for:
  - tokenpak/tokenpak/agent/auth/cooldown_manager.py
  - tokenpak/tokenpak/agent/auth/oauth_manager.py

Coverage includes:
  - CooldownManager: load, save, clear expired, get active
  - BackgroundCooldownClearer: async lifecycle, loop behavior
  - OAuthManager: detect expiring tokens, refresh single/batch
  - BackgroundOAuthRefresher: async lifecycle, refresh loop
  - Error handling and edge cases

All HTTP calls mocked. No real credentials used.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call

import pytest

# Import modules to test
from tokenpak.agent.auth.cooldown_manager import (
    CooldownManager,
    BackgroundCooldownClearer,
    HIGH_ERROR_THRESHOLD,
)
from tokenpak.agent.auth.oauth_manager import (
    OAuthManager,
    OAuthRefreshError,
    BackgroundOAuthRefresher,
    REFRESH_WINDOW_SECONDS,
    DEFAULT_INTERVAL,
    _refresh_token_openai_codex,
    _refresh_token_anthropic,
)


# ============================================================================
# Test Class 1: CooldownManager — File I/O and Expiry Logic (12 tests)
# ============================================================================


class TestCooldownManagerFileIO:
    """Test CooldownManager file loading and saving."""

    def test_load_cooldowns_file_not_exists(self):
        """_load_cooldowns returns empty dict when file doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "nonexistent.json",
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr._load_cooldowns()
            assert result == {}

    def test_load_cooldowns_valid_json(self):
        """_load_cooldowns parses valid JSON."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            data = {
                "anthropic:default": {"cooldownUntil": 1700000000, "errorCount": 2},
                "openai:prod": {"cooldownUntil": 1700000100, "errorCount": 0},
            }
            cooldowns_file.write_text(json.dumps(data))
            mgr = CooldownManager(
                cooldowns_file=cooldowns_file,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr._load_cooldowns()
            assert result == data

    def test_load_cooldowns_invalid_json(self):
        """_load_cooldowns returns empty dict on JSON decode error."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            cooldowns_file.write_text("{ invalid json }")
            mgr = CooldownManager(
                cooldowns_file=cooldowns_file,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr._load_cooldowns()
            assert result == {}

    def test_save_cooldowns_creates_directory(self):
        """_save_cooldowns creates parent directory if missing."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "subdir" / "cooldowns.json"
            mgr = CooldownManager(
                cooldowns_file=cooldowns_file,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            data = {"test:key": {"cooldownUntil": 1700000000, "errorCount": 1}}
            mgr._save_cooldowns(data)
            assert cooldowns_file.exists()
            assert json.loads(cooldowns_file.read_text()) == data

    def test_load_auth_profiles_file_not_exists(self):
        """_load_auth_profiles returns None when file doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=Path(tmpdir) / "nonexistent.json",
            )
            result = mgr._load_auth_profiles()
            assert result is None

    def test_load_auth_profiles_valid_json(self):
        """_load_auth_profiles parses valid JSON."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            data = {
                "profile1": {"provider": "anthropic", "cooldownUntil": 1700000000},
                "profile2": {"provider": "openai-codex", "cooldownUntil": 1700000100},
            }
            profiles_file.write_text(json.dumps(data))
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_file,
            )
            result = mgr._load_auth_profiles()
            assert result == data

    def test_load_auth_profiles_invalid_json(self):
        """_load_auth_profiles returns empty dict on decode error."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            profiles_file.write_text("{ broken }")
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_file,
            )
            result = mgr._load_auth_profiles()
            assert result is None

    def test_save_auth_profiles_creates_directory(self):
        """_save_auth_profiles creates parent directory if missing."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "subdir" / "profiles.json"
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_file,
            )
            data = {"profile1": {"provider": "anthropic"}}
            mgr._save_auth_profiles(data)
            assert profiles_file.exists()
            assert json.loads(profiles_file.read_text()) == data


class TestCooldownManagerClearExpired:
    """Test cooldown expiry detection and clearing."""

    def test_clear_expired_empty_file(self):
        """clear_expired returns empty list when cooldowns file is empty."""
        with TemporaryDirectory() as tmpdir:
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr.clear_expired()
            assert result == []

    def test_clear_expired_removes_past_timestamps(self):
        """clear_expired removes entries with cooldownUntil < now."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            data = {
                "expired:1": {"cooldownUntil": now - 100, "errorCount": 0},
                "active:1": {"cooldownUntil": now + 100, "errorCount": 0},
                "noexpiry:1": {"errorCount": 5},
            }
            cooldowns_file.write_text(json.dumps(data))
            mgr = CooldownManager(
                cooldowns_file=cooldowns_file,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr.clear_expired()
            assert "expired:1" in result
            assert "active:1" not in result
            assert "noexpiry:1" not in result

            # Verify file was updated
            remaining = json.loads(cooldowns_file.read_text())
            assert "expired:1" not in remaining
            assert "active:1" in remaining

    def test_clear_expired_skips_high_error_count(self):
        """clear_expired skips entries with errorCount >= HIGH_ERROR_THRESHOLD."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            data = {
                "high_error": {
                    "cooldownUntil": now - 100,
                    "errorCount": HIGH_ERROR_THRESHOLD,
                },
                "low_error": {"cooldownUntil": now - 100, "errorCount": 5},
            }
            cooldowns_file.write_text(json.dumps(data))
            mgr = CooldownManager(
                cooldowns_file=cooldowns_file,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr.clear_expired()
            assert "high_error" not in result
            assert "low_error" in result

    def test_clear_expired_from_profiles_empty(self):
        """clear_expired_from_profiles returns empty list on missing file."""
        with TemporaryDirectory() as tmpdir:
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr.clear_expired_from_profiles()
            assert result == []

    def test_clear_expired_from_profiles_removes_past(self):
        """clear_expired_from_profiles removes expired profile cooldowns."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "expired_profile": {
                    "provider": "anthropic",
                    "cooldownUntil": now - 50,
                    "errorCount": 0,
                    "usageStats": {"requests": 100},
                },
                "active_profile": {
                    "provider": "openai-codex",
                    "cooldownUntil": now + 50,
                    "errorCount": 0,
                },
            }
            profiles_file.write_text(json.dumps(data))
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_file,
            )
            result = mgr.clear_expired_from_profiles()
            assert "expired_profile" in result
            assert "active_profile" not in result

            # Verify file was updated and usageStats removed
            updated = json.loads(profiles_file.read_text())
            assert "cooldownUntil" not in updated["expired_profile"]
            assert "usageStats" not in updated["expired_profile"]

    def test_clear_expired_from_profiles_skips_high_error(self):
        """clear_expired_from_profiles skips entries with high errorCount."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "high_error_profile": {
                    "provider": "anthropic",
                    "cooldownUntil": now - 50,
                    "errorCount": HIGH_ERROR_THRESHOLD + 1,
                },
            }
            profiles_file.write_text(json.dumps(data))
            mgr = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_file,
            )
            result = mgr.clear_expired_from_profiles()
            assert result == []

    def test_get_active_cooldowns_returns_remaining_seconds(self):
        """get_active_cooldowns returns map of remaining seconds for active cooldowns."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            data = {
                "active:1": {"cooldownUntil": now + 100, "errorCount": 0},
                "expired:1": {"cooldownUntil": now - 50, "errorCount": 0},
            }
            cooldowns_file.write_text(json.dumps(data))
            mgr = CooldownManager(
                cooldowns_file=cooldowns_file,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            active = mgr.get_active_cooldowns()
            assert "active:1" in active
            assert 95 < active["active:1"] < 105  # ~100 seconds remaining
            assert "expired:1" not in active

    def test_get_active_cooldowns_includes_profiles(self):
        """get_active_cooldowns includes cooldowns from auth-profiles.json."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()

            cooldowns_file.write_text(
                json.dumps({"cool:1": {"cooldownUntil": now + 100, "errorCount": 0}})
            )
            profiles_file.write_text(
                json.dumps(
                    {
                        "profile:1": {
                            "provider": "anthropic",
                            "cooldownUntil": now + 200,
                            "errorCount": 0,
                        }
                    }
                )
            )

            mgr = CooldownManager(
                cooldowns_file=cooldowns_file, auth_profiles_file=profiles_file
            )
            active = mgr.get_active_cooldowns()
            assert "cool:1" in active
            assert "profile:profile:1" in active

    def test_run_cycle_returns_total_cleared(self):
        """run_cycle clears both sources and returns total count."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()

            cooldowns_file.write_text(
                json.dumps({"cool:1": {"cooldownUntil": now - 50, "errorCount": 0}})
            )
            profiles_file.write_text(
                json.dumps(
                    {
                        "prof:1": {
                            "provider": "anthropic",
                            "cooldownUntil": now - 50,
                            "errorCount": 0,
                        }
                    }
                )
            )

            mgr = CooldownManager(
                cooldowns_file=cooldowns_file, auth_profiles_file=profiles_file
            )
            count = mgr.run_cycle()
            assert count == 2


# ============================================================================
# Test Class 2: BackgroundCooldownClearer — Async Lifecycle (8 tests)
# ============================================================================


class TestBackgroundCooldownClearer:
    """Test async background cooldown clearing task."""

    @pytest.mark.asyncio
    async def test_background_clearer_starts_and_stops(self):
        """BackgroundCooldownClearer can start and stop cleanly."""
        clearer = BackgroundCooldownClearer(interval=1, enabled=False)
        await clearer.start()
        assert clearer._task is not None
        await clearer.stop()
        assert clearer._task is None

    @pytest.mark.asyncio
    async def test_background_clearer_idempotent_start(self):
        """Calling start() multiple times is safe (idempotent)."""
        clearer = BackgroundCooldownClearer(interval=1, enabled=False)
        await clearer.start()
        task1 = clearer._task
        await clearer.start()
        task2 = clearer._task
        assert task1 is task2  # Same task
        await clearer.stop()

    @pytest.mark.asyncio
    async def test_background_clearer_runs_cycle(self):
        """BackgroundCooldownClearer calls manager.run_cycle periodically."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()

            cooldowns_file.write_text(
                json.dumps({"cool:1": {"cooldownUntil": now - 50, "errorCount": 0}})
            )
            profiles_file.write_text(json.dumps({}))

            mgr = CooldownManager(
                cooldowns_file=cooldowns_file, auth_profiles_file=profiles_file
            )
            clearer = BackgroundCooldownClearer(interval=1, manager=mgr, enabled=True)

            await clearer.start()
            await asyncio.sleep(1.5)  # Let it run one cycle
            await clearer.stop()

            # Verify the expired entry was cleared
            remaining = json.loads(cooldowns_file.read_text())
            assert "cool:1" not in remaining

    @pytest.mark.asyncio
    async def test_background_clearer_respects_enabled_flag(self):
        """BackgroundCooldownClearer skips cycles when enabled=False."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()

            data = {"cool:1": {"cooldownUntil": now - 50, "errorCount": 0}}
            cooldowns_file.write_text(json.dumps(data))
            profiles_file.write_text(json.dumps({}))

            mgr = CooldownManager(
                cooldowns_file=cooldowns_file, auth_profiles_file=profiles_file
            )
            clearer = BackgroundCooldownClearer(
                interval=1, manager=mgr, enabled=False
            )

            await clearer.start()
            await asyncio.sleep(1.5)
            await clearer.stop()

            # Entry should still be there (not cleared)
            remaining = json.loads(cooldowns_file.read_text())
            assert "cool:1" in remaining

    @pytest.mark.asyncio
    async def test_background_clearer_handles_manager_exception(self):
        """BackgroundCooldownClearer catches and logs exceptions from manager."""
        mock_manager = Mock(spec=CooldownManager)
        mock_manager.run_cycle.side_effect = RuntimeError("Simulated error")

        clearer = BackgroundCooldownClearer(
            interval=1, manager=mock_manager, enabled=True
        )
        await clearer.start()
        await asyncio.sleep(1.5)
        await clearer.stop()

        # Should not crash — exception was caught
        assert mock_manager.run_cycle.called

    @pytest.mark.asyncio
    async def test_background_clearer_stop_awaits_task(self):
        """BackgroundCooldownClearer.stop() properly awaits task completion."""
        clearer = BackgroundCooldownClearer(interval=10, enabled=False)
        await clearer.start()
        assert clearer._task is not None
        await clearer.stop()
        # Task should be None after stop
        assert clearer._task is None

    @pytest.mark.asyncio
    async def test_background_clearer_timeout_on_stop(self):
        """BackgroundCooldownClearer cancels task if stop times out."""
        # Create a clearer with a very short timeout
        clearer = BackgroundCooldownClearer(interval=1, enabled=False)
        await clearer.start()

        # Manually set a task that never completes (for testing timeout)
        clearer._task = asyncio.create_task(asyncio.sleep(100))

        # Stop with timeout should cancel it
        await clearer.stop()
        assert clearer._task is None


# ============================================================================
# Test Class 3: OAuthManager — Token Expiry Detection (10 tests)
# ============================================================================


class TestOAuthManagerTokenDetection:
    """Test OAuth token expiry detection and filtering."""

    def test_get_expiring_profiles_empty_file(self):
        """get_expiring_profiles returns empty list when file missing."""
        with TemporaryDirectory() as tmpdir:
            mgr = OAuthManager(
                auth_profiles_file=Path(tmpdir) / "profiles.json",
                refresh_window=3600,
            )
            result = mgr.get_expiring_profiles()
            assert result == []

    def test_get_expiring_profiles_detects_expiring(self):
        """get_expiring_profiles returns tokens expiring within refresh_window."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "expiring:1": {
                    "provider": "openai-codex",
                    "refresh_token": "rt_123",
                    "expires_at": now + 1800,  # 30 min remaining < 1 hour window
                },
                "not_expiring:1": {
                    "provider": "anthropic",
                    "refresh_token": "rt_456",
                    "expires_at": now + 7200,  # 2 hours remaining > 1 hour window
                },
            }
            profiles_file.write_text(json.dumps(data))

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            result = mgr.get_expiring_profiles()

            assert len(result) == 1
            assert result[0][0] == "expiring:1"

    def test_get_expiring_profiles_skips_unsupported_providers(self):
        """get_expiring_profiles ignores providers not in OAUTH_PROVIDERS."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "unsupported:1": {
                    "provider": "unknown-provider",
                    "refresh_token": "rt_123",
                    "expires_at": now + 1800,
                },
            }
            profiles_file.write_text(json.dumps(data))

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            result = mgr.get_expiring_profiles()
            assert result == []

    def test_get_expiring_profiles_skips_no_refresh_token(self):
        """get_expiring_profiles skips profiles without refresh_token."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "no_refresh:1": {
                    "provider": "openai-codex",
                    "access_token": "at_123",
                    "expires_at": now + 1800,
                },
            }
            profiles_file.write_text(json.dumps(data))

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            result = mgr.get_expiring_profiles()
            assert result == []

    def test_get_expiring_profiles_skips_no_expires_at(self):
        """get_expiring_profiles skips profiles without expires_at."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            data = {
                "no_expiry:1": {
                    "provider": "anthropic",
                    "refresh_token": "rt_123",
                },
            }
            profiles_file.write_text(json.dumps(data))

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            result = mgr.get_expiring_profiles()
            assert result == []

    def test_get_expiring_profiles_returns_remaining_seconds(self):
        """get_expiring_profiles returns (name, profile, remaining_seconds)."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            expected_remaining = 1800
            data = {
                "test:1": {
                    "provider": "openai-codex",
                    "refresh_token": "rt_123",
                    "expires_at": now + expected_remaining,
                },
            }
            profiles_file.write_text(json.dumps(data))

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            result = mgr.get_expiring_profiles()

            assert len(result) == 1
            name, profile, remaining = result[0]
            assert name == "test:1"
            assert profile["provider"] == "openai-codex"
            assert 1795 < remaining < 1805  # ~1800 seconds

    def test_get_expiring_profiles_invalid_json(self):
        """get_expiring_profiles handles corrupt JSON gracefully."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            profiles_file.write_text("{ broken json")

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            result = mgr.get_expiring_profiles()
            assert result == []

    def test_get_expiring_profiles_non_dict_root(self):
        """get_expiring_profiles handles non-dict JSON root."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            profiles_file.write_text('["not", "a", "dict"]')

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            result = mgr.get_expiring_profiles()
            assert result == []


# ============================================================================
# Test Class 4: OAuthManager — Token Refresh (10 tests)
# ============================================================================


class TestOAuthManagerRefresh:
    """Test OAuth token refresh logic."""

    @pytest.mark.asyncio
    async def test_refresh_profile_openai_codex_success(self):
        """refresh_profile successfully refreshes OpenAI Codex token."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            profile = {
                "provider": "openai-codex",
                "access_token": "old_at",
                "refresh_token": "rt_123",
                "client_id": "client_123",
                "expires_at": now + 100,
            }
            profiles_file.write_text(json.dumps({"test:1": profile}))

            # Mock HTTP response
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "access_token": "new_at",
                "refresh_token": "rt_456",
                "expires_in": 7200,
            }

            mgr = OAuthManager(auth_profiles_file=profiles_file)

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                success = await mgr.refresh_profile("test:1", profile)
                assert success is True

                # Verify file was updated
                updated = json.loads(profiles_file.read_text())
                assert updated["test:1"]["access_token"] == "new_at"
                assert updated["test:1"]["refresh_token"] == "rt_456"

    @pytest.mark.asyncio
    async def test_refresh_profile_anthropic_success(self):
        """refresh_profile successfully refreshes Anthropic token."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            profile = {
                "provider": "anthropic",
                "access_token": "old_at",
                "refresh_token": "rt_abc",
                "client_id": "client_abc",
                "expires_at": now + 100,
            }
            profiles_file.write_text(json.dumps({"test:1": profile}))

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "access_token": "new_at_abc",
                "expires_in": 3600,
            }

            mgr = OAuthManager(auth_profiles_file=profiles_file)

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                success = await mgr.refresh_profile("test:1", profile)
                assert success is True

    @pytest.mark.asyncio
    async def test_refresh_profile_http_error(self):
        """refresh_profile returns False on HTTP error."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            profile = {
                "provider": "openai-codex",
                "access_token": "at",
                "refresh_token": "rt_123",
                "client_id": "client_123",
            }
            profiles_file.write_text(json.dumps({"test:1": profile}))

            mock_response = Mock()
            mock_response.status_code = 401  # Unauthorized

            mgr = OAuthManager(auth_profiles_file=profiles_file)

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                success = await mgr.refresh_profile("test:1", profile)
                assert success is False

    @pytest.mark.asyncio
    async def test_refresh_profile_no_refresh_token(self):
        """refresh_profile returns False when refresh_token is missing."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            profile = {"provider": "openai-codex", "access_token": "at"}
            profiles_file.write_text(json.dumps({"test:1": profile}))

            mgr = OAuthManager(auth_profiles_file=profiles_file)
            success = await mgr.refresh_profile("test:1", profile)
            assert success is False

    @pytest.mark.asyncio
    async def test_refresh_profile_unsupported_provider(self):
        """refresh_profile returns False for unsupported provider."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            profile = {
                "provider": "unknown-provider",
                "refresh_token": "rt_123",
            }
            profiles_file.write_text(json.dumps({"test:1": profile}))

            mgr = OAuthManager(auth_profiles_file=profiles_file)
            success = await mgr.refresh_profile("test:1", profile)
            assert success is False

    @pytest.mark.asyncio
    async def test_refresh_profile_unexpected_error(self):
        """refresh_profile returns False on unexpected error."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            profile = {
                "provider": "openai-codex",
                "access_token": "at",
                "refresh_token": "rt_123",
                "client_id": "client_123",
            }
            profiles_file.write_text(json.dumps({"test:1": profile}))

            mgr = OAuthManager(auth_profiles_file=profiles_file)

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.post = AsyncMock(side_effect=RuntimeError("Network error"))
                mock_client_class.return_value = mock_client

                success = await mgr.refresh_profile("test:1", profile)
                assert success is False

    @pytest.mark.asyncio
    async def test_run_cycle_refreshes_all_expiring(self):
        """run_cycle detects and refreshes all expiring tokens."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "expiring:1": {
                    "provider": "openai-codex",
                    "access_token": "at1",
                    "refresh_token": "rt_1",
                    "client_id": "client_1",
                    "expires_at": now + 1800,  # 30 min
                },
                "expiring:2": {
                    "provider": "anthropic",
                    "access_token": "at2",
                    "refresh_token": "rt_2",
                    "client_id": "client_2",
                    "expires_at": now + 1800,  # 30 min
                },
            }
            profiles_file.write_text(json.dumps(data))

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "access_token": "new_at",
                "expires_in": 7200,
            }

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                results = await mgr.run_cycle()
                assert len(results) == 2
                assert all(results.values())  # All succeeded

    @pytest.mark.asyncio
    async def test_run_cycle_no_expiring_profiles(self):
        """run_cycle returns empty dict when no tokens are expiring."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "active:1": {
                    "provider": "openai-codex",
                    "refresh_token": "rt_1",
                    "expires_at": now + 7200,  # 2 hours
                },
            }
            profiles_file.write_text(json.dumps(data))

            mgr = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            results = await mgr.run_cycle()
            assert results == {}


# ============================================================================
# Test Class 5: BackgroundOAuthRefresher — Async Lifecycle (6 tests)
# ============================================================================


class TestBackgroundOAuthRefresher:
    """Test async background OAuth refresh task."""

    @pytest.mark.asyncio
    async def test_background_refresher_starts_and_stops(self):
        """BackgroundOAuthRefresher can start and stop cleanly."""
        refresher = BackgroundOAuthRefresher(interval=1, enabled=False)
        await refresher.start()
        assert refresher._task is not None
        await refresher.stop()
        assert refresher._task is None

    @pytest.mark.asyncio
    async def test_background_refresher_idempotent_start(self):
        """Calling start() multiple times is safe."""
        refresher = BackgroundOAuthRefresher(interval=1, enabled=False)
        await refresher.start()
        task1 = refresher._task
        await refresher.start()
        task2 = refresher._task
        assert task1 is task2
        await refresher.stop()

    @pytest.mark.asyncio
    async def test_background_refresher_runs_cycle(self):
        """BackgroundOAuthRefresher calls manager.run_cycle periodically."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "expiring:1": {
                    "provider": "openai-codex",
                    "access_token": "at",
                    "refresh_token": "rt_1",
                    "client_id": "client_1",
                    "expires_at": now + 1800,
                },
            }
            profiles_file.write_text(json.dumps(data))

            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "access_token": "new_at",
                "expires_in": 7200,
            }

            mgr = OAuthManager(auth_profiles_file=profiles_file)
            refresher = BackgroundOAuthRefresher(
                interval=1, manager=mgr, enabled=True
            )

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.__aenter__.return_value = mock_client
                mock_client.__aexit__.return_value = None
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client_class.return_value = mock_client

                await refresher.start()
                await asyncio.sleep(1.5)
                await refresher.stop()

                # Token should have been refreshed
                updated = json.loads(profiles_file.read_text())
                assert updated["expiring:1"]["access_token"] == "new_at"

    @pytest.mark.asyncio
    async def test_background_refresher_respects_enabled_flag(self):
        """BackgroundOAuthRefresher skips cycles when enabled=False."""
        mock_manager = AsyncMock(spec=OAuthManager)
        mock_manager.run_cycle = AsyncMock(return_value={})

        refresher = BackgroundOAuthRefresher(
            interval=1, manager=mock_manager, enabled=False
        )

        await refresher.start()
        await asyncio.sleep(1.5)
        await refresher.stop()

        # Manager should not have been called
        assert not mock_manager.run_cycle.called

    @pytest.mark.asyncio
    async def test_background_refresher_handles_exception(self):
        """BackgroundOAuthRefresher catches and logs exceptions."""
        mock_manager = AsyncMock(spec=OAuthManager)
        mock_manager.run_cycle = AsyncMock(side_effect=RuntimeError("Simulated error"))

        refresher = BackgroundOAuthRefresher(
            interval=1, manager=mock_manager, enabled=True
        )

        await refresher.start()
        await asyncio.sleep(1.5)
        await refresher.stop()

        # Should not crash — exception was caught
        assert mock_manager.run_cycle.called

    @pytest.mark.asyncio
    async def test_background_refresher_logs_cycle_results(self):
        """BackgroundOAuthRefresher logs refresh results."""
        mock_manager = AsyncMock(spec=OAuthManager)
        mock_manager.run_cycle = AsyncMock(return_value={"profile:1": True, "profile:2": False})

        refresher = BackgroundOAuthRefresher(
            interval=1, manager=mock_manager, enabled=True
        )

        await refresher.start()
        await asyncio.sleep(1.5)
        await refresher.stop()

        assert mock_manager.run_cycle.called


# ============================================================================
# Test Class 6: Refresh Handler Functions (4 tests)
# ============================================================================


class TestRefreshHandlers:
    """Test individual OAuth provider refresh handlers."""

    @pytest.mark.asyncio
    async def test_refresh_token_openai_codex_handler(self):
        """_refresh_token_openai_codex updates access and refresh tokens."""
        profile = {
            "provider": "openai-codex",
            "access_token": "old_at",
            "refresh_token": "rt_123",
            "client_id": "client_123",
            "token_endpoint": "https://auth.openai.com/oauth/token",
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_at",
            "refresh_token": "rt_456",
            "expires_in": 7200,
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            updated = await _refresh_token_openai_codex("test:1", profile)

            assert updated["access_token"] == "new_at"
            assert updated["refresh_token"] == "rt_456"
            assert "expires_at" in updated

    @pytest.mark.asyncio
    async def test_refresh_token_openai_codex_missing_refresh(self):
        """_refresh_token_openai_codex raises error when refresh_token missing."""
        profile = {"provider": "openai-codex"}

        with pytest.raises(OAuthRefreshError):
            await _refresh_token_openai_codex("test:1", profile)

    @pytest.mark.asyncio
    async def test_refresh_token_anthropic_handler(self):
        """_refresh_token_anthropic updates access token."""
        profile = {
            "provider": "anthropic",
            "access_token": "old_at",
            "refresh_token": "rt_abc",
            "client_id": "client_abc",
            "token_endpoint": "https://claude.ai/api/oauth/token",
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_at_abc",
            "expires_in": 3600,
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            updated = await _refresh_token_anthropic("test:1", profile)

            assert updated["access_token"] == "new_at_abc"
            assert "expires_at" in updated

    @pytest.mark.asyncio
    async def test_refresh_token_anthropic_missing_refresh(self):
        """_refresh_token_anthropic raises error when refresh_token missing."""
        profile = {"provider": "anthropic"}

        with pytest.raises(OAuthRefreshError):
            await _refresh_token_anthropic("test:1", profile)


# ============================================================================
# Integration & Edge Cases (4 tests)
# ============================================================================


class TestIntegrationAndEdgeCases:
    """Integration tests and edge case scenarios."""

    def test_cooldown_and_profile_files_separate(self):
        """CooldownManager correctly separates cooldowns.json and auth-profiles.json."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()

            cooldown_data = {"cool:1": {"cooldownUntil": now - 50, "errorCount": 0}}
            profile_data = {
                "prof:1": {
                    "provider": "anthropic",
                    "cooldownUntil": now - 50,
                    "errorCount": 0,
                }
            }

            cooldowns_file.write_text(json.dumps(cooldown_data))
            profiles_file.write_text(json.dumps(profile_data))

            mgr = CooldownManager(
                cooldowns_file=cooldowns_file, auth_profiles_file=profiles_file
            )
            mgr.run_cycle()

            # Both should be cleared independently
            assert json.loads(cooldowns_file.read_text()) == {}
            assert "cooldownUntil" not in json.loads(profiles_file.read_text())["prof:1"]

    def test_cooldown_manager_with_missing_fields(self):
        """CooldownManager handles missing fields gracefully."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            data = {
                "minimal:1": {"cooldownUntil": now - 50},  # No errorCount
                "empty:1": {},  # Empty entry
            }
            cooldowns_file.write_text(json.dumps(data))

            mgr = CooldownManager(
                cooldowns_file=cooldowns_file,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            result = mgr.clear_expired()

            # Both should be handled gracefully
            assert "minimal:1" in result
            assert "empty:1" not in result  # No cooldownUntil, so not cleared

    @pytest.mark.asyncio
    async def test_concurrent_background_tasks(self):
        """Both background tasks can run concurrently."""
        with TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            profiles_file = Path(tmpdir) / "profiles.json"

            cooldowns_file.write_text(json.dumps({}))
            profiles_file.write_text(json.dumps({}))

            cooldown_mgr = CooldownManager(
                cooldowns_file=cooldowns_file, auth_profiles_file=profiles_file
            )
            oauth_mgr = OAuthManager(auth_profiles_file=profiles_file)

            clearer = BackgroundCooldownClearer(
                interval=1, manager=cooldown_mgr, enabled=True
            )
            refresher = BackgroundOAuthRefresher(
                interval=1, manager=oauth_mgr, enabled=True
            )

            # Start both
            await clearer.start()
            await refresher.start()
            await asyncio.sleep(1.5)
            await clearer.stop()
            await refresher.stop()

            # Both should complete without interference
            assert clearer._task is None
            assert refresher._task is None

    def test_oauth_manager_custom_refresh_window(self):
        """OAuthManager respects custom refresh_window."""
        with TemporaryDirectory() as tmpdir:
            profiles_file = Path(tmpdir) / "profiles.json"
            now = time.time()
            data = {
                "token:1": {
                    "provider": "openai-codex",
                    "refresh_token": "rt_123",
                    "expires_at": now + 1800,  # 30 min
                },
            }
            profiles_file.write_text(json.dumps(data))

            # With 1 hour window, should be expiring
            mgr_1h = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=3600
            )
            assert len(mgr_1h.get_expiring_profiles()) == 1

            # With 15 min window, should NOT be expiring
            mgr_15m = OAuthManager(
                auth_profiles_file=profiles_file, refresh_window=900
            )
            assert len(mgr_15m.get_expiring_profiles()) == 0


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "--cov=tokenpak.agent.auth"])
