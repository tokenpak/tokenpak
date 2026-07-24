# SPDX-License-Identifier: Apache-2.0
"""Tests for `tokenpak permissions` — show / set / reset for both clients.

Invariants under test (binding for the permission-tier feature):
  - Tier mapping writes exactly the managed keys per client and nothing else
    (Claude: permissions.defaultMode; Codex: top-level approval_policy +
    sandbox_mode).
  - `set fleet` writes ONLY TokenPak-owned launcher state; client config
    files are provably untouched (mtime + content).
  - The launcher state file is a launcher knob (`fleet_mode = true|false`)
    and never contains a `tier = "fleet"` key.
  - The persistent-tier display never reads "fleet" — values are restricted
    to strict / standard / auto / custom.
  - `reset` is scoped: only managed keys are removed; allow/deny/ask arrays,
    env blocks, profiles, comments and all unrelated keys survive. No
    full-file restore from .bak.
  - Bypass flags are only ever referenced by the two launcher files
    (flag-isolation invariant).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

from tokenpak.cli.commands import permissions as perms

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_DISPLAY_STRINGS = (
    "Claude Code tier: fleet",
    "Codex tier: fleet",
    "persistent tier: fleet",
)

CLAUDE_BYPASS = "--dangerously-skip-permissions"
CODEX_BYPASS = "--dangerously-bypass-approvals-and-sandbox"


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Redirect Path.home() so every config file is isolated to tmp_path."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _write_claude_settings(home: Path, data: dict) -> Path:
    p = home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    return p


def _write_codex_config(home: Path, text: str) -> Path:
    p = home / ".codex" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


SAMPLE_CLAUDE = {
    "permissions": {
        "allow": ["mcp__example__*"],
        "deny": ["WebFetch"],
        "ask": ["Bash(rm:*)"],
    },
    "env": {"EXAMPLE": "1"},
    "mcpServers": {"example": {"command": "example-server"}},
}

SAMPLE_CODEX = (
    "# user banner comment\n"
    'model = "gpt-5"\n'
    "\n"
    "[profiles.work]\n"
    'model = "o4"\n'
    "\n"
    "[mcp_servers.example]\n"
    'command = "example-server"\n'
)


# ---------------------------------------------------------------------------
# set — tier mapping per client (each tier × each client)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier,mode",
    [
        ("strict", "default"),
        ("standard", "acceptEdits"),
        ("auto", "bypassPermissions"),
    ],
)
def test_set_tier_claude_mapping(tmp_home, tier, mode):
    _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    result = perms.apply_claude_tier(tier)
    assert result.ok, result.error
    data = json.loads((tmp_home / ".claude" / "settings.json").read_text())
    assert data["permissions"]["defaultMode"] == mode
    # Untouched keys
    assert data["permissions"]["allow"] == ["mcp__example__*"]
    assert data["permissions"]["deny"] == ["WebFetch"]
    assert data["permissions"]["ask"] == ["Bash(rm:*)"]
    assert data["env"] == {"EXAMPLE": "1"}
    assert data["mcpServers"] == {"example": {"command": "example-server"}}
    # Backup created before write
    assert (tmp_home / ".claude" / "settings.json.bak").exists()


@pytest.mark.parametrize(
    "tier,approval,sandbox",
    [
        ("strict", "on-request", "read-only"),
        ("standard", "on-request", "workspace-write"),
        ("auto", "never", "workspace-write"),
    ],
)
def test_set_tier_codex_mapping(tmp_home, tier, approval, sandbox):
    _write_codex_config(tmp_home, SAMPLE_CODEX)
    result = perms.apply_codex_tier(tier)
    assert result.ok, result.error
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    text = (tmp_home / ".codex" / "config.toml").read_text()
    cfg = tomllib.loads(text)
    assert cfg["approval_policy"] == approval
    assert cfg["sandbox_mode"] == sandbox
    # Untouched: unrelated keys, profiles, MCP blocks, comments
    assert cfg["model"] == "gpt-5"
    assert cfg["profiles"]["work"]["model"] == "o4"
    assert cfg["mcp_servers"]["example"]["command"] == "example-server"
    assert "# user banner comment" in text
    assert (tmp_home / ".codex" / "config.toml.bak").exists()


def test_set_via_cli_handler_both_clients(tmp_home):
    _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    _write_codex_config(tmp_home, SAMPLE_CODEX)
    rc = perms.run_permissions(_ns(permissions_cmd="set", tier="auto", client="both"))
    assert rc == 0
    data = json.loads((tmp_home / ".claude" / "settings.json").read_text())
    assert data["permissions"]["defaultMode"] == "bypassPermissions"
    cfg = perms._read_codex_config()
    assert cfg["approval_policy"] == "never"


def test_set_single_client_leaves_other_alone(tmp_home):
    _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    codex_p = _write_codex_config(tmp_home, SAMPLE_CODEX)
    before = codex_p.read_text()
    rc = perms.run_permissions(_ns(permissions_cmd="set", tier="strict", client="claude-code"))
    assert rc == 0
    assert codex_p.read_text() == before


# ---------------------------------------------------------------------------
# set fleet — launcher state only, never client config
# ---------------------------------------------------------------------------


def test_set_fleet_does_not_modify_client_configs(tmp_home):
    claude_p = _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    codex_p = _write_codex_config(tmp_home, SAMPLE_CODEX)
    claude_before = (claude_p.read_text(), os.stat(claude_p).st_mtime_ns)
    codex_before = (codex_p.read_text(), os.stat(codex_p).st_mtime_ns)

    rc = perms.run_permissions(_ns(permissions_cmd="set", tier="fleet", client="both", yes=True))
    assert rc == 0
    assert perms.fleet_mode_enabled() is True
    # mtime + content provably unchanged
    assert os.stat(claude_p).st_mtime_ns == claude_before[1]
    assert os.stat(codex_p).st_mtime_ns == codex_before[1]
    assert claude_p.read_text() == claude_before[0]
    assert codex_p.read_text() == codex_before[0]
    # No bypass values leaked anywhere near client config
    assert "bypassPermissions" not in claude_p.read_text()
    assert "never" not in codex_p.read_text()
    assert "danger-full-access" not in codex_p.read_text()


def test_set_fleet_requires_explicit_optin_non_tty(tmp_home, capsys):
    rc = perms.run_permissions(_ns(permissions_cmd="set", tier="fleet", client="both", yes=False))
    assert rc == 1
    assert perms.fleet_mode_enabled() is False
    assert "--yes" in capsys.readouterr().err


def test_legacy_fleet_rejects_narrow_client_scope(tmp_home, capsys):
    rc = perms.run_permissions(_ns(permissions_cmd="set", tier="fleet", client="codex", yes=True))
    assert rc == 2
    assert perms._get_launcher_mode("codex") == "inherit"
    assert perms._get_launcher_mode("claude-code") == "inherit"
    err = capsys.readouterr().err
    assert "would broaden scope unexpectedly" in err
    assert "launcher full-bypass --client codex" in err


def test_state_file_is_launcher_knob_never_tier_fleet(tmp_home):
    perms.set_fleet_mode(True, "test")
    perms._record_last_set_tier("claude-code", "standard")
    text = (tmp_home / ".config" / "tokenpak" / "permissions.toml").read_text()
    assert 'tier = "fleet"' not in text
    assert "fleet_mode = true" in text
    assert '"claude-code" = "full-bypass"' in text
    assert '"codex" = "full-bypass"' in text
    # Recording a non-persistent tier must be refused silently
    perms._record_last_set_tier("codex", "fleet")
    text = (tmp_home / ".config" / "tokenpak" / "permissions.toml").read_text()
    assert "fleet" not in text.replace("fleet_mode", "")


def test_state_file_user_owned_and_0644(tmp_home):
    perms.set_fleet_mode(True, "test")
    p = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    assert p.exists()
    assert str(p).startswith(str(tmp_home))  # user-owned dir, never a system path
    assert (p.stat().st_mode & 0o777) == 0o644


def test_fleet_mode_enabled_never_raises_on_corrupt_state(tmp_home):
    p = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not [ valid toml ===")
    assert perms.fleet_mode_enabled() is False


@pytest.mark.parametrize("raw", ['"false"', '"true"', "0", "1"])
def test_non_boolean_legacy_fleet_value_fails_closed(tmp_home, raw):
    state = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(f"[launcher]\nfleet_mode = {raw}\n")
    for client in ("claude-code", "codex"):
        mode, warning = perms._get_launcher_mode_status(client)
        assert mode == "inherit"
        assert warning is not None
        assert "must be true or false" in warning
    assert perms.fleet_mode_enabled() is False


@pytest.mark.parametrize("raw", ["999", '"2"', "true"])
def test_unknown_or_noninteger_schema_version_fails_closed(tmp_home, raw):
    state = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        f"schema_version = {raw}\n\n"
        "[launcher]\n"
        "fleet_mode = false\n\n"
        "[launcher.modes]\n"
        'codex = "full-bypass"\n'
    )
    mode, warning = perms._get_launcher_mode_status("codex")
    assert mode == "inherit"
    assert warning is not None
    assert "unsupported launcher state schema_version" in warning


@pytest.mark.parametrize(
    "mode",
    ["inherit", "approval-bypass", "sandbox-bypass", "full-bypass"],
)
def test_launcher_mode_round_trip_for_codex(tmp_home, mode):
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode=mode,
            client="codex",
            yes=True,
        )
    )
    assert rc == 0
    assert perms._get_launcher_mode("codex") == mode
    assert perms._get_launcher_mode("claude-code") == "inherit"
    text = (tmp_home / ".config" / "tokenpak" / "permissions.toml").read_text()
    assert "schema_version = 2" in text
    assert "# WARNING: non-inherit values affect only" in text
    assert f'"codex" = "{mode}"' in text


def test_launcher_mode_round_trip_for_claude(tmp_home):
    mode = "full-bypass"
    perms._set_launcher_modes({"claude-code": mode}, "test")
    assert perms._get_launcher_mode("claude-code") == mode
    assert perms._get_launcher_mode("codex") == "inherit"


@pytest.mark.parametrize("mode", ["approval-bypass", "sandbox-bypass"])
@pytest.mark.parametrize("client", ["claude-code", "both"])
def test_partial_bypass_rejected_for_claude_atomically(tmp_home, capsys, mode, client):
    perms._set_launcher_modes({"codex": "full-bypass"}, "existing state")
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode=mode,
            client=client,
            yes=True,
        )
    )
    assert rc == 2
    assert perms._get_launcher_mode("codex") == "full-bypass"
    assert perms._get_launcher_mode("claude-code") == "inherit"
    captured = capsys.readouterr()
    assert "only inherit/full-bypass" in captured.err
    assert "--client codex" in captured.err


def test_launcher_bypass_requires_yes_non_tty(tmp_home, capsys):
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode="approval-bypass",
            client="codex",
            yes=False,
        )
    )
    assert rc == 1
    assert perms._get_launcher_mode("codex") == "inherit"
    captured = capsys.readouterr()
    assert "configured sandbox still applies" in captured.err
    assert "--yes" in captured.err
    assert "administrator policy" in captured.err


def test_tokenpak_noninteractive_disables_tty_prompt(tmp_home, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_NONINTERACTIVE", "1")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode="approval-bypass",
            client="codex",
            yes=False,
        )
    )
    assert rc == 1
    assert perms._get_launcher_mode("codex") == "inherit"
    assert "without --yes" in capsys.readouterr().err


def test_launcher_tty_prompt_defaults_to_no(tmp_home, monkeypatch, capsys):
    monkeypatch.setattr(perms, "_interactive_confirmation_allowed", lambda: True)
    monkeypatch.setattr(sys.stdin, "readline", lambda: "\n")
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode="full-bypass",
            client="codex",
            yes=False,
        )
    )
    assert rc == 0
    assert perms._get_launcher_mode("codex") == "inherit"
    captured = capsys.readouterr()
    assert "[y/N]" in captured.out
    assert "Cancelled" in captured.out


def test_launcher_yes_keeps_warning_visible(tmp_home, capsys):
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode="full-bypass",
            client="codex",
            yes=True,
        )
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "CRITICAL" in captured.err
    assert "approval prompts or" in captured.err
    assert "launcher inherit --client codex" in captured.err


@pytest.mark.parametrize(
    "config,mode,needle",
    [
        (
            'approval_policy = "never"\nsandbox_mode = "workspace-write"\n',
            "sandbox-bypass",
            "approval_policy=never plus sandbox-bypass",
        ),
        (
            'approval_policy = "on-request"\nsandbox_mode = "danger-full-access"\n',
            "approval-bypass",
            "sandbox_mode=danger-full-access plus approval-bypass",
        ),
    ],
)
def test_launcher_warning_elevates_effective_full_bypass(tmp_home, capsys, config, mode, needle):
    _write_codex_config(tmp_home, config)
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode=mode,
            client="codex",
            yes=True,
        )
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "CRITICAL EFFECTIVE CONFIG" in err
    assert needle in err


def test_legacy_fleet_state_migrates_without_broadening(tmp_home):
    state = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("[launcher]\nfleet_mode = true\n")
    assert perms._get_launcher_mode("claude-code") == "full-bypass"
    assert perms._get_launcher_mode("codex") == "full-bypass"

    perms._set_launcher_modes({"codex": "approval-bypass"}, "test migration")
    assert perms._get_launcher_mode("claude-code") == "full-bypass"
    assert perms._get_launcher_mode("codex") == "approval-bypass"
    text = state.read_text()
    assert "fleet_mode = false" in text


def test_unknown_launcher_mode_fails_closed_with_warning(tmp_home):
    state = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        "schema_version = 2\n\n"
        "[launcher]\n"
        "fleet_mode = false\n\n"
        "[launcher.modes]\n"
        'codex = "future-unrestricted"\n'
        '"claude-code" = "inherit"\n'
    )
    mode, warning = perms._get_launcher_mode_status("codex")
    assert mode == "inherit"
    assert warning is not None
    assert "invalid stored mode" in warning


@pytest.mark.parametrize("mode", ["approval-bypass", "sandbox-bypass"])
def test_unsupported_stored_claude_partial_mode_fails_closed(tmp_home, mode):
    state = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        "schema_version = 2\n\n"
        "[launcher]\n"
        "fleet_mode = false\n\n"
        "[launcher.modes]\n"
        f'"claude-code" = "{mode}"\n'
        'codex = "inherit"\n'
    )
    resolved, warning = perms._get_launcher_mode_status("claude-code")
    assert resolved == "inherit"
    assert warning is not None
    assert "unsupported stored mode" in warning


def test_show_invalid_launcher_state_uses_launcher_remediation(tmp_home, capsys):
    state = tmp_home / ".config" / "tokenpak" / "permissions.toml"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        '[launcher]\nfleet_mode = false\n\n[launcher.modes]\ncodex = "future-unrestricted"\n'
    )
    assert perms.run_permissions(_ns(permissions_cmd="show")) == 0
    out = capsys.readouterr().out
    assert "Invalid launcher state was ignored safely" in out
    assert "launcher inherit --client both" in out
    assert "client config was modified outside TokenPak" not in out


def test_launcher_reset_is_client_scoped_and_preserves_tiers(tmp_home):
    _write_codex_config(tmp_home, SAMPLE_CODEX)
    assert perms.apply_codex_tier("auto").ok
    perms._set_launcher_modes(
        {"codex": "full-bypass", "claude-code": "full-bypass"},
        "test",
    )
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode="inherit",
            client="codex",
        )
    )
    assert rc == 0
    assert perms._get_launcher_mode("codex") == "inherit"
    assert perms._get_launcher_mode("claude-code") == "full-bypass"
    assert perms.read_codex_tier()[0] == "auto"


def test_launcher_modes_never_modify_client_configs(tmp_home):
    claude_p = _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    codex_p = _write_codex_config(tmp_home, SAMPLE_CODEX)
    before = {
        claude_p: (claude_p.read_bytes(), claude_p.stat().st_mtime_ns),
        codex_p: (codex_p.read_bytes(), codex_p.stat().st_mtime_ns),
    }
    perms._set_launcher_modes(
        {"claude-code": "full-bypass", "codex": "sandbox-bypass"},
        "test",
    )
    for path, (content, mtime) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == mtime


# ---------------------------------------------------------------------------
# reset — scoped, never a full-file restore
# ---------------------------------------------------------------------------


def test_reset_claude_scoped_preserves_user_arrays(tmp_home):
    _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    assert perms.apply_claude_tier("auto").ok
    # User edits between apply and reset must survive a scoped reset
    p = tmp_home / ".claude" / "settings.json"
    data = json.loads(p.read_text())
    data["permissions"]["allow"].append("Bash(git status:*)")
    data["newTopLevelKey"] = {"added": "after-apply"}
    p.write_text(json.dumps(data))

    perms.set_fleet_mode(True, "test")
    rc = perms.run_permissions(_ns(permissions_cmd="reset", client="claude-code"))
    assert rc == 0

    after = json.loads(p.read_text())
    assert "defaultMode" not in after["permissions"]
    assert after["permissions"]["allow"] == ["mcp__example__*", "Bash(git status:*)"]
    assert after["permissions"]["deny"] == ["WebFetch"]
    assert after["newTopLevelKey"] == {"added": "after-apply"}
    # Reset clears launcher fleet state too
    assert perms.fleet_mode_enabled() is False


def test_reset_codex_scoped_preserves_profiles_and_comments(tmp_home):
    _write_codex_config(tmp_home, SAMPLE_CODEX)
    assert perms.apply_codex_tier("auto").ok
    rc = perms.run_permissions(_ns(permissions_cmd="reset", client="codex"))
    assert rc == 0
    text = (tmp_home / ".codex" / "config.toml").read_text()
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    cfg = tomllib.loads(text)
    assert "approval_policy" not in cfg
    assert "sandbox_mode" not in cfg
    assert cfg["model"] == "gpt-5"
    assert cfg["profiles"]["work"]["model"] == "o4"
    assert cfg["mcp_servers"]["example"]["command"] == "example-server"
    assert "# user banner comment" in text


def test_reset_when_nothing_managed_is_noop_success(tmp_home):
    _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    rc = perms.run_permissions(_ns(permissions_cmd="reset", client="both"))
    assert rc == 0
    data = json.loads((tmp_home / ".claude" / "settings.json").read_text())
    assert data["permissions"]["allow"] == ["mcp__example__*"]


def test_backup_restore_roundtrip(tmp_home):
    p = _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    original = p.read_text()
    assert perms.apply_claude_tier("auto").ok
    bak = tmp_home / ".claude" / "settings.json.bak"
    assert bak.exists()
    assert json.loads(bak.read_text()) == json.loads(original)
    # Full restore path (user-driven) round-trips
    p.write_text(bak.read_text())
    assert json.loads(p.read_text()) == json.loads(original)


# ---------------------------------------------------------------------------
# show / display invariants
# ---------------------------------------------------------------------------


def test_show_default_rows_exact_shape(tmp_home, capsys):
    rc = perms.run_permissions(_ns(permissions_cmd="show"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Claude Code persistent tier:  standard" in out
    assert "Codex persistent tier:        standard" in out
    assert "Claude Code launcher default: inherit" in out
    assert "Codex launcher default:       inherit" in out
    assert "Legacy full-bypass alias:     disabled" in out


def test_show_json_is_one_schema_versioned_object(tmp_home, capsys):
    rc = perms.run_permissions(_ns(permissions_cmd="show", as_json=True, quiet=False))
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == "tokenpak.permissions.v1"
    assert payload["persistent_tiers"]["codex"]["tier"] == "standard"
    assert payload["launcher_defaults"]["codex"]["mode"] == "inherit"
    assert payload["legacy_fleet_alias"]["enabled"] is False
    assert captured.err == ""


def test_cli_main_first_run_json_has_no_welcome_noise(tmp_home, monkeypatch, capsys):
    from tokenpak import _cli_core

    monkeypatch.setattr(
        _cli_core,
        "_FIRST_RUN_FLAG",
        tmp_home / ".tokenpak" / ".seen_intro",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["tokenpak", "permissions", "show", "--json"],
    )
    _cli_core.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == "tokenpak.permissions.v1"
    assert "Welcome to TokenPak" not in captured.out


def test_show_quiet_suppresses_normal_output(tmp_home, capsys):
    rc = perms.run_permissions(_ns(permissions_cmd="show", as_json=False, quiet=True))
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_show_quiet_preserves_active_safety_warnings(tmp_home, capsys):
    perms._set_launcher_modes({"codex": "approval-bypass"}, "test")
    rc = perms.run_permissions(_ns(permissions_cmd="show", as_json=False, quiet=True))
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "approval prompts will be disabled" in captured.err
    assert "administrator policy" in captured.err


def test_launcher_json_write_is_parseable_and_keeps_stderr_warning(tmp_home, capsys):
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode="approval-bypass",
            client="codex",
            yes=True,
            as_json=True,
            quiet=False,
        )
    )
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == "tokenpak.permissions.v1"
    assert payload["ok"] is True
    assert payload["mode"] == "approval-bypass"
    assert payload["clients"] == ["codex"]
    assert "tokenpak WARNING" in captured.err


def test_launcher_json_without_yes_returns_structured_refusal(tmp_home, capsys):
    rc = perms.run_permissions(
        _ns(
            permissions_cmd="launcher",
            launcher_mode="full-bypass",
            client="codex",
            yes=False,
            as_json=True,
            quiet=False,
        )
    )
    assert rc == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["error"] == "explicit --yes required"
    assert "CRITICAL" in captured.err
    assert perms._get_launcher_mode("codex") == "inherit"


@pytest.mark.parametrize("tier", ["strict", "standard", "auto"])
@pytest.mark.parametrize("fleet", [False, True])
def test_show_never_displays_fleet_as_persistent_tier(tmp_home, capsys, tier, fleet):
    _write_claude_settings(tmp_home, {})
    _write_codex_config(tmp_home, "")
    assert perms.apply_claude_tier(tier).ok
    assert perms.apply_codex_tier(tier).ok
    if fleet:
        perms.set_fleet_mode(True, "test")
    rc = perms.run_permissions(_ns(permissions_cmd="show"))
    assert rc == 0
    out = capsys.readouterr().out
    for forbidden in FORBIDDEN_DISPLAY_STRINGS:
        assert forbidden not in out
    assert f"Claude Code persistent tier:  {tier}" in out
    assert f"Codex persistent tier:        {tier}" in out
    expected_fleet = "enabled (both full-bypass)" if fleet else "disabled"
    assert f"Legacy full-bypass alias:     {expected_fleet}" in out


def test_doctor_rows_exact_shape_and_values(tmp_home):
    rows, drift = perms.doctor_rows()
    assert rows == [
        "Claude Code persistent tier:  standard",
        "Codex persistent tier:        standard",
        "Claude Code launcher default: inherit",
        "Codex launcher default:       inherit",
        "Legacy full-bypass alias:     disabled",
    ]
    assert drift is False


def test_doctor_rows_drift_reports_custom(tmp_home):
    _write_claude_settings(tmp_home, {})
    assert perms.apply_claude_tier("standard").ok
    # External hand-edit away from any known tier mapping
    p = tmp_home / ".claude" / "settings.json"
    data = json.loads(p.read_text())
    data["permissions"]["defaultMode"] = "somethingElse"
    p.write_text(json.dumps(data))
    rows, drift = perms.doctor_rows()
    assert drift is True
    assert rows[0].startswith("Claude Code persistent tier:  custom")
    assert "modified externally" in rows[0]
    assert "fleet" not in rows[0]


def test_persistent_tier_values_restricted(tmp_home):
    """Persistent-tier labels are restricted to strict/standard/auto/custom."""
    allowed = {"strict", "standard", "auto", "custom"}
    assert perms.read_claude_tier()[0] in allowed
    assert perms.read_codex_tier()[0] in allowed
    perms.set_fleet_mode(True, "test")
    assert perms.read_claude_tier()[0] in allowed
    assert perms.read_codex_tier()[0] in allowed


# ---------------------------------------------------------------------------
# Flag isolation — bypass flags live ONLY in the two launcher files
# ---------------------------------------------------------------------------


def test_bypass_flags_only_in_launcher_files():
    allowed = {
        REPO_ROOT / "tokenpak" / "companion" / "launcher.py",
        REPO_ROOT / "tokenpak" / "companion" / "codex" / "launcher.py",
    }
    offenders: list[str] = []
    for path in (REPO_ROOT / "tokenpak").rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if CLAUDE_BYPASS in text or CODEX_BYPASS in text:
            if path not in allowed:
                offenders.append(str(path))
    assert not offenders, (
        f"bypass flags must only be referenced by the two launcher files; found in: {offenders}"
    )


# ---------------------------------------------------------------------------
# Menu surface — Permission tier section registered with live rows
# ---------------------------------------------------------------------------


def test_menu_has_permission_tier_section(tmp_home):
    from tokenpak.cli.commands import menu

    assert any(key == "permissions" for key, _ in menu._HOME_ITEMS)
    assert "permissions" in menu._SUBCOMMAND_COMMANDS
    assert callable(menu._section_permissions)
    assert callable(menu._section_launcher_permissions)
    subtitle = menu._permission_tier_subtitle()
    # Both persistent rows + the launcher row surface in the section
    assert "Claude Code persistent tier: standard" in subtitle
    assert "Codex persistent tier: standard" in subtitle
    assert "Claude Code launcher default: inherit" in subtitle
    assert "Codex launcher default: inherit" in subtitle
    assert "Legacy full-bypass alias: disabled" in subtitle


def test_cli_parser_exposes_launcher_mode_matrix():
    from tokenpak._cli_core import build_parser

    args = build_parser().parse_args(
        [
            "permissions",
            "launcher",
            "sandbox-bypass",
            "--client",
            "codex",
            "--yes",
            "--json",
        ]
    )
    assert args.permissions_cmd == "launcher"
    assert args.launcher_mode == "sandbox-bypass"
    assert args.client == "codex"
    assert args.yes is True
    assert args.as_json is True

    show = build_parser().parse_args(["permissions", "show", "--json"])
    assert show.permissions_cmd == "show"
    assert show.as_json is True


def test_config_writers_never_emit_bypass_flags(tmp_home):
    """No config-writer path may put a launcher bypass flag on disk."""
    _write_claude_settings(tmp_home, SAMPLE_CLAUDE)
    _write_codex_config(tmp_home, SAMPLE_CODEX)
    for tier in ("strict", "standard", "auto"):
        perms.apply_claude_tier(tier)
        perms.apply_codex_tier(tier)
    perms.set_fleet_mode(True, "test")
    for p in (
        tmp_home / ".claude" / "settings.json",
        tmp_home / ".codex" / "config.toml",
        tmp_home / ".config" / "tokenpak" / "permissions.toml",
    ):
        text = p.read_text()
        assert CLAUDE_BYPASS not in text
        assert CODEX_BYPASS not in text
