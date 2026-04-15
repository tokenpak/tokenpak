#!/usr/bin/env python3
"""
Stale Import Linter
===================

Detects deprecated import paths left behind after the Phase 3 modular
refactor.  Run this in CI to catch stale imports before they reach QA.

Usage:
    python scripts/lint-imports.py [--paths PATH ...]

    Default paths scanned: tokenpak/ and tests/ (relative to repo root).

Exit codes:
    0 — no stale imports found
    1 — one or more stale imports detected (file:line printed to stdout)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Mapping: stale module prefix  →  canonical replacement
#
# Each key is matched as a full dotted prefix so that
#   from tokenpak.agent.cli.commands.foo import bar
# is caught by the "tokenpak.agent.cli" rule (longest match wins).
# ---------------------------------------------------------------------------
STALE_PATTERNS: dict[str, str] = {
    # tokenpak.agent.cli.commands.* → tokenpak.cli.commands.*
    "tokenpak.agent.cli.commands": "tokenpak.cli.commands",
    # tokenpak.agent.cli.* → tokenpak.cli.*
    "tokenpak.agent.cli": "tokenpak.cli",
    # tokenpak.agent.compression.* → tokenpak.compression.*
    "tokenpak.agent.compression": "tokenpak.compression",
    # tokenpak._internal._cli_core → tokenpak._cli_core
    "tokenpak._internal._cli_core": "tokenpak._cli_core",
    # tokenpak._internal.* → tokenpak (internal package was dissolved)
    "tokenpak._internal": "tokenpak",
    # tokenpak.companion.mcp_server → tokenpak.companion.mcp
    "tokenpak.companion.mcp_server": "tokenpak.companion.mcp",
}

# Pre-sort longest key first so the most-specific match is tried first.
_SORTED_KEYS = sorted(STALE_PATTERNS, key=len, reverse=True)

# Matches:  from <module>  OR  import <module>
# Capture group 1 = the module string
_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+([\w.]+)",
    re.MULTILINE,
)


def _stale_match(module: str) -> str | None:
    """Return the stale key if *module* starts with it, else None."""
    for stale_prefix in _SORTED_KEYS:
        if module == stale_prefix or module.startswith(stale_prefix + "."):
            return stale_prefix
    return None


def lint_file(path: Path) -> list[str]:
    """Return a list of violation strings for *path*."""
    violations: list[str] = []
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"warning: could not read {path}: {exc}", file=sys.stderr)
        return violations

    for match in _IMPORT_RE.finditer(source):
        module = match.group(1)
        stale_key = _stale_match(module)
        if stale_key is None:
            continue
        # Compute 1-based line number
        line_no = source[: match.start()].count("\n") + 1
        canonical = STALE_PATTERNS[stale_key]
        violations.append(
            f"{path}:{line_no}: stale import '{module}' — use '{canonical}' instead"
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Detect stale tokenpak import paths after Phase 3 refactor."
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        metavar="PATH",
        help="Directories or files to scan (default: tokenpak/ tests/)",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).parent.parent
    scan_roots: list[Path]
    if args.paths:
        scan_roots = [Path(p) for p in args.paths]
    else:
        scan_roots = [
            repo_root / "tokenpak",
            repo_root / "tests",
        ]

    all_violations: list[str] = []
    for root in scan_roots:
        if root.is_file():
            targets = [root]
        elif root.is_dir():
            targets = sorted(root.rglob("*.py"))
        else:
            print(f"warning: path not found: {root}", file=sys.stderr)
            continue
        for py_file in targets:
            all_violations.extend(lint_file(py_file))

    if all_violations:
        for v in all_violations:
            print(v)
        print(
            f"\n{len(all_violations)} stale import(s) found. Fix before merging.",
            file=sys.stderr,
        )
        return 1

    print("OK: no stale imports found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
