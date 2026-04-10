"""tests/cli/test_setup_wizard.py

Tests for ``tokenpak setup`` wizard (tokenpak/agent/cli/commands/setup.py).

Coverage:
  1. detect_claude_code() — presence/absence of ~/.claude/settings.json
  2. detect_openai() / detect_google() — importlib-based detection
  3. configure_claude_code() — writes URL, creates backup, idempotent
  4. configure_claude_code(yes=True) — skips confirmation prompt
  5. configure_claude_code() on missing file — creates parent dirs, no backup
  6. run_setup_cmd() end-to-end — no clients detected
  7. run_setup_cmd() end-to-end — claude code detected, writes config, idempotent on 2nd run
  8. --yes flag: no credentials written to config
  9. Wizard never writes API keys
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest import mock

import pytest

from tokenpak.cli.commands.setup import (
    PROXY_URL,
    OPENAI_PROXY_URL,
    configure_claude_code,
    detect_claude_code,
    run_setup_cmd,
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
def claude_settings(tmp_home):
    """Pre-create ~/.claude/settings.json with empty JSON object."""
    settings_dir = tmp_home / ".claude"
    settings_dir.mkdir(parents=True)
    settings_file = settings_dir / "settings.json"
    settings_file.write_text("{}\n")
    return settings_file


# ---------------------------------------------------------------------------
# 1. detect_claude_code
# ---------------------------------------------------------------------------


def test_detect_claude_code_absent(tmp_home):
    assert detect_claude_code() is False


def test_detect_claude_code_present(claude_settings):
    assert detect_claude_code() is True


# ---------------------------------------------------------------------------
# 2. detect_openai / detect_google
# ---------------------------------------------------------------------------


def test_detect_openai_when_installed():
    from tokenpak.cli.commands.setup import detect_openai

    with mock.patch("importlib.util.find_spec", return_value=object()):
        assert detect_openai() is True


def test_detect_openai_when_missing():
    from tokenpak.cli.commands.setup import detect_openai

    with mock.patch("importlib.util.find_spec", return_value=None):
        assert detect_openai() is False


def test_detect_google_when_installed():
    from tokenpak.cli.commands.setup import detect_google

    with mock.patch("importlib.util.find_spec", return_value=object()):
        assert detect_google() is True


def test_detect_google_when_missing():
    from tokenpak.cli.commands.setup import detect_google

    with mock.patch("importlib.util.find_spec", return_value=None):
        assert detect_google() is False


# ---------------------------------------------------------------------------
# 3. configure_claude_code — writes URL, creates backup
# ---------------------------------------------------------------------------


def test_configure_writes_proxy_url(claude_settings):
    changed = configure_claude_code(yes=True)
    assert changed is True
    data = json.loads(claude_settings.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_configure_creates_backup(claude_settings):
    configure_claude_code(yes=True)
    backups = list(claude_settings.parent.glob("settings.bak.*"))
    assert len(backups) == 1


def test_configure_preserves_other_keys(claude_settings):
    """Existing keys in settings.json must survive the write."""
    claude_settings.write_text(json.dumps({"someOtherKey": "preserved"}))
    configure_claude_code(yes=True)
    data = json.loads(claude_settings.read_text())
    assert data["someOtherKey"] == "preserved"
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


# ---------------------------------------------------------------------------
# 4. configure_claude_code — idempotent
# ---------------------------------------------------------------------------


def test_configure_idempotent(claude_settings):
    """Running twice: second run should return False (no change)."""
    configure_claude_code(yes=True)
    changed_second = configure_claude_code(yes=True)
    assert changed_second is False


def test_configure_idempotent_no_second_backup(claude_settings):
    """Running twice: only one backup should exist (from the first run)."""
    configure_claude_code(yes=True)
    configure_claude_code(yes=True)
    backups = list(claude_settings.parent.glob("settings.bak.*"))
    assert len(backups) == 1


# ---------------------------------------------------------------------------
# 5. configure_claude_code — missing file creates parent dirs
# ---------------------------------------------------------------------------


def test_configure_creates_missing_settings(tmp_home):
    """If ~/.claude/settings.json doesn't exist, wizard creates it."""
    changed = configure_claude_code(yes=True)
    assert changed is True
    settings_path = tmp_home / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


def test_configure_no_backup_for_missing_file(tmp_home):
    """No backup should be created when the file didn't exist."""
    configure_claude_code(yes=True)
    claude_dir = tmp_home / ".claude"
    backups = list(claude_dir.glob("settings.bak.*"))
    assert len(backups) == 0


# ---------------------------------------------------------------------------
# 6. run_setup_cmd — no clients detected
# ---------------------------------------------------------------------------


def test_run_setup_no_clients(tmp_home, capsys):
    args = types.SimpleNamespace(yes=True)
    with (
        mock.patch(
            "tokenpak.cli.commands.setup.detect_claude_code", return_value=False
        ),
        mock.patch(
            "tokenpak.cli.commands.setup.detect_openai", return_value=False
        ),
        mock.patch(
            "tokenpak.cli.commands.setup.detect_google", return_value=False
        ),
    ):
        run_setup_cmd(args)

    out = capsys.readouterr().out
    assert "No recognized LLM clients detected" in out
    assert "Setup complete" in out


# ---------------------------------------------------------------------------
# 7. run_setup_cmd — claude code detected, end-to-end + idempotent
# ---------------------------------------------------------------------------


def test_run_setup_claude_code_writes_config(tmp_home, capsys):
    args = types.SimpleNamespace(yes=True)
    # Simulate detect_claude_code returning True (file exists)
    claude_dir = tmp_home / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}\n")

    with (
        mock.patch(
            "tokenpak.cli.commands.setup.detect_openai", return_value=False
        ),
        mock.patch(
            "tokenpak.cli.commands.setup.detect_google", return_value=False
        ),
    ):
        run_setup_cmd(args)

    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL
    assert "Setup complete" in capsys.readouterr().out


def test_run_setup_idempotent_second_run(tmp_home, capsys):
    args = types.SimpleNamespace(yes=True)
    claude_dir = tmp_home / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}\n")

    with (
        mock.patch(
            "tokenpak.cli.commands.setup.detect_openai", return_value=False
        ),
        mock.patch(
            "tokenpak.cli.commands.setup.detect_google", return_value=False
        ),
    ):
        run_setup_cmd(args)  # first run
        run_setup_cmd(args)  # second run — must not error

    # Only one backup (from first run)
    backups = list(claude_dir.glob("settings.bak.*"))
    assert len(backups) == 1

    # URL still correct
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["env"]["ANTHROPIC_BASE_URL"] == PROXY_URL


# ---------------------------------------------------------------------------
# 8. Confirmation prompt skipped with --yes
# ---------------------------------------------------------------------------


def test_yes_flag_skips_prompt(claude_settings, monkeypatch):
    """With yes=True, configure_claude_code must NOT call input()."""
    with mock.patch("builtins.input") as mock_input:
        configure_claude_code(yes=True)
    mock_input.assert_not_called()


def test_no_flag_prompts_user(claude_settings):
    """With yes=False, configure_claude_code calls input() for confirmation."""
    with mock.patch("builtins.input", return_value="y") as mock_input:
        configure_claude_code(yes=False)
    mock_input.assert_called_once()


# ---------------------------------------------------------------------------
# 9. Wizard NEVER writes credentials
# ---------------------------------------------------------------------------


def test_no_credentials_written(claude_settings, monkeypatch):
    """settings.json must not contain any API key-like values after setup."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-secret")
    configure_claude_code(yes=True)
    raw = claude_settings.read_text()
    assert "sk-secret-key" not in raw
    assert "sk-openai-secret" not in raw
    # Only the proxy URL should appear
    assert PROXY_URL in raw
