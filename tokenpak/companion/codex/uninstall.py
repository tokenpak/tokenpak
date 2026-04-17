# SPDX-License-Identifier: Apache-2.0
"""Reverse ``tokenpak codex --install-only``.

Removes everything the installer wrote, but leaves user-owned data
(journal.db, budget.db, capsules/) alone — those are the user's own
session history, not installation artifacts.

Idempotent: running twice is safe.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .mcp_config import SERVER_NAME, unregister as mcp_unregister
from .skills_installer import uninstall_skills
from .hooks import TOKENPAK_HOOK_MARKER
from .rates_snapshot import DEFAULT_SNAPSHOT_PATH


def remove_mcp() -> bool:
    """Remove the MCP registration from Codex. Returns True if gone."""
    return mcp_unregister()


def clean_hooks_json(path: Path | None = None) -> "tuple[bool, str]":
    """Strip tokenpak entries from hooks.json, preserving anything else.

    Returns (changed, detail).
    """
    path = path or (Path.home() / ".codex" / "hooks.json")
    if not path.exists():
        return False, "hooks.json absent"
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON, leaving alone: {exc}"

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False, "no dict-shaped hooks to clean"

    cleaned: dict[str, list[dict]] = {}
    changed = False
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        kept_groups: list[dict] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            commands = group.get("hooks", [])
            non_tokenpak = [
                c
                for c in commands
                if isinstance(c, dict)
                and TOKENPAK_HOOK_MARKER not in c.get("command", "")
            ]
            if len(non_tokenpak) != len(commands):
                changed = True
            if non_tokenpak:
                kept_groups.append({**group, "hooks": non_tokenpak})
        if kept_groups:
            cleaned[event] = kept_groups

    if not cleaned:
        path.unlink()
        return True, f"deleted {path} (no other hooks remained)"

    if changed:
        path.write_text(json.dumps({"hooks": cleaned}, indent=2) + "\n")
        return True, f"stripped tokenpak entries from {path}"
    return False, "no tokenpak entries to remove"


def clean_agents_md(path: Path | None = None) -> "tuple[bool, str]":
    """Remove the ``# TokenPak Companion`` section from AGENTS.md.

    Uses the same section-boundary logic as :func:`agents_md._merge_agents`:
    everything from the marker heading up to the next top-level ``# `` heading.
    """
    path = path or (Path.home() / ".codex" / "AGENTS.md")
    if not path.exists():
        return False, "AGENTS.md absent"

    marker = "# TokenPak Companion"
    existing = path.read_text()
    if marker not in existing:
        return False, "no TokenPak section to remove"

    lines = existing.split("\n")
    kept: list[str] = []
    in_section = False
    for line in lines:
        if line.strip() == marker:
            in_section = True
            continue
        if in_section:
            if line.startswith("# ") and line.strip() != marker:
                in_section = False
                kept.append(line)
            # skip lines inside the section
            continue
        kept.append(line)

    remaining = "\n".join(kept).strip()
    if not remaining:
        path.unlink()
        return True, f"deleted {path} (only tokenpak section inside)"

    path.write_text(remaining + "\n")
    return True, f"removed tokenpak section from {path}"


def clean_rates_snapshot() -> "tuple[bool, str]":
    if DEFAULT_SNAPSHOT_PATH.exists():
        DEFAULT_SNAPSHOT_PATH.unlink()
        return True, f"removed {DEFAULT_SNAPSHOT_PATH}"
    return False, "rates snapshot absent"


def run() -> int:
    """Execute uninstall and print a report. Returns 0 on clean run."""
    errors: list[str] = []

    print("tokenpak codex uninstall:")

    try:
        mcp_ok = remove_mcp()
        print(f"  [{'ok' if mcp_ok else '—'}] MCP registration: {SERVER_NAME}")
    except Exception as exc:
        errors.append(f"MCP unregister: {exc}")
        print(f"  [!!] MCP registration: {exc}")

    for label, fn in (
        ("hooks.json", clean_hooks_json),
        ("AGENTS.md", clean_agents_md),
        ("rates snapshot", clean_rates_snapshot),
    ):
        try:
            changed, detail = fn()
            tag = "ok" if changed else "—"
            print(f"  [{tag}] {label}: {detail}")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            print(f"  [!!] {label}: {exc}")

    try:
        removed = uninstall_skills()
        detail = f"removed {len(removed)}" if removed else "nothing to remove"
        tag = "ok" if removed else "—"
        print(f"  [{tag}] skills: {detail}")
    except Exception as exc:
        errors.append(f"skills: {exc}")
        print(f"  [!!] skills: {exc}")

    print()
    print("journal.db + budget.db retained (user data)")
    if errors:
        print(f"{len(errors)} errors during uninstall", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
