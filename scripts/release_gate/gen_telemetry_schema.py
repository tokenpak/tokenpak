#!/usr/bin/env python3
"""gen_telemetry_schema.py — generate tokenpak/_snapshots/telemetry-schema.json.

Per Std 30 §7 / R7 (telemetry-schema snapshot). Captures the DDL of every
user-facing SQLite store. Schema bumps require a migration test in the same
PR (Std 10 §E8) AND multi-hop migration test passes (Std 10 §E9 / R16).

Usage:
    python3 scripts/release_gate/gen_telemetry_schema.py [--check] [--out PATH]

Authority: Std 30 §7, ratified 2026-05-09.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "tokenpak" / "_snapshots" / "telemetry-schema.json"

# User-facing SQLite stores tracked by Std 30 §7. Path is relative to user $HOME.
# Stores that don't exist yet are recorded with empty DDL but a known path
# (so a future creation produces a snapshot diff that flags the migration).
TRACKED_STORES = [
    {"path": "~/.tokenpak/telemetry.db", "purpose": "User-facing telemetry counters"},
    {"path": "~/.tokenpak/spend_guard.db", "purpose": "TIP Spend Guard audit log per Std 29 §7"},
]


def collect_ddl(db_path: Path) -> dict | None:
    if not db_path.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
            rows = con.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index', 'view') "
                "AND name NOT LIKE 'sqlite_%' "
                "ORDER BY type, name"
            ).fetchall()
        return {"objects": [{"type": t, "name": n, "sql": (s or "").strip()} for t, n, s in rows]}
    except sqlite3.Error as e:
        return {"error": f"{type(e).__name__}: {e}"}


def build_snapshot() -> dict:
    stores = []
    for spec in TRACKED_STORES:
        path = Path(spec["path"]).expanduser()
        ddl = collect_ddl(path)
        stores.append(
            {
                "path": spec["path"],
                "purpose": spec["purpose"],
                "exists": path.is_file(),
                "ddl": ddl,
            }
        )
    return {
        "version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stores": stores,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate telemetry-schema snapshot")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    snapshot = build_snapshot()
    body = json.dumps(snapshot, indent=2) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.check:
        if not args.out.exists():
            print(f"telemetry-schema.json missing at {args.out}", file=sys.stderr)
            return 1
        # Compare DDL only (ignore generated_at)
        try:
            on_disk = json.loads(args.out.read_text())
        except Exception as e:
            print(f"on-disk snapshot is not valid JSON: {e}", file=sys.stderr)
            return 1

        def fingerprint(snap):
            return [
                {
                    "path": s["path"],
                    "ddl": s.get("ddl"),
                }
                for s in snap.get("stores", [])
            ]

        if fingerprint(on_disk) != fingerprint(snapshot):
            print("telemetry-schema snapshot drift detected", file=sys.stderr)
            print(
                "If intentional: ship a migration test in the same PR per Std 10 §E8 + §E9",
                file=sys.stderr,
            )
            print(
                "and run `make telemetry-snapshot` to update the on-disk snapshot.", file=sys.stderr
            )
            return 1
        print("telemetry-schema snapshot matches on-disk", file=sys.stderr)
        return 0

    args.out.write_text(body)
    print(f"telemetry-schema snapshot written: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
