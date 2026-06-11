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


@pytest.mark.parametrize("tier,mode", [
    ("strict", "default"),
    ("standard", "acceptEdits"),
    ("auto", "bypassPermissions"),
])
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


@pytest.mark.parametrize("tier,approval,sandbox", [
    ("strict", "on-request", "read-only"),
    ("standard", "on-request", "workspace-write"),
    ("auto", "never", "workspace-write"),
])
def test_set_tier_codex_mapping(tmp_home, tier, approval, sandbox):
    _write_codex_config(tmp_home, SAMPLE_CODEX)
    result = perms.apply_codex_tier(tier)
    assert result.ok, result.error
    import tomllib

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
    rc = perms.run_permissions(
        _ns(permissions_cmd="set", tier="strict", client="claude-code")
    )
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

    rc = perms.run_permissions(
        _ns(permissions_cmd="set", tier="fleet", client="both", yes=True)
    )
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
    rc = perms.run_permissions(
        _ns(permissions_cmd="set", tier="fleet", client="both", yes=False)
    )
    assert rc == 1
    assert perms.fleet_mode_enabled() is False
    out = capsys.readouterr().out
    assert "--yes" in out


def test_state_file_is_launcher_knob_never_tier_fleet(tmp_home):
    perms.set_fleet_mode(True, "test")
    perms._record_last_set_tier("claude-code", "standard")
    text = (tmp_home / ".config" / "tokenpak" / "permissions.toml").read_text()
    assert 'tier = "fleet"' not in text
    assert "fleet_mode = true" in text
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
    import tomllib

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
    assert "TokenPak launcher fleet mode: disabled" in out


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
    expected_fleet = "enabled" if fleet else "disabled"
    assert f"TokenPak launcher fleet mode: {expected_fleet}" in out


def test_doctor_rows_exact_shape_and_values(tmp_home):
    rows, drift = perms.doctor_rows()
    assert rows == [
        "Claude Code persistent tier:  standard",
        "Codex persistent tier:        standard",
        "TokenPak launcher fleet mode: disabled",
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
        "bypass flags must only be referenced by the two launcher files; "
        f"found in: {offenders}"
    )


# ---------------------------------------------------------------------------
# Menu surface — Permission tier section registered with live rows
# ---------------------------------------------------------------------------


def test_menu_has_permission_tier_section(tmp_home):
    from tokenpak.cli.commands import menu

    assert any(key == "permissions" for key, _ in menu._HOME_ITEMS)
    assert "permissions" in menu._SUBCOMMAND_COMMANDS
    assert callable(menu._section_permissions)
    subtitle = menu._permission_tier_subtitle()
    # Both persistent rows + the launcher row surface in the section
    assert "Claude Code persistent tier: standard" in subtitle
    assert "Codex persistent tier: standard" in subtitle
    assert "TokenPak launcher fleet mode: disabled" in subtitle


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
