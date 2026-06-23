# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``tokenpak upgrade`` command."""

from __future__ import annotations

import argparse

from tokenpak import licensing as _lic
from tokenpak._cli_core import _COMMAND_GROUPS, _core_command_names, build_parser
from tokenpak.cli.commands import help as help_cmd
from tokenpak.cli.commands import license_cmd, status, upgrade


def test_upgrade_prints_default_url(monkeypatch, capsys):
    monkeypatch.delenv(upgrade.UPGRADE_URL_ENV, raising=False)

    rc = upgrade.cmd_upgrade(argparse.Namespace(print_url=True))

    assert rc == 0
    assert capsys.readouterr().out.strip() == "https://tokenpak.ai/pro"


def test_upgrade_honors_env_override(monkeypatch, capsys):
    monkeypatch.setenv(upgrade.UPGRADE_URL_ENV, "https://example.test/upgrade")

    rc = upgrade.cmd_upgrade(argparse.Namespace(print_url=True))

    assert rc == 0
    assert capsys.readouterr().out.strip() == "https://example.test/upgrade"


def test_upgrade_opens_browser(monkeypatch, capsys):
    monkeypatch.delenv(upgrade.UPGRADE_URL_ENV, raising=False)
    opened = {}

    def fake_open(url: str, new: int = 0) -> bool:
        opened["url"] = url
        opened["new"] = new
        return True

    monkeypatch.setattr(upgrade.webbrowser, "open", fake_open)

    rc = upgrade.cmd_upgrade(argparse.Namespace(print_url=False))

    assert rc == 0
    assert opened == {"url": "https://tokenpak.ai/pro", "new": 2}
    assert "Opening https://tokenpak.ai/pro" in capsys.readouterr().out


def test_upgrade_prints_manual_url_when_browser_does_not_open(monkeypatch, capsys):
    monkeypatch.delenv(upgrade.UPGRADE_URL_ENV, raising=False)
    monkeypatch.setattr(upgrade.webbrowser, "open", lambda url, new=0: False)

    rc = upgrade.cmd_upgrade(argparse.Namespace(print_url=False))

    assert rc == 0
    out = capsys.readouterr().out
    assert "visit the URL manually" in out
    assert "https://tokenpak.ai/pro" in out


def test_upgrade_registered_in_help_group():
    getting_started = dict(_COMMAND_GROUPS["Getting Started"])

    assert getting_started["upgrade"] == "Open the TokenPak Pro upgrade page"
    assert "upgrade" in _core_command_names()


def test_upgrade_registered_in_help_registry():
    commands = {cmd["command"]: cmd for cmd in help_cmd._load_registry()}

    assert commands["upgrade"]["usage"] == "/tokenpak upgrade [--print-url]"
    assert commands["upgrade"]["category"] == "Control"
    assert help_cmd._ESSENTIAL_COMMANDS["upgrade"] == (
        "Open the TokenPak Pro upgrade page"
    )


def test_upgrade_subparser_dispatches_to_cmd_upgrade():
    parser = build_parser()

    ns = parser.parse_args(["upgrade", "--print-url"])

    assert ns.func is upgrade.cmd_upgrade
    assert ns.print_url is True


def test_free_tier_license_output_uses_pro_upgrade_url():
    rendered = license_cmd._render_summary(
        {
            "tier": _lic.TIER_FREE,
            "tier_label": "Free",
            "status": "active",
            "email": "",
            "activated_at": "",
            "expires_at": "",
            "has_key": False,
            "license_path": "/tmp/tokenpak/license.json",
            "enabled_gated_count": 0,
            "gated_feature_count": 1,
        }
    )

    assert "Upgrade path: https://tokenpak.ai/pro" in rendered
    assert "pricing" not in rendered
    assert "coming soon" not in rendered


def test_status_upgrade_hint_only_for_free_tier(monkeypatch):
    monkeypatch.setattr(
        status._lic,
        "summary_for_cli",
        lambda: {"tier": _lic.TIER_FREE},
    )
    assert status._free_tier_upgrade_hint() == (
        "  Upgrade to Pro: https://tokenpak.ai/pro  (or run `tokenpak upgrade`)"
    )

    monkeypatch.setattr(
        status._lic,
        "summary_for_cli",
        lambda: {"tier": _lic.TIER_PRO},
    )
    assert status._free_tier_upgrade_hint() is None


def test_status_unreachable_path_still_shows_upgrade_hint(monkeypatch, capsys):
    monkeypatch.setattr(status, "_fetch", lambda url: None)
    monkeypatch.setattr(status, "_get_version", lambda: "0.test")
    monkeypatch.setattr(
        status,
        "_calculate_fleet_savings",
        lambda **kwargs: {"error": "db_not_found"},
    )
    monkeypatch.setattr(
        status._lic,
        "summary_for_cli",
        lambda: {"tier": _lic.TIER_FREE},
    )

    status.run(no_meme=True)

    out = capsys.readouterr().out
    assert "Proxy unreachable and no monitor database found" in out
    assert "Upgrade to Pro: https://tokenpak.ai/pro" in out
