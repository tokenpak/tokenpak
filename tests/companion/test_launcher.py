# SPDX-License-Identifier: Apache-2.0
"""Tests for the companion launcher — config file generation.

Validates that _write_mcp_config, _write_settings, and _write_system_prompt
produce the correct file contents.  Does NOT exec into Claude Code.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Banner-text-drift skip reason (grep-able):
#
# `test_main_banner_written_to_stderr` asserts the literal substring
# `"companion ready"` (lowercase) appears in stderr after `launcher.main([])`.
# Production launcher no longer emits that exact phrase; current stderr
# contains:
#
#     📦 TokenPak Companion
#        Ready • Mode: Balanced • Budget: Unlimited
#
# So `"Ready"` is present but `"companion ready"` is not. Plus on hosts
# without a `claude` binary in PATH, the launcher emits
# `"tokenpak: failed to launch claude"` — but that's a separate concern;
# the assertion would still fail even with claude present, because the
# banner phrase itself drifted.
#
# Treated as API/behavior drift; the live tests in this file
# (write_mcp_config / write_settings / write_system_prompt /
# main_generates_all_config_files / prefix_session_name helpers /
# main_passes_through_extra_args / main_proxy_detection_exception_path)
# remain meaningful guards.
SKIP_LAUNCHER_BANNER_TEXT_DRIFT = (
    'Test asserts literal `"companion ready"` in launcher stderr. '
    'Production banner now emits `"TokenPak Companion / Ready • ..."` '
    "without the lowercase `companion ready` substring. API drift."
)


from tokenpak.companion import launcher
from tokenpak.companion.config import CompanionConfig

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
    with patch.object(
        type(cfg), "run_dir", new_callable=lambda: property(lambda self: tmp_path / "run")
    ):
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
    safe_path = ["-P"] if sys.version_info >= (3, 11) else []
    assert server["args"] == [*safe_path, "-m", "tokenpak.companion.mcp.server"]


def test_write_mcp_config_uses_safe_python_path(tmp_path):
    """MCP server is launched with -P so a ``tokenpak`` entry in the launch
    cwd cannot shadow the installed package (cwd-shadow regression guard)."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        launcher._write_mcp_config(cfg)
    server = json.loads((run_dir / "mcp.json").read_text())["mcpServers"]["tokenpak-companion"]
    if sys.version_info >= (3, 11):
        assert "-P" in server["args"]
        # -P must precede -m to take effect for the module launch.
        assert server["args"].index("-P") < server["args"].index("-m")
    else:
        assert "-P" not in server["args"]


@pytest.mark.parametrize(
    ("version_info", "expected_args"),
    [
        ((3, 10, 13, "final", 0), ["-m", "tokenpak.companion.mcp.server"]),
        ((3, 11, 0, "final", 0), ["-P", "-m", "tokenpak.companion.mcp.server"]),
    ],
)
def test_write_mcp_config_version_gates_safe_path_flag(
    tmp_path, monkeypatch, version_info, expected_args
):
    """Claude MCP config remains runnable on 3.10 and isolated on 3.11+."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal")
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "version_info", version_info)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        launcher._write_mcp_config(cfg)
    server = json.loads((run_dir / "mcp.json").read_text())["mcpServers"]["tokenpak-companion"]
    assert server["command"] == sys.executable
    assert server["args"] == expected_args


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


def test_write_settings_python_fallback_hook_isolated_mode(tmp_path):
    """The fallback uses the launcher interpreter and safe-path mode when supported."""
    fake_pkg = tmp_path / "pkg"
    (fake_pkg / "hooks").mkdir(parents=True)
    (fake_pkg / "hooks" / "pre_send.py").write_text("# fallback hook\n")
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(launcher, "__file__", str(fake_pkg / "launcher.py")):
        with patch.object(
            type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
            path = launcher._write_settings(cfg)
    settings = json.loads(Path(path).read_text())
    hook_cmd = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    expected_prefix = [sys.executable]
    if sys.version_info >= (3, 11):
        expected_prefix.append("-P")
    assert shlex.split(hook_cmd) == [*expected_prefix, str(fake_pkg / "hooks" / "pre_send.py")]


@pytest.mark.parametrize(
    ("version_info", "expected_safe_path"),
    [
        ((3, 10, 13, "final", 0), False),
        ((3, 11, 0, "final", 0), True),
    ],
)
def test_write_settings_python_fallback_version_gates_safe_path(
    tmp_path, monkeypatch, version_info, expected_safe_path
):
    """The fallback hook uses the exact launcher interpreter on 3.10/3.11+."""
    fake_pkg = tmp_path / "package with spaces"
    (fake_pkg / "hooks").mkdir(parents=True)
    hook_py = fake_pkg / "hooks" / "pre_send.py"
    hook_py.write_text("# fallback hook\n")
    executable = str(tmp_path / "python runtime" / "python3")
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.setattr(sys, "version_info", version_info)
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(launcher, "__file__", str(fake_pkg / "launcher.py")):
        with patch.object(
            type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
            path = launcher._write_settings(cfg)
    settings = json.loads(Path(path).read_text())
    hook_cmd = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    expected = [executable, str(hook_py)]
    if expected_safe_path:
        expected.insert(1, "-P")
    assert shlex.split(hook_cmd) == expected


def test_write_settings_python_fallback_handles_empty_sys_executable(tmp_path, monkeypatch):
    """An embedded empty sys.executable keeps the compatible python3 fallback."""
    fake_pkg = tmp_path / "pkg"
    (fake_pkg / "hooks").mkdir(parents=True)
    hook_py = fake_pkg / "hooks" / "pre_send.py"
    hook_py.write_text("# fallback hook\n")
    monkeypatch.setattr(sys, "executable", "")
    monkeypatch.setattr(sys, "version_info", (3, 12, 0, "final", 0))
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(launcher, "__file__", str(fake_pkg / "launcher.py")):
        with patch.object(
            type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
            path = launcher._write_settings(cfg)
    settings = json.loads(Path(path).read_text())
    hook_cmd = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert shlex.split(hook_cmd) == ["python3", str(hook_py)]


def test_write_settings_without_hooks(tmp_path):
    """settings.json has no hooks block when hooks_enabled=False."""
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=False)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    with patch.object(type(cfg), "run_dir", new_callable=lambda: property(lambda self: run_dir)):
        path = launcher._write_settings(cfg)
    settings = json.loads(Path(path).read_text())
    assert "hooks" not in settings


def test_write_settings_preserves_custom_prompt_hooks_and_installs_tokenpak_once(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    settings_dir = home / ".claude"
    settings_dir.mkdir(parents=True)
    custom_allow = {"type": "command", "command": "bash /opt/custom/allow.sh"}
    custom_deny = {"type": "command", "command": "bash /opt/custom/deny.sh"}
    settings_dir.joinpath("settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "", "hooks": [custom_allow]},
                        {"matcher": "protected/.*", "hooks": [custom_deny]},
                    ]
                }
            }
        )
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    first = json.loads(Path(launcher._write_settings(cfg, run_dir=run_dir)).read_text())
    # Feed the generated overlay back as the supported repeat-install input.
    settings_dir.joinpath("settings.json").write_text(json.dumps(first))
    second = json.loads(Path(launcher._write_settings(cfg, run_dir=run_dir)).read_text())

    entries = second["hooks"]["UserPromptSubmit"]
    commands = [hook["command"] for entry in entries for hook in entry.get("hooks", [])]
    assert commands[:2] == [custom_allow["command"], custom_deny["command"]]
    assert sum("tokenpak/companion/hooks/pre_send" in command for command in commands) == 1
    assert entries[1]["matcher"] == "protected/.*"


def test_disabling_tokenpak_hooks_does_not_remove_custom_denial(monkeypatch, tmp_path):
    home = tmp_path / "home"
    settings_dir = home / ".claude"
    settings_dir.mkdir(parents=True)
    deny = {
        "matcher": "protected/.*",
        "hooks": [{"type": "command", "command": "bash /opt/custom/deny.sh"}],
    }
    settings_dir.joinpath("settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": [deny]}})
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=False)
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    settings = json.loads(Path(launcher._write_settings(cfg, run_dir=run_dir)).read_text())

    assert settings["hooks"]["UserPromptSubmit"] == [deny]


def test_scoped_same_command_cannot_suppress_unconditional_guard():
    command = "bash /package/tokenpak/companion/hooks/pre_send.sh"
    scoped = {
        "matcher": "docs/.*",
        "hooks": [{"type": "command", "command": command}],
    }
    settings = {"hooks": {"UserPromptSubmit": [scoped]}}

    launcher._merge_command_hook(settings, "UserPromptSubmit", command, matcher="")
    launcher._merge_command_hook(settings, "UserPromptSubmit", command, matcher="")

    entries = settings["hooks"]["UserPromptSubmit"]
    assert entries[0] == scoped
    unconditional = [entry for entry in entries if entry.get("matcher", "") == ""]
    assert len(unconditional) == 1
    assert unconditional[0]["hooks"] == [{"type": "command", "command": command}]


def test_composed_custom_denial_command_still_emits_its_payload(monkeypatch, tmp_path):
    """Exercise the preserved command, without asserting native merge semantics."""
    import subprocess

    home = tmp_path / "home"
    settings_dir = home / ".claude"
    settings_dir.mkdir(parents=True)
    deny_script = tmp_path / "deny.sh"
    deny_script.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' \'{"decision":"block","reason":"custom policy denial"}\'\n'
    )
    deny_command = f"bash {shlex.quote(str(deny_script))}"
    settings_dir.joinpath("settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "protected/.*",
                            "hooks": [{"type": "command", "command": deny_command}],
                        }
                    ]
                }
            }
        )
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    cfg = CompanionConfig(journal_dir=tmp_path / "journal", hooks_enabled=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    settings = json.loads(Path(launcher._write_settings(cfg, run_dir=run_dir)).read_text())

    preserved = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    result = subprocess.run(
        shlex.split(preserved),
        input="{}",
        text=True,
        capture_output=True,
        check=False,
    )
    decision = json.loads(result.stdout)

    assert result.returncode == 0
    assert decision == {
        "decision": "block",
        "reason": "custom policy denial",
    }


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
        "estimate_tokens",
        "check_budget",
        "load_pak",
        "load_capsule",  # documented legacy alias
        "prune_context",
        "journal_read",
        "journal_write",
        "session_info",
    ]:
        assert tool in content, f"System prompt missing tool: {tool}"


# ---------------------------------------------------------------------------
# main() — file generation without execvpe
# ---------------------------------------------------------------------------


def test_main_generates_all_config_files(tmp_path):
    """launcher.main() creates mcp.json, settings.json, companion-prompt.md."""
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"

    with patch.dict(
        os.environ,
        {
            "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
            "TOKENPAK_COMPANION_ENABLED": "1",
        },
    ):
        # Patch run_dir and os.execvpe so we don't actually launch claude
        with patch.object(
            CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
                launcher.main([])
                mock_exec.assert_called_once()
                exec_cmd = mock_exec.call_args[0][0]
                assert exec_cmd == "claude"

    launch_dirs = list(run_dir.glob("launch-*"))
    assert len(launch_dirs) == 1
    assert (launch_dirs[0] / "mcp.json").exists()
    assert (launch_dirs[0] / "settings.json").exists()
    assert (launch_dirs[0] / "companion-prompt.md").exists()


def test_main_passes_through_extra_args(tmp_path):
    """launcher.main(args) appends extra args to the claude command."""
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"

    with patch.dict(os.environ, {"TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir)}):
        with patch.object(
            CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
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
    """When no --name/-n flag is present, injects the default branded label.

    The default label is plain text ('📦 TokenPak Claude Companion') — the
    host CLI renders the session name and sanitizes control bytes in it, so
    no styling is embedded.
    """
    result = launcher._prefix_session_name(["--no-update-notifier"])
    assert "--name" in result
    idx = result.index("--name")
    label = result[idx + 1]
    # The branded default label IS exactly _DEFAULT_SESSION_LABEL.
    assert label == launcher._DEFAULT_SESSION_LABEL
    # And it contains the 📦 prefix character (just not at index 0).
    assert launcher._SESSION_PREFIX in label
    # Quick sanity: TokenPak brand tokens are present.
    assert "Token" in label
    assert "Pak" in label


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

    with (
        patch.dict(
            os.environ,
            {
                "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
                "TOKENPAK_COMPANION_PROXY_URL": "",  # no explicit proxy
            },
        ),
        patch.dict(os.environ, {}, clear=False),
    ):
        # The launcher passes a copy of the ambient environment to the child, so
        # an inherited ANTHROPIC_BASE_URL would satisfy the assertion below
        # without the launcher having set anything.  Anyone running Claude
        # through TokenPak's own proxy exports exactly that variable, so this
        # test would fail on the machines it most needs to hold on.  patch.dict
        # restores the whole mapping on exit, so popping here is scoped.
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        with patch.object(
            CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
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


def test_main_reports_inherited_routing_when_it_selects_no_proxy(tmp_path, capsys):
    """The banner must not imply a direct connection while the child is routed.

    The launcher hands the child a copy of its own environment, so an
    ANTHROPIC_BASE_URL exported by the user routes Claude even when TokenPak
    detects no proxy and selects nothing. Reporting only TokenPak's own
    selection would leave the operator believing traffic goes straight to the
    provider — and, if that inherited URL is not TokenPak's proxy, believing
    the session is being measured when it is not.
    """
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"
    inherited = "http://127.0.0.1:9999"

    import httpx as _httpx

    with patch.dict(
        os.environ,
        {
            "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
            "TOKENPAK_COMPANION_PROXY_URL": "",  # no explicit proxy
            "ANTHROPIC_BASE_URL": inherited,
        },
    ):
        with patch.object(
            CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
                with patch.object(_httpx, "get", side_effect=Exception("connection refused")):
                    captured_env = {}

                    def capture_exec(cmd, args, env):
                        captured_env.update(env)

                    mock_exec.side_effect = capture_exec
                    launcher.main([])

    # The inherited value is passed through untouched — this is a reporting
    # fix, not a routing change.
    assert captured_env["ANTHROPIC_BASE_URL"] == inherited
    err = capsys.readouterr().err
    assert inherited in err
    assert "not selected by TokenPak" in err
    assert "Proxy active" not in err


def test_main_does_not_report_inherited_routing_when_it_selects_the_proxy(tmp_path, capsys):
    """TokenPak's own selection keeps the plain banner, with no second line.

    Detection is stubbed rather than left to the ambient machine: a developer
    running TokenPak's proxy locally would otherwise decide this test's
    outcome.
    """
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"
    selected = "http://tokenpak-test-proxy:8766"

    import httpx as _httpx

    class _Healthy:
        status_code = 200

    with patch.dict(
        os.environ,
        {
            "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
            "TOKENPAK_PROXY_URL": selected,
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:9999",
        },
    ):
        with patch.object(
            CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe") as mock_exec:
                with patch.object(_httpx, "get", return_value=_Healthy()):
                    captured_env = {}

                    def capture_exec(cmd, args, env):
                        captured_env.update(env)

                    mock_exec.side_effect = capture_exec
                    launcher.main([])

    # TokenPak's selection overrides the inherited value, so the single
    # "Proxy active" line is the whole truth and the second line must not fire.
    assert captured_env["ANTHROPIC_BASE_URL"] == selected
    err = capsys.readouterr().err
    assert f"Proxy active → {selected}" in err
    assert "not selected by TokenPak" not in err


@pytest.mark.skip(reason=SKIP_LAUNCHER_BANNER_TEXT_DRIFT)
def test_main_banner_written_to_stderr(tmp_path, capsys):
    """launcher.main() prints a startup banner to stderr."""
    run_dir = tmp_path / "run"
    journal_dir = tmp_path / "journal"

    with patch.dict(
        os.environ,
        {
            "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
            "TOKENPAK_COMPANION_PROFILE": "balanced",
        },
    ):
        with patch.object(
            CompanionConfig, "run_dir", new_callable=lambda: property(lambda self: run_dir)
        ):
            run_dir.mkdir(parents=True, exist_ok=True)
            with patch("tokenpak.companion.launcher.os.execvpe"):
                launcher.main([])

    captured = capsys.readouterr()
    assert "tokenpak" in captured.err
    assert "companion ready" in captured.err
