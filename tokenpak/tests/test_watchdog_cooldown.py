"""
Tests for CooldownManager in watchdog.py.

Only tests the pure-logic CooldownManager class — no daemon, no subprocess, no port binding.
Uses tmp_path to avoid writing to real ~/.tokenpak/cooldowns.json.
"""

import json
import time
from pathlib import Path

import pytest

from tokenpak.proxy.proxy_watchdog import CooldownManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_manager(tmp_path: Path) -> CooldownManager:
    """Create a CooldownManager backed by a temp file."""
    return CooldownManager(cooldowns_file=tmp_path / "cooldowns.json")


def write_cooldowns(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2))


def read_cooldowns(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Tests: no cooldown
# ---------------------------------------------------------------------------


def test_clear_expired_empty_file(tmp_path):
    """clear_expired on a missing file returns empty list without error."""
    mgr = make_manager(tmp_path)
    assert mgr.clear_expired() == []


def test_clear_expired_empty_dict(tmp_path):
    """clear_expired on an empty JSON object returns empty list."""
    cf = tmp_path / "cooldowns.json"
    write_cooldowns(cf, {})
    mgr = CooldownManager(cooldowns_file=cf)
    assert mgr.clear_expired() == []


# ---------------------------------------------------------------------------
# Tests: active cooldowns (should NOT be cleared)
# ---------------------------------------------------------------------------


def test_active_cooldown_not_cleared(tmp_path):
    """Cooldown that hasn't expired yet must NOT be removed."""
    cf = tmp_path / "cooldowns.json"
    future = time.time() + 3600
    write_cooldowns(cf, {"anthropic:default": {"cooldownUntil": future, "errorCount": 1}})
    mgr = CooldownManager(cooldowns_file=cf)
    cleared = mgr.clear_expired()
    assert cleared == []
    remaining = read_cooldowns(cf)
    assert "anthropic:default" in remaining


def test_high_error_count_not_cleared_even_if_expired(tmp_path):
    """Expired cooldown with errorCount >= HIGH_ERROR_THRESHOLD must stay."""
    cf = tmp_path / "cooldowns.json"
    past = time.time() - 60
    write_cooldowns(
        cf,
        {"anthropic:default": {"cooldownUntil": past, "errorCount": CooldownManager.HIGH_ERROR_THRESHOLD}},
    )
    mgr = CooldownManager(cooldowns_file=cf)
    cleared = mgr.clear_expired()
    assert cleared == []
    remaining = read_cooldowns(cf)
    assert "anthropic:default" in remaining


# ---------------------------------------------------------------------------
# Tests: expired cooldowns (should BE cleared)
# ---------------------------------------------------------------------------


def test_expired_cooldown_is_cleared(tmp_path):
    """Cooldown with cooldownUntil in the past and low errorCount is removed."""
    cf = tmp_path / "cooldowns.json"
    past = time.time() - 60
    write_cooldowns(cf, {"anthropic:default": {"cooldownUntil": past, "errorCount": 1}})
    mgr = CooldownManager(cooldowns_file=cf)
    cleared = mgr.clear_expired()
    assert "anthropic:default" in cleared
    remaining = read_cooldowns(cf)
    assert "anthropic:default" not in remaining


def test_clear_expired_removes_only_expired(tmp_path):
    """Only expired entries are removed; unexpired entries are preserved."""
    cf = tmp_path / "cooldowns.json"
    past = time.time() - 60
    future = time.time() + 3600
    write_cooldowns(
        cf,
        {
            "anthropic:default": {"cooldownUntil": past, "errorCount": 0},
            "openai:default": {"cooldownUntil": future, "errorCount": 0},
        },
    )
    mgr = CooldownManager(cooldowns_file=cf)
    cleared = mgr.clear_expired()
    assert cleared == ["anthropic:default"]
    remaining = read_cooldowns(cf)
    assert "anthropic:default" not in remaining
    assert "openai:default" in remaining


def test_zero_cooldown_timestamp_is_skipped(tmp_path):
    """Entry with cooldownUntil == 0 is considered inactive and left alone."""
    cf = tmp_path / "cooldowns.json"
    write_cooldowns(cf, {"anthropic:default": {"cooldownUntil": 0, "errorCount": 0}})
    mgr = CooldownManager(cooldowns_file=cf)
    cleared = mgr.clear_expired()
    assert cleared == []
    remaining = read_cooldowns(cf)
    assert "anthropic:default" in remaining


# ---------------------------------------------------------------------------
# Tests: persist + reload round-trip
# ---------------------------------------------------------------------------


def test_persist_and_reload(tmp_path):
    """After clearing expired entries the file is updated correctly on re-read."""
    cf = tmp_path / "cooldowns.json"
    past = time.time() - 10
    future = time.time() + 3600
    write_cooldowns(
        cf,
        {
            "expired-key": {"cooldownUntil": past, "errorCount": 2},
            "live-key": {"cooldownUntil": future, "errorCount": 0},
        },
    )
    mgr = CooldownManager(cooldowns_file=cf)
    mgr.clear_expired()

    # Reload from disk and verify state
    on_disk = read_cooldowns(cf)
    assert "expired-key" not in on_disk
    assert "live-key" in on_disk
    assert on_disk["live-key"]["cooldownUntil"] == pytest.approx(future, abs=1)


def test_multiple_clear_calls_idempotent(tmp_path):
    """Calling clear_expired twice doesn't corrupt state."""
    cf = tmp_path / "cooldowns.json"
    past = time.time() - 10
    write_cooldowns(cf, {"k": {"cooldownUntil": past, "errorCount": 1}})
    mgr = CooldownManager(cooldowns_file=cf)
    first = mgr.clear_expired()
    second = mgr.clear_expired()
    assert "k" in first
    assert second == []


# ---------------------------------------------------------------------------
# Tests: check_auth_profiles
# ---------------------------------------------------------------------------


def test_check_auth_profiles_no_file(tmp_path, monkeypatch):
    """check_auth_profiles returns [] when auth-profiles.json doesn't exist."""
    import tokenpak.proxy.proxy_watchdog as wd_module

    monkeypatch.setattr(wd_module, "AUTH_PROFILES_FILE", tmp_path / "auth-profiles.json")
    mgr = make_manager(tmp_path)
    assert mgr.check_auth_profiles() == []


def test_check_auth_profiles_active_cooldown_warning(tmp_path, monkeypatch):
    """check_auth_profiles warns about active cooldowns but doesn't clear them."""
    import tokenpak.proxy.proxy_watchdog as wd_module

    ap = tmp_path / "auth-profiles.json"
    future = time.time() + 3600
    ap.write_text(json.dumps({"profile1": {"cooldownUntil": future, "errorCount": 1}}))
    monkeypatch.setattr(wd_module, "AUTH_PROFILES_FILE", ap)

    mgr = make_manager(tmp_path)
    warnings = mgr.check_auth_profiles()
    assert len(warnings) == 1
    assert "profile1" in warnings[0]
    assert "cooldown" in warnings[0]


def test_check_auth_profiles_clears_expired(tmp_path, monkeypatch):
    """check_auth_profiles clears expired cooldowns in auth-profiles.json."""
    import tokenpak.proxy.proxy_watchdog as wd_module

    ap = tmp_path / "auth-profiles.json"
    past = time.time() - 60
    ap.write_text(
        json.dumps({"profile1": {"cooldownUntil": past, "errorCount": 1, "someOtherField": True}})
    )
    monkeypatch.setattr(wd_module, "AUTH_PROFILES_FILE", ap)

    mgr = make_manager(tmp_path)
    warnings = mgr.check_auth_profiles()
    assert warnings == []

    # File should be updated — cooldownUntil removed
    updated = json.loads(ap.read_text())
    assert "cooldownUntil" not in updated["profile1"]
    assert updated["profile1"]["someOtherField"] is True
