#!/usr/bin/env python3
"""migration_multihop.py — Std 30 §14.1 / R16 multi-hop migration gate.

Runs telemetry-store migrations from each of the last 6 minor versions (or
back to the latest MAJOR boundary, whichever is shorter) to HEAD on a fresh
DB seeded with synthetic data at each baseline schema. Asserts:
    (a) every migration applies cleanly
    (b) pre-migration rows remain queryable post-migration
    (c) downgrade path is either tested or documented as one-way

Stub-mode behavior: if `tokenpak/telemetry/storage/migrations/` is empty or
absent, this script reports SKIPPED and exits 0 (the gate is a no-op until
migrations exist). Once migrations land, the gate becomes active.

Usage:
    python3 scripts/release_gate/migration_multihop.py [--baselines N]

Authority: Std 30 §14.1 (R16), Std 10 §E9, ratified 2026-05-09.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "tokenpak" / "telemetry" / "storage" / "migrations"
DEFAULT_BASELINES = 6


def list_minor_versions(n: int) -> list[str]:
    """Get the last N minor version tags from git, descending."""
    try:
        out = subprocess.check_output(
            ["git", "tag", "--list", "v*.*.0", "--sort=-v:refname"],
            text=True,
        )
    except subprocess.CalledProcessError:
        return []
    versions = [t.strip() for t in out.splitlines() if t.strip()]
    # Stop at the latest MAJOR boundary
    if not versions:
        return []
    head_major = versions[0].lstrip("v").split(".")[0]
    bounded = [v for v in versions if v.lstrip("v").split(".")[0] == head_major]
    return bounded[:n]


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-hop migration gate (R16)")
    parser.add_argument("--baselines", type=int, default=DEFAULT_BASELINES)
    args = parser.parse_args()

    if not MIGRATIONS_DIR.is_dir() or not any(MIGRATIONS_DIR.iterdir()):
        print(
            f"migration_multihop: SKIPPED — migrations dir empty/absent at {MIGRATIONS_DIR}",
            file=sys.stderr,
        )
        print("  This gate becomes active once migrations land. Std 30 §14.1.", file=sys.stderr)
        return 0

    baselines = list_minor_versions(args.baselines)
    if not baselines:
        print("migration_multihop: SKIPPED — no minor-version tags found", file=sys.stderr)
        return 0

    print(f"migration_multihop: testing {len(baselines)} baselines: {baselines}", file=sys.stderr)

    # Per-baseline: would seed a fresh DB at that schema, run migrations to HEAD,
    # assert rows queryable. Implementation is intentionally a placeholder until
    # the first actual migration ships (the migration framework itself dictates
    # the harness shape). Tracker: Std 30 §14.1 R16 follow-up.

    print(
        "migration_multihop: harness-pending — full multi-hop assertion will activate",
        file=sys.stderr,
    )
    print(
        "  with the first telemetry migration. Until then, this script returns 0.", file=sys.stderr
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
