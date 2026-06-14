# SPDX-License-Identifier: Apache-2.0
"""Tests for `tokenpak integrate <client> --apply --tier ...`.

Covers:
  - --tier writes the per-client mapping additively (backup first,
    unrelated keys untouched)
  - non-interactive --apply with no --tier defaults to the standard tier
  - --tier fleet prints a warning, requires explicit opt-in (--yes when
    non-interactive), sets launcher state, and never persists bypass
    values into client config
  - legacy callers whose namespace has no `tier` attribute keep the exact
    pre-tier behavior (no tier keys written)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tokenpak.cli.commands import permissions as perms
from tokenpak.cli.commands.integrate import run_integrate

PROXY = "http://localhost:8766"


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "proxy_url": PROXY,
        "apply": True,
        "revert": False,
        "client": "claude-code",
        "all": False,
        "tier": None,
        "yes": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _seed_claude(home: Path) -> Path:
    p = home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "permissions": {"allow": ["mcp__example__*"], "deny": ["WebFetch"]},
        "env": {"KEEP": "yes"},
    }))
    return p


def _seed_codex(home: Path) -> Path:
    p = home / ".codex" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# keep this comment\n"
        'model = "gpt-5"\n'
        "\n"
        "[profiles.work]\n"
        'model = "o4"\n'
    )
    return p


# ---------------------------------------------------------------------------
# Claude Code --apply --tier
# ---------------------------------------------------------------------------


def test_apply_tier_standard_claude(tmp_home, capsys):
    p = _seed_claude(tmp_home)
    rc = run_integrate(_args(tier="standard"))
    assert rc == 0
    data = json.loads(p.read_text())
    assert data["permissions"]["defaultMode"] == "acceptEdits"
    # All other keys untouched
    assert data["permissions"]["allow"] == ["mcp__example__*"]
    assert data["permissions"]["deny"] == ["WebFetch"]
    assert data["env"]["KEEP"] == "yes"
    # Backed up first — the .bak holds the pre-apply state (no tier key,
    # no proxy env wiring yet)
    bak = json.loads((tmp_home / ".claude" / "settings.json.bak").read_text())
    assert "defaultMode" not in bak.get("permissions", {})
    assert "ANTHROPIC_BASE_URL" not in bak.get("env", {})
    out = capsys.readouterr().out
    assert "tier: standard" in out


def test_apply_tier_auto_claude(tmp_home):
    p = _seed_claude(tmp_home)
    rc = run_integrate(_args(tier="auto"))
    assert rc == 0
    data = json.loads(p.read_text())
    assert data["permissions"]["defaultMode"] == "bypassPermissions"
    assert data["permissions"]["allow"] == ["mcp__example__*"]


def test_apply_no_tier_non_tty_defaults_standard(tmp_home):
    """Non-interactive --apply with no --tier silently uses standard."""
    p = _seed_claude(tmp_home)
    rc = run_integrate(_args(tier=None))
    assert rc == 0
    data = json.loads(p.read_text())
    assert data["permissions"]["defaultMode"] == "acceptEdits"


def test_legacy_namespace_without_tier_attr_writes_no_tier(tmp_home):
    """Callers predating the tier flag keep the exact pre-tier behavior."""
    p = _seed_claude(tmp_home)
    legacy = argparse.Namespace(
        proxy_url=PROXY, apply=True, revert=False, client="claude-code", all=False,
    )
    rc = run_integrate(legacy)
    assert rc == 0
    data = json.loads(p.read_text())
    assert "defaultMode" not in data["permissions"]


# ---------------------------------------------------------------------------
# Codex --apply --tier
# ---------------------------------------------------------------------------


def test_apply_tier_auto_codex(tmp_home, capsys):
    p = _seed_codex(tmp_home)
    rc = run_integrate(_args(client="codex", tier="auto"))
    assert rc == 0
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    text = p.read_text()
    cfg = tomllib.loads(text)
    assert cfg["approval_policy"] == "never"
    assert cfg["sandbox_mode"] == "workspace-write"
    # Unrelated keys + comments untouched
    assert cfg["model"] == "gpt-5"
    assert cfg["profiles"]["work"]["model"] == "o4"
    assert "# keep this comment" in text
    # Backup created before the write
    assert (tmp_home / ".codex" / "config.toml.bak").exists()
    out = capsys.readouterr().out
    assert "tier: auto" in out


def test_apply_tier_strict_codex(tmp_home):
    p = _seed_codex(tmp_home)
    rc = run_integrate(_args(client="codex", tier="strict"))
    assert rc == 0
    cfg = perms._read_codex_config()
    assert cfg["approval_policy"] == "on-request"
    assert cfg["sandbox_mode"] == "read-only"


# ---------------------------------------------------------------------------
# --tier fleet via integrate
# ---------------------------------------------------------------------------


def test_apply_tier_fleet_with_yes(tmp_home, capsys):
    """Fleet: warning printed, default persistent tier applied, launcher
    state enabled, and no bypass values persisted into client config."""
    claude_p = _seed_claude(tmp_home)
    rc = run_integrate(_args(tier="fleet", yes=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fleet mode" in out
    assert "enabled" in out
    # Launcher state set
    assert perms.fleet_mode_enabled() is True
    # Persistent tier written as the default (fleet itself never persists)
    data = json.loads(claude_p.read_text())
    assert data["permissions"]["defaultMode"] == "acceptEdits"
    # Fleet never leaks bypass values into client config
    assert "bypassPermissions" not in claude_p.read_text()


def test_apply_tier_fleet_leaves_existing_tier_unchanged(tmp_home, capsys):
    claude_p = _seed_claude(tmp_home)
    assert perms.apply_claude_tier("auto").ok
    rc = run_integrate(_args(tier="fleet", yes=True))
    assert rc == 0
    data = json.loads(claude_p.read_text())
    assert data["permissions"]["defaultMode"] == "bypassPermissions"  # auto, unchanged
    assert perms.fleet_mode_enabled() is True
    assert "unchanged" in capsys.readouterr().out


def test_apply_tier_fleet_without_yes_non_tty_refuses(tmp_home, capsys):
    claude_p = _seed_claude(tmp_home)
    before = claude_p.read_text()
    rc = run_integrate(_args(tier="fleet", yes=False))
    assert rc == 1
    assert perms.fleet_mode_enabled() is False
    assert claude_p.read_text() == before
    assert "--yes" in capsys.readouterr().out


def test_apply_tier_fleet_codex_no_bypass_leak(tmp_home):
    codex_p = _seed_codex(tmp_home)
    rc = run_integrate(_args(client="codex", tier="fleet", yes=True))
    assert rc == 0
    text = codex_p.read_text()
    # Default tier persisted; never the bypass-shaped values
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    cfg = tomllib.loads(text)
    assert cfg["approval_policy"] == "on-request"
    assert cfg["sandbox_mode"] == "workspace-write"
    assert '"never"' not in text
    assert "danger-full-access" not in text
    assert perms.fleet_mode_enabled() is True


# ---------------------------------------------------------------------------
# Additive invariants through the integrate path
# ---------------------------------------------------------------------------


def test_integrate_tier_preserves_allow_deny_ask(tmp_home):
    p = tmp_home / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "permissions": {
            "allow": ["a", "b"],
            "deny": ["c"],
            "ask": ["d"],
            "additionalDirectories": ["/srv/data"],
        },
        "hooks": {"SessionStart": []},
    }))
    rc = run_integrate(_args(tier="auto"))
    assert rc == 0
    data = json.loads(p.read_text())
    assert data["permissions"]["allow"] == ["a", "b"]
    assert data["permissions"]["deny"] == ["c"]
    assert data["permissions"]["ask"] == ["d"]
    assert data["permissions"]["additionalDirectories"] == ["/srv/data"]
    assert data["hooks"] == {"SessionStart": []}
    assert data["permissions"]["defaultMode"] == "bypassPermissions"
