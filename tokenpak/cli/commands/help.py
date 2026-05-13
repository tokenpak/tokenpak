"""
tokenpak help — Programmatic help system.

Commands:
    /tokenpak help                  Essential commands only
    /tokenpak help --more           Essential + intermediate commands
    /tokenpak help --all            All commands (complete reference)
    /tokenpak help <command>        Detailed per-command help
    /tokenpak help --minimal        One-line command list
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
# Command tiers (complexity-based, not license-based)
# ─────────────────────────────────────────────

_ESSENTIAL_COMMANDS = {
    "setup": "Guided first-run configuration",
    "start": "Start the proxy",
    "stop": "Stop the proxy",
    "status": "Health, stats, and savings summary",
    "cost": "API spend and token usage",
    "savings": "What TokenPak saved you",
    "doctor": "Run diagnostics and health checks",
    "dashboard": "Open web metrics dashboard",
}

_INTERMEDIATE_COMMANDS = {
    "watch": "Live terminal savings dashboard",
    "logs": "View proxy logs",
    "stats": "Registry and cache statistics",
    "config": "View/edit configuration",
    "integrate": "Client-specific setup guides",
    "index": "Index files for context retrieval",
    "search": "Search indexed content",
    "demo": "See compression on sample data",
    "restart": "Restart the proxy",
    "version": "Show current versions",
}

# ─────────────────────────────────────────────
# Registry loader
# ─────────────────────────────────────────────

_REGISTRY_PATH = Path(__file__).parent.parent.parent / "core" / "registry" / "commands.json"


def _load_registry() -> list[dict]:
    """Load command registry from JSON. Returns empty list on failure."""
    try:
        with open(_REGISTRY_PATH) as f:
            data = json.load(f)
        return data.get("commands", [])
    except Exception:
        return []


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


def print_essential_help() -> None:
    """Print essential commands only (default view for new users)."""
    n = len(_load_registry())
    print("TokenPak — LLM Proxy with Prompt Packing\n")
    print("Essential Commands:\n")
    for cmd, desc in _ESSENTIAL_COMMANDS.items():
        print(f"  {cmd:<14} {desc}")
    print()
    print("Run `tokenpak help --more` for intermediate commands.")
    print(f"Run `tokenpak help --all` for all {n} commands.")
    print("Run `tokenpak help <command>` for details on any command.")


def print_intermediate_help() -> None:
    """Print essential + intermediate commands."""
    n = len(_load_registry())
    print("TokenPak — LLM Proxy with Prompt Packing\n")

    print("Essential Commands:\n")
    for cmd, desc in _ESSENTIAL_COMMANDS.items():
        print(f"  {cmd:<14} {desc}")
    print()

    print("Monitoring:\n")
    monitoring_cmds = {
        k: v for k, v in _INTERMEDIATE_COMMANDS.items() if k in ["watch", "logs", "stats"]
    }
    for cmd, desc in monitoring_cmds.items():
        print(f"  {cmd:<14} {desc}")
    print()

    print("Configuration:\n")
    config_cmds = {
        k: v
        for k, v in _INTERMEDIATE_COMMANDS.items()
        if k in ["config", "integrate", "restart", "version"]
    }
    for cmd, desc in config_cmds.items():
        print(f"  {cmd:<14} {desc}")
    print()

    print("Content:\n")
    content_cmds = {
        k: v for k, v in _INTERMEDIATE_COMMANDS.items() if k in ["index", "search", "demo"]
    }
    for cmd, desc in content_cmds.items():
        print(f"  {cmd:<14} {desc}")
    print()

    print(f"Run `tokenpak help --all` for all {n} commands.")
    print("Run `tokenpak help <command>` for details on any command.")


def print_full_help(tier: Optional[str] = None) -> None:
    """Print all commands."""
    commands = _load_registry()
    groups = _group_commands(commands)

    print("TokenPak — LLM Proxy with Prompt Packing\n")
    print("All Commands:\n")

    for group_name, cmds in groups.items():
        print(f"  {group_name}:")
        for cmd in cmds:
            name = cmd["command"]
            desc = cmd.get("description", "")
            aliases = cmd.get("aliases", [])
            alias_str = f"  (alias: {', '.join(aliases)})" if aliases else ""
            print(f"    {name:<16} {desc}{alias_str}")
        print()

    print("Run `tokenpak help <command>` for details.")


def print_minimal_help(tier: Optional[str] = None) -> None:
    """Print one-line compact command list."""
    commands = _load_registry()
    names = [c["command"] for c in commands]

    print("TokenPak  —  available commands:")
    print("  " + "  ".join(names))


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

    print(f"tokenpak {target['command']}")
    print("─" * 40)
    print(f"  Purpose  : {target.get('description', '')}")
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


# ─────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────


def run(args: Optional[list[str]] = None) -> None:
    """Entry point: parse args and dispatch to appropriate help function.

    Flags:
      --more     Show essential + intermediate commands
      --all      Show all commands (complete reference)
      --minimal  One-line compact command list
    """
    if args is None:
        args = sys.argv[2:]  # skip 'tokenpak' and 'help'

    if not args:
        # Default: show essential commands only
        print_essential_help()
        return

    if args[0] == "--more":
        print_intermediate_help()
        return

    if args[0] == "--all":
        print_full_help()
        return

    if args[0] == "--minimal":
        print_minimal_help()
        return

    if args[0].startswith("-"):
        print(f"Unknown option: {args[0]!r}")
        sys.exit(1)

    # Assume it's a command name (e.g., 'tokenpak help start')
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
        """Show help. Use `help <command>` for details."""
        if minimal:
            print_minimal_help()
        elif command:
            print_command_help(command)
        else:
            print_full_help()

except ImportError:
    pass
