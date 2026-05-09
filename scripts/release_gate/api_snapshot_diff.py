#!/usr/bin/env python3
"""api_snapshot_diff.py — diff public-api snapshots between two git refs.

Per Std 30 §6 / R6 (adjacent-removal guard) + Std 21 §11 (`_internal/` refactor
isolation rule). Used by `make api-snapshot-diff` and by Rei Po Phase 4.9
adjacent-removal audit.

Usage:
    python3 scripts/release_gate/api_snapshot_diff.py <base> <head>

Output: list of added (+) and removed (-) symbols, one per line. Exit 0 always
(diff itself is informational; gating happens in api_snapshot_check.py via the
PR body declaration check).
"""

from __future__ import annotations

import json
import subprocess
import sys

SNAP_PATH = "tokenpak/_snapshots/public-api.json"


def load_at(ref: str) -> set[tuple[str, str]]:
    try:
        out = subprocess.check_output(
            ["git", "show", f"{ref}:{SNAP_PATH}"], stderr=subprocess.DEVNULL
        )
        data = json.loads(out)
        return {(s["module"], s["name"]) for s in data.get("symbols", [])}
    except subprocess.CalledProcessError:
        # Snapshot didn't exist at that ref — treat as empty
        return set()
    except Exception as e:
        print(f"failed to load snapshot at {ref}: {e}", file=sys.stderr)
        return set()


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <base> <head>", file=sys.stderr)
        return 2
    base, head = sys.argv[1], sys.argv[2]
    base_syms = load_at(base)
    head_syms = load_at(head)
    added = sorted(head_syms - base_syms)
    removed = sorted(base_syms - head_syms)
    for m, n in added:
        print(f"+ {m}.{n}")
    for m, n in removed:
        print(f"- {m}.{n}")
    if not added and not removed:
        print("(no public-api changes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
