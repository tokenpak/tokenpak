# SPDX-License-Identifier: Apache-2.0
"""Launcher permission-default and legacy fleet injection tests.

Per-client TokenPak-owned state makes `tokenpak claude` / `tokenpak codex`
inject the selected session arguments at exec time, with a mandatory stderr
warning. Client config files are never involved. Legacy fleet state still
maps to full-bypass for both clients.

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


def _write_launcher_modes(home: Path, *, claude: str, codex: str) -> None:
    p = home / ".config" / "tokenpak" / "permissions.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "schema_version = 2\n\n"
        "[launcher]\n"
        "fleet_mode = false\n\n"
        "[launcher.modes]\n"
        f'"claude-code" = "{claude}"\n'
        f'codex = "{codex}"\n'
    )


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
    stderr = capsys.readouterr().err
    assert "claude-code launcher mode full-bypass active" in stderr
    assert CLAUDE_BYPASS in stderr
    assert "Managed policy may still constrain" in stderr


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


def test_claude_launcher_unsupported_partial_mode_is_ignored():
    original = ["claude", "--foo"]
    out, flags, skipped = claude_launcher._apply_launcher_mode(
        original,
        "approval-bypass",
    )
    assert out == original
    assert flags == ()
    assert skipped is None
    assert original == ["claude", "--foo"]


def test_claude_launcher_explicit_permission_mode_wins():
    args = ["claude", "--permission-mode", "manual"]
    out, flags, skipped = claude_launcher._apply_launcher_mode(
        args,
        "full-bypass",
    )
    assert out == args
    assert flags == ()
    assert "precedence" in (skipped or "")


def test_claude_launcher_state_is_client_scoped(tmp_home):
    _write_launcher_modes(
        tmp_home,
        claude="inherit",
        codex="full-bypass",
    )
    assert claude_launcher._launcher_mode_state()[0] == "inherit"
    assert codex_launcher._launcher_mode_state()[0] == "full-bypass"


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


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("inherit", []),
        ("approval-bypass", ["--ask-for-approval", "never"]),
        ("sandbox-bypass", ["--sandbox", "danger-full-access"]),
        ("full-bypass", [CODEX_BYPASS]),
    ],
)
def test_codex_launcher_mode_exact_argv(mode, expected):
    original = ["--foo"]
    out, flags, skipped, effective = codex_launcher._apply_launcher_mode(
        original,
        mode,
        env={},
    )
    assert out == [*expected, "--foo"]
    assert list(flags) == expected
    assert skipped is None
    assert effective == mode
    assert original == ["--foo"]
    assert "--full-auto" not in out


@pytest.mark.parametrize(
    "mode,args",
    [
        ("approval-bypass", ["--ask-for-approval", "on-request"]),
        ("approval-bypass", ["-a", "never"]),
        ("sandbox-bypass", ["--sandbox=workspace-write"]),
        ("sandbox-bypass", ["-s", "read-only"]),
        ("full-bypass", ["--sandbox", "workspace-write"]),
        ("approval-bypass", ["-c", "approval_policy=on-request"]),
        (
            "approval-bypass",
            ["-c", "profiles.review.approval_policy=on-request"],
        ),
        ("sandbox-bypass", ["--config=sandbox_mode=workspace-write"]),
        ("full-bypass", ["-c", "default_permissions=:workspace-write"]),
        (
            "approval-bypass",
            ["-c", "approval_policy.granular.sandbox_approval=true"],
        ),
        ("sandbox-bypass", ["--yolo"]),
    ],
)
def test_codex_explicit_permission_args_override_stored_mode(mode, args):
    out, flags, skipped, effective = codex_launcher._apply_launcher_mode(
        args,
        mode,
        env={},
    )
    assert out == args
    assert flags == ()
    assert "precedence" in (skipped or "")
    assert effective == mode


def test_codex_explicit_yolo_satisfies_full_bypass_without_duplication():
    out, flags, skipped, effective = codex_launcher._apply_launcher_mode(
        ["--yolo", "--foo"],
        "full-bypass",
        env={},
    )
    assert out == ["--yolo", "--foo"]
    assert flags == ("--yolo",)
    assert skipped is None
    assert effective == "full-bypass"


def test_codex_env_alias_remains_full_bypass():
    out, flags, skipped, effective = codex_launcher._apply_launcher_mode(
        ["--foo"],
        "inherit",
        env={"TOKENPAK_CODEX_BYPASS_APPROVALS_AND_SANDBOX": "true"},
    )
    assert out == [CODEX_BYPASS, "--foo"]
    assert flags == (CODEX_BYPASS,)
    assert skipped is None
    assert effective == "full-bypass"


def test_codex_launcher_banner_names_risk_flags_and_reset():
    banner = codex_launcher._launcher_mode_banner(
        "sandbox-bypass",
        ("--sandbox", "danger-full-access"),
        None,
    )
    assert banner is not None
    assert "sandbox-bypass" in banner
    assert "--sandbox danger-full-access" in banner
    assert "approval policy still applies" in banner
    assert "launcher inherit --client codex" in banner
