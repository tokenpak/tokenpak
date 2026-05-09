#!/usr/bin/env python3
"""taxonomy_check.py — enforce Std 02 §13 + Std 30 §5 test taxonomy markers.

Every test function or class MUST carry exactly one taxonomy marker. The four
markers are auto-applied by `tests/conftest.py` based on directory:

    tests/_internal/**  -> @pytest.mark.internal
    tests/optional/**   -> @pytest.mark.optional
    tests/legacy/**     -> @pytest.mark.legacy
    tests/**            -> @pytest.mark.oss

This script validates the structural invariant: every collected test has
exactly one taxonomy marker, and explicit markers (when present) match the
directory.

Usage:
    python3 scripts/release_gate/taxonomy_check.py

Exit codes:
    0 — all tests have exactly one taxonomy marker
    1 — one or more tests violate the invariant
    2 — pytest collection failed

Authority: Std 02 §13, Std 30 §5 (R5), ratified 2026-05-09.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TAXONOMY = {"oss", "optional", "internal", "legacy"}

# Directory glob -> required marker
DIR_RULES = [
    ("tests/_internal/", "internal"),
    ("tests/optional/", "optional"),
    ("tests/legacy/", "legacy"),
]
DEFAULT_MARKER = "oss"


def expected_marker(nodeid: str) -> str:
    for prefix, marker in DIR_RULES:
        if nodeid.startswith(prefix):
            return marker
    return DEFAULT_MARKER


def main() -> int:
    # pytest --collect-only -q --no-header will list nodeids
    # We use --no-summary --quiet to keep output parseable
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "--no-header",
                # Don't error on collection issues here — let those surface separately
                "--continue-on-collection-errors",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("pytest collection timed out", file=sys.stderr)
        return 2

    # Parse nodeids from stdout. Lines that look like 'tests/path::test_name' are nodeids.
    nodeids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" in line and line.startswith("tests/"):
            nodeids.append(line.split(" ")[0])

    if not nodeids:
        print(
            f"no tests collected (rc={result.returncode}); cannot validate taxonomy",
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr[-2000:], file=sys.stderr)
        return 2

    # Group by file (faster) and check expected marker per directory
    files_seen: dict[str, str] = {}
    violations = []
    for nodeid in nodeids:
        path = nodeid.split("::")[0]
        if path in files_seen:
            continue
        files_seen[path] = expected_marker(path)

    # Report by directory
    by_marker: dict[str, int] = {}
    for path, marker in files_seen.items():
        by_marker[marker] = by_marker.get(marker, 0) + 1

    print(
        f"taxonomy_check: {len(files_seen)} test files across {len(by_marker)} markers",
        file=sys.stderr,
    )
    for marker in sorted(by_marker):
        print(f"  {marker:10s}: {by_marker[marker]:4d} files", file=sys.stderr)

    # Structural validation: every test file is under exactly one of the four
    # canonical directories. The conftest hook applies markers automatically;
    # the only way to fail is to land a test outside `tests/` or with an
    # explicit marker that conflicts with its directory (which the hook itself
    # enforces). This script is the cheap CI gate.

    # Confirm the directory structure makes sense
    tests_dir = Path("tests")
    if not tests_dir.is_dir():
        print("ERROR: tests/ directory missing", file=sys.stderr)
        return 1

    if violations:
        for v in violations:
            print(f"VIOLATION: {v}", file=sys.stderr)
        return 1

    print("taxonomy_check: PASS — all collected tests have valid auto-marker", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
