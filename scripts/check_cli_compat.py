#!/usr/bin/env python3
"""
Breaking Change Detection: CLI Command Rename Check
====================================================

Ensures that CLI commands aren't silently renamed without a deprecation alias.
Compares current CLI commands against a registry of known stable commands.

Usage:
    python scripts/check_cli_compat.py

Exit codes:
    0 — OK, all known commands still present
    1 — Breaking change: known command missing (renamed without deprecation alias)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Commands that are part of the stable public API.
# If any of these are removed or renamed, it's a breaking change.
STABLE_COMMANDS = [
    "serve",
    "status",
    "doctor",
    "index",
    "version",
    "route",
    "search",
    "stats",
]


def get_current_commands() -> list[str]:
    """Return the live top-level CLI command names by introspecting the parser.

    H3 hardening (L11b release-gate integrity): the previous implementation
    regex-scanned a hard-coded ``tokenpak/cli.py`` path. That module does not
    exist — the CLI is built by ``tokenpak._cli_core.build_parser()`` — so the
    old code fell into the ``file missing -> return STABLE_COMMANDS`` branch on
    *every* run and could never detect a breaking rename (fail-open). We now
    import the canonical parser builder and walk its registered subcommands. A
    missing module, an unimportable CLI, or a parser with no subcommands is a
    HARD FAILURE (the gate cannot do its job), not a silent pass.
    """
    try:
        from tokenpak._cli_core import build_parser
    except Exception as e:  # ImportError or any import-time failure
        print(f"❌ CLI compat gate cannot import tokenpak._cli_core.build_parser: {e}")
        print("   The gate cannot verify CLI commands — failing CLOSED (was fail-open / H3).")
        sys.exit(1)

    try:
        parser = build_parser()
    except Exception as e:
        print(f"❌ CLI compat gate could not build the CLI parser: {e}")
        print("   The gate cannot verify CLI commands — failing CLOSED (was fail-open / H3).")
        sys.exit(1)

    commands: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            commands.update(action.choices.keys())

    if not commands:
        print("❌ CLI compat gate found no subcommands on the parser — failing CLOSED (H3).")
        sys.exit(1)

    return sorted(commands)


def check_cli_compat():
    current = get_current_commands()
    missing = [cmd for cmd in STABLE_COMMANDS if cmd not in current]

    if missing:
        print("❌ BREAKING CHANGE DETECTED: Stable CLI commands missing (possible rename):")
        for cmd in missing:
            print(f"   - tokenpak {cmd}")
        print()
        print("Action required:")
        print("  1. Add a deprecation alias for the renamed command")
        print("  2. Keep the old command name for at least one major version")
        print("  3. OR update STABLE_COMMANDS in scripts/check_cli_compat.py if intentional")
        sys.exit(1)
    else:
        print(f"✅ CLI compat OK — {len(STABLE_COMMANDS)} stable command(s) all present")
        for cmd in STABLE_COMMANDS:
            print(f"   ✓ tokenpak {cmd}")


if __name__ == "__main__":
    print("TokenPak Breaking Change Detection — CLI Compatibility Check")
    print("=" * 60)
    check_cli_compat()
    print()
    print("✅ CLI compat check passed")
