"""
tokenpak help — Tier-aware, programmatic help system.

Commands:
    /tokenpak help                  Full filtered command list for current tier
    /tokenpak help <command>        Detailed per-command help
    /tokenpak help --minimal        One-line command list
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
# Tier ordering (lower index = lower tier)
# ─────────────────────────────────────────────

_TIER_ORDER = ["oss", "pro", "team", "enterprise"]

_TIER_LABELS = {
    "oss": "OSS (Community Edition)",
    "pro": "Pro",
    "team": "Team",
    "enterprise": "Enterprise",
}

_UPSELL_MESSAGES = {
    "oss": "Upgrade to PRO to unlock adaptive compression, smart routing, and real-time dashboards.",
    "pro": "Upgrade to TEAM to unlock multi-agent coordination, distributed workflows, and SLA management.",
    "team": "Upgrade to ENTERPRISE to unlock compliance reporting, audit logs, and encrypted vault storage.",
}

# ─────────────────────────────────────────────
# Registry loader
# ─────────────────────────────────────────────

_REGISTRY_PATH = Path(__file__).parent.parent.parent.parent / "registry" / "commands.json"


def _load_registry() -> list[dict]:
    """Load command registry from JSON. Returns empty list on failure."""
    try:
        with open(_REGISTRY_PATH) as f:
            data = json.load(f)
        return data.get("commands", [])
    except Exception:
        return []


# ─────────────────────────────────────────────
# Tier detection
# ─────────────────────────────────────────────

def _current_tier() -> str:
    """Return current license tier string (oss/pro/team/enterprise). Never raises."""
    try:
        from tokenpak.agent.license.activation import get_plan
        result = get_plan()
        return result.tier.value
    except Exception:
        return "oss"


def _tier_rank(tier: str) -> int:
    try:
        return _TIER_ORDER.index(tier.lower())
    except ValueError:
        return 0


def _is_visible(cmd_tier: str, user_tier: str) -> bool:
    """Return True if user's tier includes access to cmd_tier."""
    return _tier_rank(cmd_tier) <= _tier_rank(user_tier)


# ─────────────────────────────────────────────
# Help output functions
# ─────────────────────────────────────────────

def _group_commands(commands: list[dict]) -> dict[str, list[dict]]:
    """Group command list by category, preserving order."""
    groups: dict[str, list[dict]] = {}
    for cmd in commands:
        cat = cmd.get("category", "Other")
        groups.setdefault(cat, []).append(cmd)
    return groups


def print_full_help(tier: Optional[str] = None) -> None:
    """Print tier-filtered full help."""
    if tier is None:
        tier = _current_tier()
    tier_label = _TIER_LABELS.get(tier, tier.upper())

    commands = _load_registry()
    visible = [c for c in commands if _is_visible(c.get("tier", "oss"), tier)]
    groups = _group_commands(visible)

    print(f"TokenPak — LLM Proxy with Context Compression")
    print(f"Tier: {tier_label}\n")
    print("Commands:\n")

    for group_name, cmds in groups.items():
        print(f"  {group_name}:")
        for cmd in cmds:
            name = cmd["command"]
            desc = cmd.get("description", "")
            aliases = cmd.get("aliases", [])
            alias_str = f"  (alias: {', '.join(aliases)})" if aliases else ""
            print(f"    {name:<16} {desc}{alias_str}")
        print()

    upsell = _UPSELL_MESSAGES.get(tier)
    if upsell:
        print(f"  ↑ {upsell}")
        print()

    print("Run `tokenpak help <command>` for details.")
    print("Run `tokenpak help --minimal` for a compact list.")


def print_minimal_help(tier: Optional[str] = None) -> None:
    """Print one-line compact command list filtered by tier."""
    if tier is None:
        tier = _current_tier()
    tier_label = _TIER_LABELS.get(tier, tier.upper())

    commands = _load_registry()
    visible = [c["command"] for c in commands if _is_visible(c.get("tier", "oss"), tier)]

    print(f"TokenPak [{tier_label}]  —  available commands:")
    print("  " + "  ".join(visible))

    upsell = _UPSELL_MESSAGES.get(tier)
    if upsell:
        print(f"\n  ↑ {upsell}")


def print_command_help(command_name: str) -> None:
    """Print detailed help for a specific command."""
    commands = _load_registry()

    # Search by name or alias
    target = None
    for cmd in commands:
        if cmd["command"] == command_name or command_name in cmd.get("aliases", []):
            target = cmd
            break

    if target is None:
        print(f"Unknown command: {command_name!r}")
        print("Run `tokenpak help` to see all available commands.")
        sys.exit(1)

    tier = target.get("tier", "oss")
    tier_label = _TIER_LABELS.get(tier, tier.upper())
    current = _current_tier()

    print(f"tokenpak {target['command']}")
    print("─" * 40)
    print(f"  Purpose  : {target.get('description', '')}")
    print(f"  Tier     : {tier_label}")
    print(f"  Usage    : {target.get('usage', '')}")
    print()

    detail = target.get("detail", "")
    if detail:
        print(f"  {detail}")
        print()

    aliases = target.get("aliases", [])
    if aliases:
        print(f"  Aliases  : {', '.join(aliases)}")

    related = target.get("related", [])
    if related:
        print(f"  Related  : {', '.join(related)}")

    if not _is_visible(tier, current):
        current_label = _TIER_LABELS.get(current, current.upper())
        print()
        print(f"  ⚠️  This command requires {tier_label}. (You are on {current_label}.)")


# ─────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────

def run(args: Optional[list[str]] = None) -> None:
    """Entry point: parse args and dispatch to appropriate help function."""
    if args is None:
        args = sys.argv[2:]  # skip 'tokenpak' and 'help'

    if not args:
        print_full_help()
        return

    if args[0] == "--minimal":
        print_minimal_help()
        return

    if args[0].startswith("-"):
        print(f"Unknown option: {args[0]!r}")
        sys.exit(1)

    print_command_help(args[0])


# ─────────────────────────────────────────────
# Click integration (optional)
# ─────────────────────────────────────────────

try:
    import click

    @click.command("help")
    @click.argument("command", required=False, default=None)
    @click.option("--minimal", is_flag=True, help="Show compact one-line command list")
    def help_cmd(command: Optional[str], minimal: bool):
        """Show tier-aware help. Use `help <command>` for details."""
        if minimal:
            print_minimal_help()
        elif command:
            print_command_help(command)
        else:
            print_full_help()

except ImportError:
    pass
