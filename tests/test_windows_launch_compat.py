# SPDX-License-Identifier: Apache-2.0
"""Native Windows launch and session-binding compatibility.

Portable logic runs everywhere. The Windows branches are exercised on any OS by
replacing the module-level ``os`` reference of the module under test with a
shim whose ``name`` is ``"nt"``; ``pathlib`` keeps the real ``os``, so paths
stay native. Behaviour that needs a real Windows process, shell or client is
skipped off Windows and listed in the port report's Windows checklist.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tokenpak
from tokenpak.companion import _python_spawn, launcher, windows_claude
from tokenpak.companion.codex import windows_launcher
from tokenpak.companion.config import CompanionConfig
from tokenpak.companion.hooks import windows_native
from tokenpak.status import binding

REPO_ROOT = Path(tokenpak.__file__).resolve().parents[1]
BYPASS_FLAGS = {
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
    "--full-auto",
    "--yolo",
}


class _WindowsOs:
    """Delegate to ``os`` but report a native Windows platform."""

    name = "nt"

    def __getattr__(self, attr: str):
        return getattr(os, attr)


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "User's home with spaces"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    state = home / ".tpk"
    state.mkdir()
    run = state / "companion" / "run" / "one"
    run.mkdir(parents=True)
    for name in ("TOKENPAK_COMPANION_BUDGET", "TOKENPAK_COMPANION_EXTRA_DIRS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TOKENPAK_HOME", str(state))
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(state / "companion"))
    monkeypatch.setenv(binding.ENV, str(run))
    monkeypatch.setenv("TOKENPAK_COMPANION_ASYNC_FLUSH", "0")
    return home, state, run


# ---------------------------------------------------------------------------
# argv and exit propagation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,forwarded",
    [
        (["--", "--version"], ["--version"]),
        (["--version", "--", "-x"], ["--version", "--", "-x"]),
        (["--", "--", "-p"], ["--", "-p"]),
        ([], []),
    ],
)
def test_tokenpak_claude_strips_only_its_forwarding_separator(monkeypatch, argv, forwarded):
    from tokenpak import _cli_core, companion

    captured = []
    monkeypatch.setattr(_cli_core, "_require_companion_client", lambda name: None)
    monkeypatch.setattr(companion, "launch", lambda args: captured.append(args) or 7)
    with pytest.raises(SystemExit) as stopped:
        _cli_core.cmd_claude(SimpleNamespace(args=argv, budget=None))
    assert stopped.value.code == 7
    assert captured == [forwarded]


def test_tokenpak_claude_success_exit_is_zero(monkeypatch):
    from tokenpak import _cli_core, companion

    monkeypatch.setattr(_cli_core, "_require_companion_client", lambda name: None)
    monkeypatch.setattr(companion, "launch", lambda args: 0)
    with pytest.raises(SystemExit) as stopped:
        _cli_core.cmd_claude(SimpleNamespace(args=["-p", "x"], budget=None))
    assert stopped.value.code == 0


def test_windows_claude_launch_waits_for_child_and_propagates_exit(profile, monkeypatch):
    home, state, _run = profile
    captured = {}
    monkeypatch.setattr(launcher, "os", _WindowsOs())
    monkeypatch.setattr(launcher, "_launcher_mode_state", lambda: ("inherit", None))
    monkeypatch.setattr(windows_claude, "prepare_environment", lambda: None)
    monkeypatch.setattr(launcher, "_proxy_healthy", lambda url, timeout: True)

    def call(args, **kwargs):
        captured.update(args=list(args), env=kwargs.get("env"))
        return 7

    monkeypatch.setattr(launcher.subprocess, "call", call)
    monkeypatch.setattr(
        launcher.os, "execvpe", lambda *a: pytest.fail("exec must not replace the process")
    )
    assert launcher.main(["-p", "synthetic probe"]) == 7
    assert captured["args"][0].endswith("claude.exe")
    probe = captured["args"].index("-p")
    assert captured["args"][probe + 1] == "synthetic probe"
    assert not BYPASS_FLAGS & set(captured["args"])
    directory = Path(captured["env"][binding.ENV])
    assert directory.is_relative_to(state / "companion" / "run")
    mcp = json.loads((directory / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["tokenpak-companion"]["env"][binding.ENV] == str(directory)
    assert "TokenPak companion" in (directory / "companion-prompt.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("flag", ["--help", "-h", "--version", "-v"])
def test_windows_claude_fast_paths_forward_and_propagate_exit(profile, monkeypatch, flag):
    captured = []
    monkeypatch.setattr(launcher, "os", _WindowsOs())
    monkeypatch.setattr(windows_claude, "prepare_environment", lambda: None)
    monkeypatch.setattr(launcher.subprocess, "call", lambda args, **kw: captured.append(args) or 3)
    assert launcher.main([flag]) == 3
    assert captured[0][1:] == [flag]


def test_windows_codex_split_overrides_preserves_arguments():
    config, args = windows_launcher.split_overrides(
        ["exec", "-c", 'model_reasoning_effort="low"', "--config=x=1", "--", "-c", "literal"]
    )
    assert config == ["-c", 'model_reasoning_effort="low"', "--config=x=1"]
    assert args == ["exec", "--", "-c", "literal"]
    with pytest.raises(ValueError):
        windows_launcher.split_overrides(["exec", "-c"])


@pytest.fixture
def codex_profile(profile, monkeypatch, tmp_path):
    _home, state, _run = profile
    binary = tmp_path / "bin dir" / "codex.exe"
    binary.parent.mkdir()
    binary.write_text("not executed")
    monkeypatch.delenv("TOKENPAK_CODEX_BINARY", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(windows_launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(windows_launcher._paths, "home", lambda: state)
    (state / "windows-env.json").write_text(
        json.dumps({"TOKENPAK_CODEX_BINARY": str(binary), "PATH": "ignored"}), encoding="utf-8"
    )
    return state, binary


def test_windows_codex_profile_binary_is_resolved_before_discovery(codex_profile, monkeypatch):
    _state, binary = codex_profile
    calls = []
    monkeypatch.setattr(
        windows_launcher.subprocess, "call", lambda args, **kw: calls.append(args) or 4
    )
    assert windows_launcher.main(["--version"]) == 4
    assert calls == [[str(binary), "--version"]]


def test_windows_codex_launch_routes_without_bypass_and_propagates_exit(codex_profile, monkeypatch):
    state, binary = codex_profile
    calls = []

    class _Health:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *a):
            return json.dumps({"status": "ok", "version": "test"}).encode()

    monkeypatch.setattr(windows_launcher, "urlopen", lambda url, timeout: _Health())

    def call(args, **kwargs):
        calls.append((args, kwargs.get("env")))
        return 5

    monkeypatch.setattr(windows_launcher.subprocess, "call", call)
    assert windows_launcher.main(["exec", "-c", "x=1", "--", "hello"]) == 5
    args, env = calls[0]
    assert args[0] == str(binary)
    assert args[1:3] == ["-c", "x=1"]
    assert args[-2:] == ["--", "hello"]
    assert not BYPASS_FLAGS & set(args)
    assert not any(a.startswith(("--sandbox", "--ask-for-approval")) for a in args)
    directory = Path(env[binding.ENV])
    assert directory.is_relative_to(state / "companion" / "run")
    # Only TOKENPAK_* and the named path variables are taken from the profile.
    assert env.get("PATH") != "ignored"


def test_windows_codex_missing_binary_is_an_observable_failure(profile, monkeypatch, capsys):
    monkeypatch.delenv("TOKENPAK_CODEX_BINARY", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(windows_launcher.shutil, "which", lambda name: None)
    monkeypatch.setattr(windows_launcher._paths, "home", lambda: profile[1])
    assert windows_launcher.main(["--version"]) == 1
    assert "TOKENPAK_CODEX_BINARY" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Interpreter flags (Python 3.10 floor)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version,expect_p", [((3, 10), False), ((3, 12), True)])
def test_native_commands_follow_interpreter_version(monkeypatch, version, expect_p):
    prefix = _python_spawn.python_spawn_prefix(
        executable="C:/Py thon/python.exe", version_info=version
    )
    monkeypatch.setattr(windows_claude, "python_spawn_prefix", lambda: list(prefix))
    hook = windows_claude.native_hook("bind")
    assert hook["command"] == "C:/Py thon/python.exe"
    assert ("-P" in hook["args"]) is expect_p
    assert hook["args"][-3:] == ["-m", "tokenpak.companion.hooks.windows_native", "bind"]
    script = base64.b64decode(windows_claude.status_command().rsplit(" ", 1)[1]).decode("utf-16-le")
    assert ("-P -m" in script) is expect_p
    assert "tokenpak.companion.hooks.windows_native status" in script


# ---------------------------------------------------------------------------
# Native Claude settings overlay
# ---------------------------------------------------------------------------


def _native_settings(settings: dict, *, hooks_enabled: bool = True) -> dict:
    cfg = CompanionConfig(hooks_enabled=hooks_enabled)
    windows_claude.configure_native_settings(
        settings,
        cfg,
        Path("run-dir"),
        status_line=True,
        replaced_commands={"bash /x/pre_send.sh", "bash /x/session_bind.sh"},
    )
    return settings


def test_native_settings_replace_shell_hooks_and_preserve_custom_hooks():
    settings = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "bash /x/pre_send.sh"}]},
                {"hooks": [{"type": "command", "command": "custom-policy"}]},
            ],
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "bash /x/session_bind.sh"}]}
            ],
        }
    }
    data = _native_settings(settings)
    hooks = [h for entries in data["hooks"].values() for e in entries for h in e["hooks"]]
    assert {"type": "command", "command": "custom-policy"} in hooks
    assert not any(h["command"].startswith("bash ") for h in hooks)
    native = [h for h in hooks if "args" in h]
    assert len(native) == 3
    assert "EncodedCommand" in data["statusLine"]["command"]


def test_native_settings_merge_is_idempotent():
    once = _native_settings({"hooks": {}})
    twice = _native_settings(json.loads(json.dumps(once)))
    assert twice == once
    starts = twice["hooks"]["SessionStart"]
    assert sum(1 for e in starts for h in e["hooks"] if h["args"][-1] == "bind") == 1
    assert sum(1 for e in starts for h in e["hooks"] if h["args"][-1] == "title") == 1
    assert len(twice["hooks"]["UserPromptSubmit"]) == 1


def test_native_settings_keep_custom_clear_title_and_respect_disabled_hooks():
    custom = {"matcher": "clear", "hooks": [{"type": "command", "command": "my-title"}]}
    data = _native_settings({"hooks": {"SessionStart": [custom]}})
    titles = [h for e in data["hooks"]["SessionStart"] for h in e["hooks"] if "args" in h]
    assert [h["args"][-1] for h in titles] == ["bind"]
    assert custom in data["hooks"]["SessionStart"]
    disabled = _native_settings({"hooks": {}}, hooks_enabled=False)
    assert disabled["hooks"] == {}


@pytest.mark.skipif(os.name != "nt", reason="NOT-TESTABLE off Windows: native overlay")
def test_windows_settings_overlay_preserves_user_scopes(profile):
    home, state, run = profile
    (home / ".claude").mkdir()
    original = {
        "theme": "dark",
        "permissions": {"defaultMode": "default", "allow": ["Read"]},
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "custom"}]}]},
    }
    (home / ".claude" / "settings.json").write_text(json.dumps(original))
    cfg = CompanionConfig(journal_dir=state / "companion")
    path = launcher._write_settings(cfg, run_dir=run, status_line=True)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["permissions"]["defaultMode"] == "default"
    assert json.loads((home / ".claude" / "settings.json").read_text()) == original
    assert str(home / "vault") not in data["permissions"].get("additionalDirectories", [])


# ---------------------------------------------------------------------------
# Native Claude session binding hooks
# ---------------------------------------------------------------------------


def test_native_bind_is_launch_local_and_title_restores(profile, capsys):
    _, _, run = profile
    for sid in ("claude-one", "claude-two"):
        payload = {"session_id": sid, "hook_event_name": "SessionStart"}
        assert windows_native.handle(payload, "bind") == 0
        assert binding.current_session() == sid
    title = {"hookSpecificOutput": {"hookEventName": "SessionStart", "sessionTitle": "TokenPak"}}
    (run / "session_title.json").write_text(json.dumps(title), encoding="utf-8")
    windows_native.handle({"session_id": "claude-two", "source": "clear"}, "title")
    assert json.loads(capsys.readouterr().out) == title


def test_native_bind_rejects_invalid_identity_and_wrong_event(profile):
    with pytest.raises(ValueError):
        windows_native.handle({"session_id": "../bad", "hook_event_name": "SessionStart"}, "bind")
    with pytest.raises(ValueError):
        windows_native.handle({"session_id": "claude-ok", "hook_event_name": "Stop"}, "bind")
    with pytest.raises(ValueError):
        windows_native.handle({"session_id": "claude-ok"}, "unknown")
    assert not (profile[2] / "current-session").exists()


def test_native_status_renders_only_valid_metrics(profile, capsys):
    payload = {
        "session_id": "claude-status-1",
        "context_window": {"used_percentage": 12.4},
        "cost": {"total_cost_usd": 0.01},
    }
    windows_native.handle(payload, "status")
    assert capsys.readouterr().out.strip() == (
        "TokenPak | claude-s | context 12% | session $0.0100"
    )
    payload["context_window"]["used_percentage"] = 140
    payload["cost"]["total_cost_usd"] = -1
    windows_native.handle(payload, "status")
    assert capsys.readouterr().out.strip() == "TokenPak | claude-s"


@pytest.mark.parametrize("raw", ["[]", "{", "x" * (1024 * 1024 + 2)])
def test_native_hook_main_rejects_bad_input(profile, monkeypatch, raw, capsys):
    monkeypatch.setattr(sys, "argv", ["windows_native", "bind"])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=lambda n: raw[:n]))
    assert windows_native.main() == 1
    assert "native Claude hook failed" in capsys.readouterr().err


def test_generated_native_commands_execute(profile):
    _, state, run = profile
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    for action, event in (("bind", "SessionStart"), ("prompt", "UserPromptSubmit")):
        hook = windows_claude.native_hook(action)
        data = {"session_id": "claude-native-test", "hook_event_name": event, "prompt": "hi"}
        result = subprocess.run(
            [hook["command"], *hook["args"]],
            input=json.dumps(data),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
    assert (run / "current-session").read_text() == "claude-native-test"


@pytest.mark.skipif(os.name != "nt", reason="NOT-TESTABLE off Windows: Git Bash statusLine")
def test_status_command_survives_actual_git_bash(profile):
    git_bash = Path("C:/Program Files/Git/bin/bash.exe")
    if not git_bash.is_file():
        pytest.skip("Git Bash not installed")
    payload = {"session_id": "claude-native-test", "context_window": {"used_percentage": 12}}
    result = subprocess.run(
        [str(git_bash), "-c", windows_claude.status_command()],
        input=json.dumps(payload),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "context 12%" in result.stdout


# ---------------------------------------------------------------------------
# PID liveness and background flush process flags
# ---------------------------------------------------------------------------


def test_liveness_probe_never_terminates_live_process():
    from tokenpak.core.runtime.lifecycle import pid_alive

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        for _ in range(5):
            assert pid_alive(proc.pid)
            assert proc.poll() is None
        proc.terminate()
        proc.wait(timeout=10)
        assert not pid_alive(proc.pid)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_lifecycle_liveness_delegates_to_platform_probe(monkeypatch):
    from tokenpak.core.runtime import lifecycle
    from tokenpak.platform import process

    seen = []
    monkeypatch.setattr(process, "pid_alive", lambda pid: seen.append(pid) or True)
    assert lifecycle.pid_alive(4242) is True
    assert seen == [4242]
    assert lifecycle.pid_alive(0) is False
    assert lifecycle.pid_alive(None) is False
    assert seen == [4242]


@pytest.mark.parametrize("windows", [False, True])
def test_background_flush_process_flags(tmp_path, monkeypatch, windows):
    from tokenpak.companion import _sqlite

    monkeypatch.setenv("TOKENPAK_COMPANION_ASYNC_FLUSH", "1")
    if windows:
        monkeypatch.setattr(_sqlite, "os", _WindowsOs())
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: seen.update(kw))
    assert _sqlite.request_async_pre_send_flush(tmp_path) is True
    assert seen["start_new_session"] is not windows
    assert seen["creationflags"] == (0x08000000 if windows else 0)
