# SPDX-License-Identifier: Apache-2.0
"""Tests for the companion launcher — config file generation.

Validates that _write_mcp_config, _write_settings, and _write_system_prompt
produce the correct file contents.  Does NOT exec into Claude Code.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.companion.config import CompanionConfig
from tokenpak.companion import launcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path, **kwargs) -> CompanionConfig:
    """Build a CompanionConfig that writes to tmp_path."""
    cfg = CompanionConfig(
        journal_dir=tmp_path / "journal",
        **kwargs,
    )
    # Override run_dir by setting journal_dir — run_dir is a property derived from home()
    # We patch it directly for tests.
    return cfg


# ---------------------------------------------------------------------------
# _write_mcp_config
# ---------------------------------------------------------------------------

def test_write_mcp_config_creates_file(tmp_path):
    """_write_mcp_config writes mcp.json to config.run_dir."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal")
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: tmp_path / "run")):
        (tmp_path / "run").mkdir(parents=True, exist_ok=True)
        path = launcher._write_mcp_config(cfg)
    assert Path(path).exists()


def test_write_mcp_config_structure(tmp_path):
    """mcp.json has mcpServers.tokenpak-companion with stdio command."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        launcher._write_mcp_config(cfg)
    mcp_data = json.loads((run_dir / "mcp.json").read_text())
    server = mcp_data["mcpServers"]["tokenpak-companion"]
    assert server["type"] == "stdio"
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "tokenpak.companion.mcp.server"]


# ---------------------------------------------------------------------------
# _write_settings
# ---------------------------------------------------------------------------

def test_write_settings_with_hooks_enabled(tmp_path):
    """settings.json includes UserPromptSubmit hook when hooks_enabled=True."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        path = launcher._write_settings(cfg)
    settings = json.loads(Path(path).read_text())
    assert "hooks" in settings
    assert "UserPromptSubmit" in settings["hooks"]
    hook_cmd = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "pre_send" in hook_cmd
    assert "bash" in hook_cmd


def test_write_settings_without_hooks(tmp_path):
    """settings.json has no hooks block when hooks_enabled=False."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=False)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        path = launcher._write_settings(cfg)
    settings = json.loads(Path(path).read_text())
    assert "hooks" not in settings


def test_write_settings_has_mcp_permission(tmp_path):
    """settings.json always includes permission allow for MCP tools."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        path = launcher._write_settings(cfg)
    settings = json.loads(Path(path).read_text())
    allow_list = settings["permissions"]["allow"]
    assert any("tokenpak-companion" in p for p in allow_list)


# ---------------------------------------------------------------------------
# _write_system_prompt
# ---------------------------------------------------------------------------

def test_write_system_prompt_creates_file(tmp_path):
    """_write_system_prompt creates companion-prompt.md."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        path = launcher._write_system_prompt(cfg)
    assert Path(path).exists()
    assert Path(path).name == "companion-prompt.md"


def test_write_system_prompt_mentions_all_tools(tmp_path):
    """System prompt references all 7 MCP tool names."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        path = launcher._write_system_prompt(cfg)
    content = Path(path).read_text()
    for tool in [
        "estimate_tokens", "check_budget", "load_capsule",
        "prune_context", "journal_read", "journal_write", "session_info",
    ]:
        assert tool in content, f"System prompt missing tool: {tool}"


# ---------------------------------------------------------------------------
# main() — file generation without execvpe
# ---------------------------------------------------------------------------

def test_main_generates_all_config_files(tmp_path):
    """launcher.main() creates mcp.json, settings.json, companion-prompt.md."""
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"

    with patch.dict(os.environ, {
        "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
        "TOKENPAK_COMPANION_ENABLED": "1",
    }):
        # Patch run_dir and os.execvpe so we don't actually launch claude
        with patch.object(CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
                launcher.main([])
                mock_exec.assert_called_once()
                exec_cmd = mock_exec.call_args[0][0]
                assert exec_cmd == "claude"

    assert (run_dir / "mcp.json").exists()
    assert (run_dir / "settings.json").exists()
    assert (run_dir / "companion-prompt.md").exists()


def test_main_passes_through_extra_args(tmp_path):
    """launcher.main(args) appends extra args to the claude command."""
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"

    with patch.dict(os.environ, {"TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir)}):
        with patch.object(CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
                launcher.main(["--no-update-notifier", "-p", "test prompt"])
                exec_list = mock_exec.call_args[0][1]
                assert "--no-update-notifier" in exec_list
                assert "-p" in exec_list
                assert "test prompt" in exec_list


# ---------------------------------------------------------------------------
# _prefix_session_name
# ---------------------------------------------------------------------------

def test_prefix_session_name_no_flag():
    """When no --name/-n flag is present, injects a default name."""
    result = launcher._prefix_session_name(["--no-update-notifier"])
    assert "--name" in result
    idx = result.index("--name")
    assert result[idx + 1].startswith(launcher._SESSION_PREFIX)


def test_prefix_session_name_long_flag():
    """--name VALUE gets the session prefix prepended to VALUE."""
    result = launcher._prefix_session_name(["--name", "my-session"])
    idx = result.index("--name")
    assert result[idx + 1] == f"{launcher._SESSION_PREFIX} my-session"


def test_prefix_session_name_short_flag():
    """-n VALUE gets the session prefix prepended to VALUE."""
    result = launcher._prefix_session_name(["-n", "my-session"])
    idx = result.index("-n")
    assert result[idx + 1] == f"{launcher._SESSION_PREFIX} my-session"


def test_prefix_session_name_equals_form():
    """--name=VALUE form gets the session prefix prepended."""
    result = launcher._prefix_session_name(["--name=my-session"])
    assert any(a == f"--name={launcher._SESSION_PREFIX} my-session" for a in result)


def test_prefix_session_name_does_not_mutate_input():
    """Input list is not mutated."""
    original = ["--name", "original"]
    launcher._prefix_session_name(original)
    assert original == ["--name", "original"]


# ---------------------------------------------------------------------------
# main() — proxy detection exception path
# ---------------------------------------------------------------------------

def test_main_proxy_detection_exception_path(tmp_path):
    """When httpx raises, proxy detection falls through without setting ANTHROPIC_BASE_URL."""
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"

    import httpx as _httpx

    with patch.dict(os.environ, {
        "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
        "TOKENPAK_COMPANION_PROXY_URL": "",  # no explicit proxy
    }):
        with patch.object(CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
                with patch.object(_httpx, "get", side_effect=Exception("connection refused")):
                    captured_env = {}

                    def capture_exec(cmd, args, env):
                        captured_env.update(env)

                    mock_exec.side_effect = capture_exec
                    launcher.main([])

    # ANTHROPIC_BASE_URL should NOT be set when httpx raises
    assert "ANTHROPIC_BASE_URL" not in captured_env


def test_main_banner_written_to_stderr(tmp_path, capsys):
    """launcher.main() prints a startup banner to stderr."""
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"

    with patch.dict(os.environ, {
        "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
        "TOKENPAK_COMPANION_PROFILE": "balanced",
    }):
        with patch.object(CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe"):
                launcher.main([])

    captured = capsys.readouterr()
    assert "tokenpak" in captured.err
    assert "companion ready" in captured.err
