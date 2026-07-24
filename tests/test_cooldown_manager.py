"""Tests for tokenpak._internal.auth.cooldown_manager module."""

import pytest

pytest.importorskip(
    "tokenpak.infrastructure.cooldown", reason="module not available in current build"
)
import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest
from tokenpak.infrastructure.cooldown import (
    BackgroundCooldownClearer,
    CooldownManager,
)


class TestCooldownManagerInit:
    """Test CooldownManager initialization."""

    def test_default_init(self):
        """Test default initialization with standard paths."""
        manager = CooldownManager()
        assert manager.cooldowns_file == Path.home() / ".tokenpak" / "cooldowns.json"
        assert manager.auth_profiles_file == Path.home() / ".tokenpak" / "auth-profiles.json"

    def test_custom_paths(self):
        """Test initialization with custom paths."""
        custom_cooldowns = Path("/tmp/custom_cooldowns.json")
        custom_profiles = Path("/tmp/custom_profiles.json")
        manager = CooldownManager(
            cooldowns_file=custom_cooldowns,
            auth_profiles_file=custom_profiles,
        )
        assert manager.cooldowns_file == custom_cooldowns
        assert manager.auth_profiles_file == custom_profiles


class TestCooldownManagerLoadSave:
    """Test loading and saving cooldown data."""

    def test_load_nonexistent_cooldowns(self):
        """Test loading when cooldowns file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "none.json",
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            data = manager._load_cooldowns()
            assert data == {}

    def test_load_valid_cooldowns(self):
        """Test loading valid cooldowns file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "provider:key1": {"cooldownUntil": 1234567890, "errorCount": 2},
                        "provider:key2": {"cooldownUntil": 1234567891, "errorCount": 0},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            data = manager._load_cooldowns()
            assert len(data) == 2
            assert data["provider:key1"]["errorCount"] == 2

    def test_save_cooldowns(self):
        """Test saving cooldowns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            data = {"test:key": {"cooldownUntil": 1234567890, "errorCount": 1}}
            manager._save_cooldowns(data)
            assert cooldowns_path.exists()
            saved = json.loads(cooldowns_path.read_text())
            assert saved["test:key"]["cooldownUntil"] == 1234567890


class TestCooldownManagerClearExpired:
    """Test clearing expired cooldowns."""

    def test_clear_expired_not_expired(self):
        """Test that non-expired cooldowns are not cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": now + 3600, "errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired()
            assert cleared == []
            # Verify cooldown still exists
            data = manager._load_cooldowns()
            assert "test:key" in data

    def test_clear_expired_is_expired(self):
        """Test that expired cooldowns are cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": now - 3600, "errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired()
            assert "test:key" in cleared
            # Verify cooldown was removed
            data = manager._load_cooldowns()
            assert "test:key" not in data

    def test_clear_expired_high_error_count(self):
        """Test that expired cooldowns with high error count are not cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": now - 100, "errorCount": 15},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired()
            assert "test:key" not in cleared
            # Cooldown should still exist
            data = manager._load_cooldowns()
            assert "test:key" in data

    def test_clear_expired_multiple_entries(self):
        """Test clearing multiple cooldowns at once."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "expired1": {"cooldownUntil": now - 100, "errorCount": 1},
                        "expired2": {"cooldownUntil": now - 100, "errorCount": 2},
                        "active": {"cooldownUntil": now + 3600, "errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired()
            assert len(cleared) == 2
            assert "expired1" in cleared
            assert "expired2" in cleared
            data = manager._load_cooldowns()
            assert "active" in data
            assert "expired1" not in data

    def test_clear_expired_empty_cooldowns(self):
        """Test clearing when no cooldowns exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired()
            assert cleared == []


class TestCooldownManagerProfiles:
    """Test clearing expired cooldowns from auth profiles."""

    def test_clear_profiles_nonexistent(self):
        """Test clearing when profiles file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired_from_profiles()
            assert cleared == []

    def test_clear_profiles_not_expired(self):
        """Test that non-expired profile cooldowns are not cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_path = Path(tmpdir) / "profiles.json"
            now = time.time()
            profiles_path.write_text(
                json.dumps(
                    {
                        "my-profile": {
                            "cooldownUntil": now + 3600,
                            "errorCount": 1,
                        },
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_path,
            )
            cleared = manager.clear_expired_from_profiles()
            assert cleared == []

    def test_clear_profiles_expired(self):
        """Test clearing expired profile cooldowns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_path = Path(tmpdir) / "profiles.json"
            now = time.time()
            profiles_path.write_text(
                json.dumps(
                    {
                        "my-profile": {
                            "cooldownUntil": now - 100,
                            "errorCount": 2,
                        },
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_path,
            )
            cleared = manager.clear_expired_from_profiles()
            assert "my-profile" in cleared

    def test_clear_profiles_high_error_count(self):
        """Test profile cooldowns with high error count are not cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_path = Path(tmpdir) / "profiles.json"
            now = time.time()
            profiles_path.write_text(
                json.dumps(
                    {
                        "problematic": {
                            "cooldownUntil": now - 100,
                            "errorCount": 15,
                        },
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_path,
            )
            cleared = manager.clear_expired_from_profiles()
            assert "problematic" not in cleared

    def test_clear_profiles_removes_fields(self):
        """Test that cleared profiles have cooldownUntil removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_path = Path(tmpdir) / "profiles.json"
            now = time.time()
            profiles_path.write_text(
                json.dumps(
                    {
                        "profile1": {
                            "cooldownUntil": now - 100,
                            "errorCount": 1,
                            "usageStats": {"count": 5},
                        },
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_path,
            )
            manager.clear_expired_from_profiles()
            profiles = manager._load_auth_profiles()
            assert "cooldownUntil" not in profiles["profile1"]
            assert "usageStats" not in profiles["profile1"]


class TestCooldownManagerActiveCooldowns:
    """Test getting active cooldowns."""

    def test_get_active_cooldowns_none(self):
        """Test getting active cooldowns when none exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            active = manager.get_active_cooldowns()
            assert active == {}

    def test_get_active_cooldowns_from_file(self):
        """Test getting active cooldowns from cooldowns.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": now + 3600, "errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            active = manager.get_active_cooldowns()
            assert "test:key" in active
            assert 3500 < active["test:key"] <= 3600  # Allow small variance

    def test_get_active_cooldowns_from_profiles(self):
        """Test getting active cooldowns from profiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profiles_path = Path(tmpdir) / "profiles.json"
            now = time.time()
            profiles_path.write_text(
                json.dumps(
                    {
                        "profile1": {"cooldownUntil": now + 3600, "errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "cooldowns.json",
                auth_profiles_file=profiles_path,
            )
            active = manager.get_active_cooldowns()
            assert "profile:profile1" in active

    def test_get_active_cooldowns_excludes_expired(self):
        """Test that expired cooldowns are not included in active."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "expired": {"cooldownUntil": now - 100, "errorCount": 1},
                        "active": {"cooldownUntil": now + 3600, "errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            active = manager.get_active_cooldowns()
            assert "expired" not in active
            assert "active" in active


class TestCooldownManagerRunCycle:
    """Test run_cycle method."""

    def test_run_cycle_clears_both_sources(self):
        """Test that run_cycle clears from both cooldowns and profiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            profiles_path = Path(tmpdir) / "profiles.json"
            now = time.time()

            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": now - 100, "errorCount": 1},
                    }
                )
            )
            profiles_path.write_text(
                json.dumps(
                    {
                        "profile1": {"cooldownUntil": now - 100, "errorCount": 1},
                    }
                )
            )

            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=profiles_path,
            )
            count = manager.run_cycle()
            assert count == 2

    def test_run_cycle_returns_count(self):
        """Test that run_cycle returns correct count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()

            cooldowns_path.write_text(
                json.dumps(
                    {
                        "k1": {"cooldownUntil": now - 100, "errorCount": 1},
                        "k2": {"cooldownUntil": now - 100, "errorCount": 1},
                        "k3": {"cooldownUntil": now + 3600, "errorCount": 1},
                    }
                )
            )

            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            count = manager.run_cycle()
            assert count == 2


class TestBackgroundCooldownClearerInit:
    """Test BackgroundCooldownClearer initialization."""

    def test_default_init(self):
        """Test default initialization."""
        clearer = BackgroundCooldownClearer()
        assert clearer.interval == 60
        assert clearer.enabled is True
        assert clearer._task is None

    def test_custom_interval(self):
        """Test initialization with custom interval."""
        clearer = BackgroundCooldownClearer(interval=30)
        assert clearer.interval == 30

    def test_custom_manager(self):
        """Test initialization with custom manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_manager = CooldownManager(
                cooldowns_file=Path(tmpdir) / "custom.json",
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            clearer = BackgroundCooldownClearer(manager=custom_manager)
            assert clearer.manager is custom_manager

    def test_disabled_init(self):
        """Test initialization with enabled=False."""
        clearer = BackgroundCooldownClearer(enabled=False)
        assert clearer.enabled is False


@pytest.mark.asyncio
class TestBackgroundCooldownClearerAsync:
    """Test BackgroundCooldownClearer async methods."""

    async def test_start_creates_task(self):
        """Test that start() creates an async task."""
        clearer = BackgroundCooldownClearer(interval=10)
        await clearer.start()
        assert clearer._task is not None
        assert not clearer._task.done()
        await clearer.stop()

    async def test_start_idempotent(self):
        """Test that start() is idempotent."""
        clearer = BackgroundCooldownClearer(interval=10)
        await clearer.start()
        first_task = clearer._task
        await clearer.start()
        second_task = clearer._task
        assert first_task is second_task
        await clearer.stop()

    async def test_stop_cancels_task(self):
        """Test that stop() cancels the task."""
        clearer = BackgroundCooldownClearer(interval=10)
        await clearer.start()
        task = clearer._task
        await clearer.stop()
        assert task.done() or clearer._task is None

    async def test_stop_idempotent(self):
        """Test that stop() is idempotent."""
        clearer = BackgroundCooldownClearer(interval=10)
        await clearer.start()
        await clearer.stop()
        await clearer.stop()  # Should not crash

    async def test_run_cycle_when_enabled(self):
        """Test that run_cycle is called when enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": now - 100, "errorCount": 1},
                    }
                )
            )

            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            clearer = BackgroundCooldownClearer(
                interval=1,
                manager=manager,
                enabled=True,
            )
            await clearer.start()
            # Wait for at least one cycle
            await asyncio.sleep(1.5)
            await clearer.stop()

            # Verify the cooldown was cleared
            data = manager._load_cooldowns()
            assert "test:key" not in data

    async def test_skip_cycle_when_disabled(self):
        """Test that run_cycle is skipped when disabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            now = time.time()
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": now - 100, "errorCount": 1},
                    }
                )
            )

            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            clearer = BackgroundCooldownClearer(
                interval=1,
                manager=manager,
                enabled=False,
            )
            await clearer.start()
            await asyncio.sleep(1.5)
            await clearer.stop()

            # Cooldown should NOT be cleared
            data = manager._load_cooldowns()
            assert "test:key" in data


class TestCooldownManagerEdgeCases:
    """Test edge cases."""

    def test_malformed_json(self):
        """Test handling of malformed JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            cooldowns_path.write_text("{ invalid json")
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            # Should not crash, return empty
            data = manager._load_cooldowns()
            assert data == {}

    def test_missing_cooldown_until(self):
        """Test cooldown entry without cooldownUntil."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired()
            # Should not crash, cooldown preserved
            assert cleared == []
            data = manager._load_cooldowns()
            assert "test:key" in data

    def test_zero_cooldown_until(self):
        """Test cooldown entry with zero cooldownUntil."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_path = Path(tmpdir) / "cooldowns.json"
            cooldowns_path.write_text(
                json.dumps(
                    {
                        "test:key": {"cooldownUntil": 0, "errorCount": 1},
                    }
                )
            )
            manager = CooldownManager(
                cooldowns_file=cooldowns_path,
                auth_profiles_file=Path(tmpdir) / "profiles.json",
            )
            cleared = manager.clear_expired()
            # Zero cooldownUntil is treated as no cooldown
            assert "test:key" not in cleared
