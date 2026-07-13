# SPDX-License-Identifier: Apache-2.0
"""Reverse ``tokenpak codex --install-only``.

Removes everything the installer wrote, but leaves user-owned data
(journal.db, budget.db, capsules/) alone — those are the user's own
session history, not installation artifacts.

Idempotent: running twice is safe.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING as _TYPE_CHECKING

from .hooks import TOKENPAK_HOOK_MARKER
from .mcp_config import SERVER_NAME
from .mcp_config import unregister as mcp_unregister
from .rates_snapshot import DEFAULT_SNAPSHOT_PATH
from .skills_installer import _clean_skills_config, uninstall_skills

if _TYPE_CHECKING:
    from .session_home import SessionPaths


def _selected_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def remove_mcp() -> bool:
    """Remove the MCP registration from Codex. Returns True if gone."""
    return _remove_mcp()


def _remove_mcp(codex_home: Path | None = None) -> bool:
    """Remove MCP registration from one internally selected Codex home."""
    return mcp_unregister(codex_home)


def clean_hooks_json(path: Path | None = None) -> "tuple[bool, str]":
    """Strip tokenpak entries from hooks.json, preserving anything else.

    Returns (changed, detail).
    """
    path = path or (_selected_codex_home() / "hooks.json")
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
                if isinstance(c, dict) and TOKENPAK_HOOK_MARKER not in c.get("command", "")
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
    path = path or (_selected_codex_home() / "AGENTS.md")
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


def _clean_selected_skills_config(path: Path) -> "tuple[bool, str]":
    """Remove TokenPak skill references from one selected config."""
    changed = _clean_skills_config(path)
    if changed:
        return True, f"removed TokenPak skill references from {path}"
    return False, f"no TokenPak skill references in {path}"


def _global_skill_removal_is_safe(paths: "SessionPaths") -> "tuple[bool, str]":
    """Allow implicit removal only when a shared home has no managed peers."""
    if paths.mode != "shared":
        return False, "selected isolated/workspace home does not own global skills"

    from .session_home import (
        _RETENTION_GUARD_NAME,
        _RETENTION_RECEIPT_NAME,
        _tokenpak_home,
    )

    tokenpak_home = _tokenpak_home()
    getuid = getattr(os, "geteuid", None)
    owner_uid = getuid() if getuid is not None else None
    chain = (
        (tokenpak_home, {0o700}),
        (tokenpak_home / "companion", {0o700, 0o775}),
        (tokenpak_home / "companion" / "codex", {0o700}),
    )
    for directory, modes in chain:
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return True, "no other managed Codex homes"
        except OSError:
            return False, f"cannot safely inspect managed homes at {directory}"
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) not in modes
            or (owner_uid is not None and info.st_uid != owner_uid)
        ):
            return False, f"managed-home boundary is unsafe at {directory}"
    ignored = {_RETENTION_GUARD_NAME, _RETENTION_RECEIPT_NAME}
    for namespace in ("sessions", "workspaces"):
        root = tokenpak_home / "companion" / "codex" / namespace
        try:
            info = root.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False, f"cannot safely inspect managed homes at {root}"
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or (owner_uid is not None and info.st_uid != owner_uid)
        ):
            return False, f"managed-home namespace is unsafe at {root}"
        try:
            with os.scandir(root) as entries:
                if any(entry.name not in ignored for entry in entries):
                    return False, f"global skills are referenced by another {namespace} home"
        except OSError:
            return False, f"cannot safely inspect managed homes at {root}"
    return True, "no other managed Codex homes"


def _run_selected(
    *,
    paths: "SessionPaths | None" = None,
    codex_home: Path | None = None,
    session_mode: str | None = None,
    workspace_dir: Path | None = None,
    remove_global_skills: bool | None = None,
) -> int:
    """Internal selected-home uninstall runner."""
    if paths is None:
        from .session_home import InvalidSessionMode, current_paths, select_paths

        try:
            if codex_home is not None:
                paths = select_paths(
                    session_mode,
                    workspace_dir=workspace_dir,
                    selected_home=codex_home,
                )
            else:
                paths = current_paths(session_mode, workspace_dir=workspace_dir)
        except (InvalidSessionMode, ValueError) as exc:
            print(f"tokenpak codex uninstall: {exc}", file=sys.stderr)
            return 2

    from . import state_lock
    from .session_home import HomeInUseError, SessionLease

    status = state_lock.probe(paths.home)
    if status.locked:
        print(state_lock.remediation_hint(status), file=sys.stderr)
        return 1

    lease: SessionLease | None = None
    if paths.home.exists():
        try:
            lease = SessionLease.acquire(paths)
        except (OSError, RuntimeError, HomeInUseError) as exc:
            print(
                f"tokenpak codex uninstall: selected home is active or unsafe: {exc}",
                file=sys.stderr,
            )
            return 1
        # Native Codex does not participate in the lifecycle lease.  Sample
        # attachments once more after acquiring it and immediately before any
        # uninstall mutation to narrow the probe-to-cleanup race.
        status = state_lock.probe(paths.home)
        if status.locked:
            lease.release()
            print(state_lock.remediation_hint(status), file=sys.stderr)
            return 1

    errors: list[str] = []

    print("tokenpak codex uninstall:")
    for label, value in paths.report_rows():
        print(f"  {label}: {value}")
    print()

    try:
        try:
            mcp_ok = _remove_mcp(paths.home)
            print(f"  [{'ok' if mcp_ok else '—'}] MCP registration: {SERVER_NAME}")
        except Exception as exc:
            errors.append(f"MCP unregister: {exc}")
            print(f"  [!!] MCP registration: {exc}")

        cleaners = [
            ("hooks.json", lambda: clean_hooks_json(paths.hooks)),
            ("AGENTS.md", lambda: clean_agents_md(paths.agents)),
        ]
        if paths.mode != "shared":
            cleaners.append(("skills config", lambda: _clean_selected_skills_config(paths.config)))
        cleaners.append(("rates snapshot", clean_rates_snapshot))
        for label, fn in cleaners:
            try:
                changed, detail = fn()
                tag = "ok" if changed else "—"
                print(f"  [{tag}] {label}: {detail}")
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                print(f"  [!!] {label}: {exc}")

        removal_reason = "explicit top-level uninstall"
        if remove_global_skills is None:
            remove_global_skills, removal_reason = _global_skill_removal_is_safe(paths)
        if remove_global_skills:
            try:
                removed = uninstall_skills()
                detail = f"removed {len(removed)}" if removed else "nothing to remove"
                tag = "ok" if removed else "—"
                print(f"  [{tag}] global skills: {detail}")
            except Exception as exc:
                errors.append(f"global skills: {exc}")
                print(f"  [!!] global skills: {exc}")
        else:
            print(
                f"  [—] global skills: retained ({removal_reason}; "
                "top-level tokenpak uninstall removes them explicitly)"
            )
    finally:
        if lease is not None:
            lease.release()

    print()
    print("journal.db + budget.db retained (user data)")
    if errors:
        print(f"{len(errors)} errors during uninstall", file=sys.stderr)
        return 1
    return 0


def run() -> int:
    """Execute uninstall and print a report. Returns 0 on clean run."""
    return _run_selected()


def _run_global() -> int:
    """Reverse the global install as part of top-level TokenPak uninstall."""
    return _run_selected(remove_global_skills=True)


def main(argv: list[str] | None = None) -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
