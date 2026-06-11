# SPDX-License-Identifier: Apache-2.0
"""Fleet-mode injection tests for both launchers.

Fleet mode is the launcher-scoped runtime bypass: TokenPak-owned state
(~/.config/tokenpak/permissions.toml) makes `tokenpak claude` /
`tokenpak codex` inject the client's bypass flag into argv at exec time,
with a mandatory stderr banner. Client config files are never involved.

Covers:
  - Claude launcher: flag injection, no duplication (bare-mode coherence),
    mandatory banner text, state reader (absent / on / corrupt)
  - Codex launcher: fleet param injection, coexistence with the env-var
    back-compat alias, banner text, vanilla default
  - End-to-end: launcher main() passes the flag to the exec'd argv
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tokenpak.companion import launcher as claude_launcher
from tokenpak.companion.codex import launcher as codex_launcher

CLAUDE_BYPASS = "--dangerously-skip-permissions"
CODEX_BYPASS = "--dangerously-bypass-approvals-and-sandbox"

CLAUDE_BANNER = f"tokenpak: fleet mode — bypass flags injected ({CLAUDE_BYPASS})"
CODEX_BANNER = f"tokenpak: fleet mode — bypass flags injected ({CODEX_BYPASS})"


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _enable_fleet_state(home: Path) -> None:
    p = home / ".config" / "tokenpak" / "permissions.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[launcher]\nfleet_mode = true\n")


# ---------------------------------------------------------------------------
# Claude launcher — _apply_fleet_mode + _fleet_mode_enabled
# ---------------------------------------------------------------------------


def test_claude_fleet_off_no_injection_no_banner():
    stream = io.StringIO()
    out = claude_launcher._apply_fleet_mode(["claude", "--foo"], False, stream=stream)
    assert out == ["claude", "--foo"]
    assert stream.getvalue() == ""


def test_claude_fleet_on_injects_flag_and_banner():
    stream = io.StringIO()
    out = claude_launcher._apply_fleet_mode(["claude"], True, stream=stream)
    assert out.count(CLAUDE_BYPASS) == 1
    assert CLAUDE_BANNER in stream.getvalue()


def test_claude_fleet_no_duplicate_when_flag_present():
    """Bare mode (or the user) may already carry the flag — never duplicate.

    The banner still prints: it is the canonical guardrail for any
    fleet-enabled launch.
    """
    stream = io.StringIO()
    out = claude_launcher._apply_fleet_mode(
        ["claude", CLAUDE_BYPASS], True, stream=stream
    )
    assert out.count(CLAUDE_BYPASS) == 1
    assert CLAUDE_BANNER in stream.getvalue()


def test_claude_fleet_does_not_mutate_input():
    original = ["claude", "--foo"]
    claude_launcher._apply_fleet_mode(original, True, stream=io.StringIO())
    assert original == ["claude", "--foo"]


def test_claude_fleet_state_reader_absent(tmp_home):
    assert claude_launcher._fleet_mode_enabled() is False


def test_claude_fleet_state_reader_enabled(tmp_home):
    _enable_fleet_state(tmp_home)
    assert claude_launcher._fleet_mode_enabled() is True


def test_claude_fleet_state_reader_corrupt_never_raises(tmp_home):
    p = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[[[ broken")
    assert claude_launcher._fleet_mode_enabled() is False


def test_claude_main_passes_flag_to_exec(tmp_home, monkeypatch, capsys):
    """End-to-end: with fleet state on, the exec'd claude argv carries the
    bypass flag and the banner reaches stderr."""
    _enable_fleet_state(tmp_home)
    # Point proxy detection at a dead port so the launcher never finds one
    monkeypatch.setenv("TOKENPAK_PROXY_URL", "http://127.0.0.1:1")

    captured: dict = {}

    class _Stop(Exception):
        pass

    def fake_execvpe(prog, args, env):
        captured["prog"] = prog
        captured["args"] = args
        raise _Stop()

    monkeypatch.setattr(claude_launcher.os, "execvpe", fake_execvpe)
    with pytest.raises(_Stop):
        claude_launcher.main([])
    assert captured["prog"] == "claude"
    assert CLAUDE_BYPASS in captured["args"]
    assert CLAUDE_BANNER in capsys.readouterr().err


def test_claude_main_vanilla_without_fleet(tmp_home, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_PROXY_URL", "http://127.0.0.1:1")
    captured: dict = {}

    class _Stop(Exception):
        pass

    def fake_execvpe(prog, args, env):
        captured["args"] = args
        raise _Stop()

    monkeypatch.setattr(claude_launcher.os, "execvpe", fake_execvpe)
    with pytest.raises(_Stop):
        claude_launcher.main([])
    assert CLAUDE_BYPASS not in captured["args"]
    assert "fleet mode" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Codex launcher — fleet param + env-var back-compat alias
# ---------------------------------------------------------------------------


def test_codex_fleet_param_injects_flag():
    out = codex_launcher._maybe_inject_bypass_flag(["-x"], env={}, fleet=True)
    assert out.count(CODEX_BYPASS) == 1
    assert "-x" in out


def test_codex_default_stays_vanilla():
    out = codex_launcher._maybe_inject_bypass_flag(["-x"], env={}, fleet=False)
    assert out == ["-x"]


def test_codex_fleet_no_duplicate_with_user_flag():
    out = codex_launcher._maybe_inject_bypass_flag(
        [CODEX_BYPASS, "--foo"], env={}, fleet=True
    )
    assert out.count(CODEX_BYPASS) == 1


def test_codex_env_alias_still_works_with_fleet_off():
    """The env var remains the back-compat alias of fleet mode."""
    out = codex_launcher._maybe_inject_bypass_flag(
        [], env={"TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX": "1"}, fleet=False
    )
    assert out == [CODEX_BYPASS]


def test_codex_fleet_and_env_alias_together_inject_once():
    out = codex_launcher._maybe_inject_bypass_flag(
        ["--foo"],
        env={"TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX": "yes"},
        fleet=True,
    )
    assert out.count(CODEX_BYPASS) == 1


def test_codex_banner_on_fleet():
    assert codex_launcher._fleet_banner(env={}, fleet=True) == CODEX_BANNER


def test_codex_banner_on_env_alias():
    banner = codex_launcher._fleet_banner(
        env={"TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX": "true"}, fleet=False
    )
    assert banner == CODEX_BANNER


def test_codex_banner_off_by_default():
    assert codex_launcher._fleet_banner(env={}, fleet=False) is None


def test_codex_fleet_state_reader(tmp_home):
    assert codex_launcher._fleet_state_enabled() is False
    _enable_fleet_state(tmp_home)
    assert codex_launcher._fleet_state_enabled() is True
