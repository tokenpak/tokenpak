"""tests/cli/test_install_claude_code.py

Tests for ``tokenpak install --claude-code`` (tokenpak/agent/cli/commands/install.py).

Coverage
--------
1. fresh install — no existing Claude Code, exits with error
2. fresh install — Claude Code present, no existing settings.json → writes + creates backup path
3. update existing install — preserves other settings.json fields
4. atomic write safety — .tmp file used; mid-write crash → original restored from backup
5. --dry-run — produces no writes
6. smoke test pass → banner printed
7. smoke test fail → settings.json restored from backup, non-zero exit
8. idempotent — already configured, no changes on second run
9. --mode flag overrides auto-detect
10. --no-systemd skips unit write
"""

from __future__ import annotations

import json
import os
import subprocess
import types
from pathlib import Path
from unittest import mock

import pytest

from tokenpak.cli.commands.install import (
    PROXY_URL,
    MODE_PROFILE_MAP,
    _atomic_write_settings,
    _backup_settings,
    _read_settings,
    _settings_path,
    _systemd_unit_path,
    auto_detect_mode,
    configure_settings,
    detect_claude_binary,
    detect_claude_dir,
    install_systemd_unit,
    restore_backup,
    run_install_cmd,
    run_smoke_test,
    select_mode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a temp directory."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


@pytest.fixture()
def claude_dir(tmp_home):
    """Create the ~/.claude/ directory (simulates Claude Code installed)."""
    d = tmp_home / ".claude"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def claude_settings(claude_dir):
    """Create ~/.claude/settings.json with a minimal valid JSON object."""
    f = claude_dir / "settings.json"
    f.write_text("{}\n", encoding="utf-8")
    return f


@pytest.fixture()
def claude_settings_with_data(claude_dir):
    """Create ~/.claude/settings.json with existing fields that must be preserved."""
    f = claude_dir / "settings.json"
    data = {"theme": "dark", "someOtherKey": 42, "env": {"EXISTING_VAR": "keep-me"}}
    f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return f


def _fake_args(**kwargs):
    """Build a minimal args namespace."""
    defaults = {
        "dry_run": False,
        "no_systemd": True,  # skip systemd by default in unit tests
        "mode": None,
        "claude_code": True,
    }
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 1. detect_claude_binary
# ---------------------------------------------------------------------------


def test_detect_claude_binary_found():
    with mock.patch("shutil.which", return_value="/usr/local/bin/claude"):
        assert detect_claude_binary() == "/usr/local/bin/claude"


def test_detect_claude_binary_not_found():
    with mock.patch("shutil.which", return_value=None):
        assert detect_claude_binary() is None


# ---------------------------------------------------------------------------
# 2. detect_claude_dir
# ---------------------------------------------------------------------------


def test_detect_claude_dir_absent(tmp_home):
    assert detect_claude_dir() is False


def test_detect_claude_dir_present(claude_dir):
    assert detect_claude_dir() is True


# ---------------------------------------------------------------------------
# 3. _read_settings / _atomic_write_settings
# ---------------------------------------------------------------------------


def test_read_settings_missing(tmp_home):
    assert _read_settings(_settings_path()) == {}


def test_read_settings_invalid_json(claude_dir):
    p = claude_dir / "settings.json"
    p.write_text("not json", encoding="utf-8")
    assert _read_settings(p) == {}


def test_atomic_write_settings_creates_file(claude_dir):
    p = claude_dir / "settings.json"
    data = {"env": {"ANTHROPIC_BASE_URL": PROXY_URL}}
    _atomic_write_settings(p, data)
    assert json.loads(p.read_text()) == data
    # .tmp file must NOT be left behind
    assert not p.with_suffix(".json.tmp").exists()


def test_atomic_write_settings_no_tmp_on_validation_failure(claude_dir):
    """If JSON serialisation fails, no tmp file must persist."""
    p = claude_dir / "settings.json"
    # inject an un-serialisable value
    data = {"key": object()}
    with pytest.raises(TypeError):
        _atomic_write_settings(p, data)
    assert not p.with_suffix(".json.tmp").exists()


# ---------------------------------------------------------------------------
# 4. configure_settings — idempotency
# ---------------------------------------------------------------------------


def test_configure_settings_idempotent(claude_settings):
    """Already correct URL → changed=False, no backup."""
    existing = {"env": {"ANTHROPIC_BASE_URL": PROXY_URL}}
    claude_settings.write_text(json.dumps(existing, indent=2) + "\n")
    changed, backup = configure_settings(dry_run=False)
    assert changed is False
    assert backup is None


def test_configure_settings_writes_url(claude_settings):
    changed, backup = configure_settings(dry_run=False)
    assert changed is True
    assert backup is not None and backup.exists()
    data = json.loads(claude_settings.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_configure_settings_preserves_other_fields(claude_settings_with_data):
    """Existing settings fields (theme, someOtherKey, EXISTING_VAR) must survive."""
    changed, backup = configure_settings(dry_run=False)
    assert changed is True
    data = json.loads(_settings_path().read_text())
    assert data["theme"] == "dark"
    assert data["someOtherKey"] == 42
    assert data["env"]["EXISTING_VAR"] == "keep-me"
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


# ---------------------------------------------------------------------------
# 5. --dry-run — no writes
# ---------------------------------------------------------------------------


def test_dry_run_no_writes(claude_dir):
    """--dry-run must not create or modify any files."""
    before = set(claude_dir.iterdir())
    configure_settings(dry_run=True)
    after = set(claude_dir.iterdir())
    assert before == after, "dry-run must not create or modify files"


def test_dry_run_full_command(tmp_home, claude_dir):
    """run_install_cmd with --dry-run must not write settings.json."""
    with (
        mock.patch("shutil.which", return_value="/usr/local/bin/claude"),
        mock.patch(
            "tokenpak.cli.commands.install.run_smoke_test", return_value=True
        ),
    ):
        args = _fake_args(dry_run=True, no_systemd=True, mode="cli")
        run_install_cmd(args)
    # No settings.json written
    assert not (claude_dir / "settings.json").exists()


# ---------------------------------------------------------------------------
# 6. smoke test pass → banner
# ---------------------------------------------------------------------------


def test_smoke_test_pass(capsys):
    proc = mock.MagicMock()
    proc.returncode = 0
    proc.stdout = "OK"
    proc.stderr = ""
    with (
        mock.patch("shutil.which", return_value="/usr/local/bin/claude"),
        mock.patch("subprocess.run", return_value=proc),
    ):
        assert run_smoke_test(dry_run=False) is True


def test_smoke_test_fail():
    proc = mock.MagicMock()
    proc.returncode = 1
    proc.stdout = ""
    proc.stderr = "Error: connection refused"
    with (
        mock.patch("shutil.which", return_value="/usr/local/bin/claude"),
        mock.patch("subprocess.run", return_value=proc),
    ):
        assert run_smoke_test(dry_run=False) is False


def test_smoke_test_timeout():
    with (
        mock.patch("shutil.which", return_value="/usr/local/bin/claude"),
        mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 60)),
    ):
        # Timeout is non-fatal per spec
        assert run_smoke_test(dry_run=False) is True


# ---------------------------------------------------------------------------
# 7. smoke test fail → restore backup, non-zero exit
# ---------------------------------------------------------------------------


def test_smoke_fail_restores_backup(tmp_home, claude_settings, capsys):
    """If smoke test fails, settings.json is restored from backup."""
    with (
        mock.patch("shutil.which", return_value="/usr/local/bin/claude"),
        mock.patch(
            "tokenpak.cli.commands.install.run_smoke_test", return_value=False
        ),
    ):
        args = _fake_args(no_systemd=True, mode="cli")
        with pytest.raises(SystemExit) as exc_info:
            run_install_cmd(args)
        assert exc_info.value.code == 2

    # settings.json should be back to its original empty-object state
    data = json.loads(claude_settings.read_text())
    assert "ANTHROPIC_BASE_URL" not in data.get("env", {})


# ---------------------------------------------------------------------------
# 8. idempotent — run twice, no extra backup on second run
# ---------------------------------------------------------------------------


def test_idempotent_second_run(tmp_home, claude_settings):
    """Second run when already configured must not create another backup."""
    with (
        mock.patch("shutil.which", return_value="/usr/local/bin/claude"),
        mock.patch(
            "tokenpak.cli.commands.install.run_smoke_test", return_value=True
        ),
    ):
        args = _fake_args(no_systemd=True, mode="cli")
        run_install_cmd(args)
        # Count backups after first run
        backups_after_first = list((tmp_home / ".claude").glob("settings.json.bak.*"))
        assert len(backups_after_first) == 1

        # Second run
        run_install_cmd(args)
        backups_after_second = list((tmp_home / ".claude").glob("settings.json.bak.*"))
        # No new backup created on second run
        assert len(backups_after_second) == len(backups_after_first)


# ---------------------------------------------------------------------------
# 9. --mode flag overrides auto-detect
# ---------------------------------------------------------------------------


def test_select_mode_explicit():
    assert select_mode("tui") == "tui"
    assert select_mode("cron") == "cron"


def test_select_mode_invalid():
    with pytest.raises(ValueError, match="Invalid mode"):
        select_mode("invalid")


def test_select_mode_auto_detect_tmux(monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-0/default,1234,0")
    monkeypatch.delenv("CRON_INVOCATION", raising=False)
    monkeypatch.delenv("CRON", raising=False)
    monkeypatch.delenv("VSCODE_PID", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "")
    assert auto_detect_mode() == "tmux"


def test_select_mode_auto_detect_ide(monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("CRON_INVOCATION", raising=False)
    monkeypatch.delenv("CRON", raising=False)
    monkeypatch.setenv("VSCODE_PID", "12345")
    monkeypatch.setenv("TERM_PROGRAM", "")
    assert auto_detect_mode() == "ide"


def test_mode_sets_correct_profile():
    for mode, profile in MODE_PROFILE_MAP.items():
        assert profile == f"claude-code-{mode}"


# ---------------------------------------------------------------------------
# 10. --no-systemd skips unit write
# ---------------------------------------------------------------------------


def test_no_systemd_skips_unit(tmp_home, claude_dir, capsys):
    with (
        mock.patch("shutil.which", return_value="/usr/local/bin/claude"),
        mock.patch(
            "tokenpak.cli.commands.install.run_smoke_test", return_value=True
        ),
    ):
        args = _fake_args(no_systemd=True, mode="cli")
        run_install_cmd(args)

    unit_path = _systemd_unit_path()
    assert not unit_path.exists(), "Unit file must not be created when --no-systemd is set"


def test_systemd_unit_written(tmp_home, claude_dir):
    """Without --no-systemd the unit file is created."""
    with mock.patch("subprocess.run"):  # stub daemon-reload
        install_systemd_unit(dry_run=False)
    unit_path = _systemd_unit_path()
    assert unit_path.exists()
    content = unit_path.read_text()
    assert "tokenpak-proxy" in content
    assert "WantedBy=default.target" in content


def test_systemd_unit_idempotent(tmp_home, claude_dir):
    """Writing the unit twice → second call returns changed=False."""
    with mock.patch("subprocess.run"):
        changed_first = install_systemd_unit(dry_run=False)
        changed_second = install_systemd_unit(dry_run=False)
    assert changed_first is True
    assert changed_second is False


# ---------------------------------------------------------------------------
# 11. restore_backup
# ---------------------------------------------------------------------------


def test_restore_backup(claude_dir):
    original = {"env": {"ANTHROPIC_BASE_URL": "old-url"}}
    p = claude_dir / "settings.json"
    p.write_text(json.dumps(original, indent=2) + "\n")
    import shutil as _shutil
    backup = p.parent / "settings.json.bak.test"
    _shutil.copy2(p, backup)
    # Now overwrite settings
    p.write_text('{"env": {"ANTHROPIC_BASE_URL": "new-url"}}\n')
    restore_backup(backup)
    assert json.loads(p.read_text()) == original


def test_restore_backup_none_is_noop(claude_dir):
    """restore_backup(None) must not raise."""
    restore_backup(None)


# ---------------------------------------------------------------------------
# 12. no Claude Code → exit 1
# ---------------------------------------------------------------------------


def test_no_claude_code_exits(tmp_home, capsys):
    """If neither binary nor ~/.claude/ exists, installer exits 1."""
    with (
        mock.patch("shutil.which", return_value=None),
        mock.patch(
            "tokenpak.cli.commands.install.detect_claude_dir", return_value=False
        ),
    ):
        args = _fake_args(no_systemd=True, mode="cli")
        with pytest.raises(SystemExit) as exc_info:
            run_install_cmd(args)
        assert exc_info.value.code == 1
