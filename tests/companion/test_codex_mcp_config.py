# SPDX-License-Identifier: Apache-2.0
"""Tests for Codex MCP registration command construction."""

from __future__ import annotations

import subprocess
import sys

from tokenpak.companion.codex import mcp_config


class _FakeCompleted:
    returncode = 0
    stdout = ""
    stderr = ""


def _capture_register_cmd(monkeypatch, env_vars=None):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(mcp_config, "_is_registered", lambda codex_home=None: False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mcp_config._register(env_vars) is True
    return captured["cmd"]


def test_register_spawn_uses_safe_path_mode_on_py311_plus(monkeypatch) -> None:
    """The registered server spawn must carry -P so a tokenpak/ directory in
    the user's cwd cannot shadow the installed package."""
    if sys.version_info < (3, 11):
        monkeypatch.setattr(sys, "version_info", (3, 11, 0, "final", 0))
    cmd = _capture_register_cmd(monkeypatch)
    exe_idx = cmd.index(sys.executable)
    assert cmd[exe_idx + 1] == "-P"
    assert cmd[exe_idx + 2 : exe_idx + 4] == ["-m", "tokenpak.companion.mcp.server"]


def test_register_spawn_omits_safe_path_flag_on_py310(monkeypatch) -> None:
    """-P does not exist on Python 3.10; the flag must be version-gated."""
    monkeypatch.setattr(sys, "version_info", (3, 10, 13, "final", 0))
    cmd = _capture_register_cmd(monkeypatch)
    assert "-P" not in cmd
    exe_idx = cmd.index(sys.executable)
    assert cmd[exe_idx + 1 : exe_idx + 3] == ["-m", "tokenpak.companion.mcp.server"]


def test_register_env_vars_precede_separator(monkeypatch) -> None:
    """--env pairs must stay before the -- separator regardless of the
    safe-path flag."""
    cmd = _capture_register_cmd(
        monkeypatch, {"TOKENPAK_COMPANION_PROFILE": "lean"}
    )
    sep = cmd.index("--")
    env_idx = cmd.index("--env")
    assert env_idx < sep
    assert cmd[env_idx + 1] == "TOKENPAK_COMPANION_PROFILE=lean"
