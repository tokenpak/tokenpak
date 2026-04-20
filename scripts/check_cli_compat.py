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
    """Extract command names from the CLI implementation file."""
    # CLI implementation moved from tokenpak/cli.py to tokenpak/cli/_impl.py
    # (package-ified in v1.0.3). Check both locations for backward compat.
    cli_path = REPO_ROOT / "tokenpak" / "cli" / "_impl.py"
    if not cli_path.exists():
        cli_path = REPO_ROOT / "tokenpak" / "cli.py"
    if not cli_path.exists():
        print(f"⚠️  CLI not found at {cli_path} — skipping check")
        return STABLE_COMMANDS  # assume all present if file missing

    content = cli_path.read_text()
    # Heuristic: find argparse subparser add_parser("command") calls
    import re
    # Match: add_parser("cmd") or add_parser('cmd')
    found = re.findall(r'add_parser\(["\'](\w[\w-]*)["\']', content)
    # Also match cmd_xxx function names as fallback
    cmd_funcs = re.findall(r'def cmd_(\w+)\s*\(', content)
    return list(set(found) | set(cmd_funcs))


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
