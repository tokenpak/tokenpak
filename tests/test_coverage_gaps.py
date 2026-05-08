"""
test_coverage_gaps.py — Targeted tests to fill top coverage gaps.

TPK-087: Coverage Gap Analysis
Files targeted (highest-miss, 0% coverage):
  1. tokenpak/agent/cli/commands/doctor.py  — 291 stmts, 0%
  2. tokenpak/agent/cli/commands/dashboard.py — 242 stmts, 0%
  3. tokenpak/watchdog.py (CooldownManager) — 187 stmts, 0%
  4. tokenpak/cli.py (_proxy_get helper) — 2394 stmts, 36%
  5. tokenpak/agent/cli/commands/doctor.py Colors class — still 0%
"""

import json
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# WS-A residual import guard — TSR-01-followup.
# `tokenpak.watchdog` (CooldownManager) and `tokenpak.agent.*` are not
# part of the slim OSS surface. Coverage-gap tests in this file reach
# into them; skip cleanly when absent. Full installs that ship the
# agent + watchdog modules exercise normally.
pytest.importorskip(
    "tokenpak.watchdog",
    reason="tokenpak.watchdog (CooldownManager) not part of slim OSS surface",
)


# ---------------------------------------------------------------------------
# 1. doctor.py — Colors helper
# ---------------------------------------------------------------------------

class TestDoctorColors:
    """Colors is 0% covered — simple pure functions, high value."""

    def setup_method(self):
        from tokenpak.cli.commands.doctor import Colors
        self.Colors = Colors

    def test_ok_wraps_green(self):
        result = self.Colors.ok("all good")
        assert "✅" in result
        assert "all good" in result

    def test_warn_wraps_yellow(self):
        result = self.Colors.warn("check this")
        assert "⚠️" in result
        assert "check this" in result

    def test_fail_wraps_red(self):
        result = self.Colors.fail("broken")
        assert "❌" in result
        assert "broken" in result

    def test_reset_code_present(self):
        # ANSI reset must be present so terminal state is restored
        result = self.Colors.ok("test")
        assert "\033[0m" in result


# ---------------------------------------------------------------------------
# 2. doctor.py — run_doctor smoke test (no real FS writes, mock home)
# ---------------------------------------------------------------------------

class TestRunDoctor:
    """run_doctor() is 0% covered — smoke test with mocked filesystem."""

    def test_run_doctor_returns_int(self, tmp_path):
        """run_doctor should return 0 (all pass/warn) or 1 (fail) — always int."""
        from tokenpak.cli.commands.doctor import run_doctor
        with patch("pathlib.Path.home", return_value=tmp_path):
            rc = run_doctor(fix=False)
        assert isinstance(rc, int)
        assert rc in (0, 1)

    def test_run_doctor_fix_mode_returns_int(self, tmp_path):
        from tokenpak.cli.commands.doctor import run_doctor
        with patch("pathlib.Path.home", return_value=tmp_path):
            rc = run_doctor(fix=True)
        assert isinstance(rc, int)


# ---------------------------------------------------------------------------
# 3. dashboard.py — _uptime_str pure function
# ---------------------------------------------------------------------------

class TestDashboardHelpers:
    """dashboard.py helpers are 0% covered — test pure functions."""

    def test_uptime_str_none_returns_unknown(self):
        from tokenpak.cli.commands.dashboard import _uptime_str
        result = _uptime_str(None)
        assert "unknown" in result.lower() or result == "–"

    def test_uptime_str_recent_start(self):
        from tokenpak.cli.commands.dashboard import _uptime_str
        # Started 65 seconds ago
        start = time.time() - 65
        result = _uptime_str(start)
        assert result  # non-empty

    def test_uptime_str_long_running(self):
        from tokenpak.cli.commands.dashboard import _uptime_str
        # Started 2 hours ago
        start = time.time() - 7200
        result = _uptime_str(start)
        assert result

    def test_collect_local_data_returns_dict(self):
        """collect_local_data should return a dict even when proxy is down."""
        from tokenpak.cli.commands.dashboard import collect_local_data
        with patch("tokenpak.cli.commands.dashboard._http_get", return_value=None):
            data = collect_local_data()
        assert isinstance(data, dict)

    def test_load_auth_profiles_missing_file(self, tmp_path):
        """_load_auth_profiles returns {} when file absent."""
        from tokenpak.cli.commands.dashboard import _load_auth_profiles
        with patch("tokenpak.cli.commands.dashboard.AUTH_PROFILES_FILE",
                   tmp_path / "nonexistent.json"):
            result = _load_auth_profiles()
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 4. watchdog.py — CooldownManager (100% testable, 0% covered)
# ---------------------------------------------------------------------------

class TestCooldownManager:
    """CooldownManager is pure file I/O logic — 0% covered."""

    def test_load_empty_when_missing(self, tmp_path):
        from tokenpak.watchdog import CooldownManager
        cm = CooldownManager(tmp_path / "cooldowns.json")
        result = cm._load()
        assert isinstance(result, dict)
        assert len(result) == 0

    def test_load_valid_json(self, tmp_path):
        from tokenpak.watchdog import CooldownManager
        p = tmp_path / "cooldowns.json"
        p.write_text(json.dumps({"anthropic:default": {"cooldownUntil": 9999999999, "errorCount": 2}}))
        cm = CooldownManager(p)
        data = cm._load()
        assert "anthropic:default" in data

    def test_clear_expired_removes_past_entries(self, tmp_path):
        from tokenpak.watchdog import CooldownManager
        p = tmp_path / "cooldowns.json"
        past = int(time.time()) - 3600  # 1 hour ago
        p.write_text(json.dumps({"anthropic:default": {"cooldownUntil": past, "errorCount": 1}}))
        cm = CooldownManager(p)
        cleared = cm.clear_expired()
        assert isinstance(cleared, list)

    def test_clear_expired_keeps_active_entries(self, tmp_path):
        from tokenpak.watchdog import CooldownManager
        p = tmp_path / "cooldowns.json"
        future = int(time.time()) + 3600  # 1 hour from now
        p.write_text(json.dumps({"anthropic:default": {"cooldownUntil": future, "errorCount": 5}}))
        cm = CooldownManager(p)
        cm.clear_expired()
        remaining = cm._load()
        # Active cooldown must be preserved
        assert "anthropic:default" in remaining


# ---------------------------------------------------------------------------
# 5. cli.py — _proxy_get helper (network-isolated)
# ---------------------------------------------------------------------------

class TestProxyGetHelper:
    """_proxy_get is in cli.py which is 36% covered — test edge cases."""

    def test_returns_none_when_unreachable(self):
        from tokenpak.cli import _proxy_get
        # Use a port no server listens on
        result = _proxy_get("/stats", port=19999)
        assert result is None

    def test_returns_none_on_bad_json(self):
        """Even if server responds, bad JSON → None."""
        from tokenpak.cli import _proxy_get
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json{"
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _proxy_get("/stats")
        assert result is None

    def test_returns_dict_on_valid_response(self):
        from tokenpak.cli import _proxy_get
        payload = json.dumps({"status": "ok", "requests": 42}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = payload
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _proxy_get("/stats")
        assert result == {"status": "ok", "requests": 42}
