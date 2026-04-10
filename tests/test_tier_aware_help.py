"""Tests for tier-aware help system."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

REGISTRY_PATH = Path(__file__).parent.parent / "tokenpak" / "registry" / "commands.json"


def _load_registry() -> list[dict]:
    with open(REGISTRY_PATH) as f:
        return json.load(f)["commands"]


def _mock_tier(tier: str):
    """Context manager to fake a license tier."""
    mock_result = MagicMock()
    mock_result.tier.value = tier
    return patch(
        "tokenpak.cli.commands.help._current_tier",
        return_value=tier,
    )


def _capture(fn, *args, **kwargs) -> str:
    """Run fn(*args, **kwargs) and return stdout as string."""
    buf = StringIO()
    with patch("sys.stdout", buf):
        fn(*args, **kwargs)
    return buf.getvalue()


# ─────────────────────────────────────────────
# 1. Registry: structure validation
# ─────────────────────────────────────────────

class TestCommandRegistry:
    def test_registry_file_exists(self):
        assert REGISTRY_PATH.exists(), "commands.json not found"

    def test_registry_has_commands(self):
        commands = _load_registry()
        assert len(commands) > 10, "Expected at least 10 commands"

    def test_all_commands_have_required_fields(self):
        required = {"command", "tier", "category", "description", "usage"}
        commands = _load_registry()
        for cmd in commands:
            missing = required - cmd.keys()
            assert not missing, f"Command {cmd.get('command')} missing fields: {missing}"

    def test_all_tiers_are_valid(self):
        valid_tiers = {"oss", "pro", "team", "enterprise"}
        commands = _load_registry()
        for cmd in commands:
            assert cmd["tier"] in valid_tiers, (
                f"Command {cmd['command']} has invalid tier: {cmd['tier']!r}"
            )

    def test_registry_covers_all_tier_levels(self):
        commands = _load_registry()
        tiers_present = {c["tier"] for c in commands}
        assert tiers_present == {"oss", "pro", "team", "enterprise"}, (
            f"Expected all tiers represented, got: {tiers_present}"
        )

    def test_oss_commands_include_basics(self):
        commands = _load_registry()
        oss_cmds = {c["command"] for c in commands if c["tier"] == "oss"}
        for expected in ("start", "stop", "status", "cost", "doctor"):
            assert expected in oss_cmds, f"Expected OSS command: {expected}"


# ─────────────────────────────────────────────
# 2. Tier filtering
# ─────────────────────────────────────────────

class TestTierFiltering:
    def test_oss_only_sees_oss_commands(self):
        from tokenpak.cli.commands.help import _is_visible
        commands = _load_registry()
        # Pro commands should NOT be visible to OSS
        pro_cmds = [c for c in commands if c["tier"] == "pro"]
        for cmd in pro_cmds:
            assert not _is_visible("pro", "oss"), (
                f"Pro command {cmd['command']} should be hidden from OSS"
            )

    def test_enterprise_sees_all_commands(self):
        from tokenpak.cli.commands.help import _is_visible
        for tier in ("oss", "pro", "team", "enterprise"):
            assert _is_visible(tier, "enterprise"), f"Enterprise should see {tier} commands"

    def test_pro_sees_oss_and_pro(self):
        from tokenpak.cli.commands.help import _is_visible
        assert _is_visible("oss", "pro")
        assert _is_visible("pro", "pro")
        assert not _is_visible("team", "pro")
        assert not _is_visible("enterprise", "pro")

    def test_team_sees_oss_pro_and_team(self):
        from tokenpak.cli.commands.help import _is_visible
        assert _is_visible("oss", "team")
        assert _is_visible("pro", "team")
        assert _is_visible("team", "team")
        assert not _is_visible("enterprise", "team")


# ─────────────────────────────────────────────
# 3. Full help output
# ─────────────────────────────────────────────

class TestFullHelp:
    def test_full_help_shows_tier_badge(self):
        from tokenpak.cli.commands.help import print_full_help
        out = _capture(print_full_help, tier="oss")
        assert "OSS" in out, "Tier badge missing from full help"

    def test_full_help_filters_commands(self):
        from tokenpak.cli.commands.help import print_full_help
        oss_out = _capture(print_full_help, tier="oss")
        # 'audit' is enterprise-only — should not appear in OSS help
        assert "audit" not in oss_out, "Enterprise command 'audit' should not appear in OSS help"

    def test_full_help_pro_shows_optimize(self):
        from tokenpak.cli.commands.help import print_full_help
        pro_out = _capture(print_full_help, tier="pro")
        assert "optimize" in pro_out, "Pro command 'optimize' should appear in pro help"

    def test_full_help_enterprise_includes_compliance(self):
        from tokenpak.cli.commands.help import print_full_help
        ent_out = _capture(print_full_help, tier="enterprise")
        assert "compliance" in ent_out

    def test_full_help_has_usage_tip(self):
        from tokenpak.cli.commands.help import print_full_help
        out = _capture(print_full_help, tier="pro")
        assert "tokenpak help" in out, "Usage tip missing"


# ─────────────────────────────────────────────
# 4. Per-command help
# ─────────────────────────────────────────────

class TestCommandHelp:
    def test_command_help_shows_usage(self):
        from tokenpak.cli.commands.help import print_command_help
        out = _capture(print_command_help, "start")
        assert "Usage" in out or "usage" in out.lower()

    def test_command_help_shows_tier(self):
        from tokenpak.cli.commands.help import print_command_help
        out = _capture(print_command_help, "optimize")
        assert "Pro" in out, "Tier label missing from command help"

    def test_command_help_shows_related(self):
        from tokenpak.cli.commands.help import print_command_help
        out = _capture(print_command_help, "cost")
        assert "Related" in out or len(out) > 50

    def test_command_help_alias_resolves(self):
        from tokenpak.cli.commands.help import print_command_help
        # 'auto-optimize' is an alias for 'optimize'
        out = _capture(print_command_help, "auto-optimize")
        assert "optimize" in out.lower()

    def test_command_help_unknown_exits(self):
        from tokenpak.cli.commands.help import print_command_help
        with pytest.raises(SystemExit):
            print_command_help("nonexistent-command-xyz")


# ─────────────────────────────────────────────
# 5. Minimal help
# ─────────────────────────────────────────────

class TestMinimalHelp:
    def test_minimal_shows_oss_commands(self):
        from tokenpak.cli.commands.help import print_minimal_help
        out = _capture(print_minimal_help, tier="oss")
        assert "start" in out
        assert "status" in out

    def test_minimal_hides_enterprise_from_oss(self):
        from tokenpak.cli.commands.help import print_minimal_help
        out = _capture(print_minimal_help, tier="oss")
        assert "audit" not in out, "Enterprise command should not appear in OSS minimal help"

    def test_minimal_is_compact(self):
        from tokenpak.cli.commands.help import print_minimal_help
        out = _capture(print_minimal_help, tier="oss")
        # Should be few lines
        lines = [l for l in out.strip().splitlines() if l.strip()]
        assert len(lines) <= 5, f"Minimal help should be compact, got {len(lines)} lines"


# ─────────────────────────────────────────────
# 6. Upsell teaser
# ─────────────────────────────────────────────

class TestUpsellTeaser:
    def test_upsell_shown_for_oss(self):
        from tokenpak.cli.commands.help import print_full_help
        out = _capture(print_full_help, tier="oss")
        assert "Upgrade" in out or "PRO" in out, "Upsell teaser should appear for OSS tier"

    def test_upsell_not_shown_for_enterprise(self):
        from tokenpak.cli.commands.help import print_full_help
        out = _capture(print_full_help, tier="enterprise")
        assert "Upgrade" not in out, "No upsell teaser for Enterprise tier"

    def test_upsell_minimal_no_fluff(self):
        from tokenpak.cli.commands.help import print_full_help
        out = _capture(print_full_help, tier="oss")
        # Should not have long marketing copy
        upsell_lines = [l for l in out.splitlines() if "Upgrade" in l]
        for line in upsell_lines:
            assert len(line) < 200, f"Upsell line too long (marketing fluff?): {line!r}"
