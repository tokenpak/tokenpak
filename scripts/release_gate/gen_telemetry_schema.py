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
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# Mark this process as a snapshot-generation run before any tokenpak module
# is imported, so library first-run side effects (e.g. RBAC admin bootstrap)
# are skipped and cannot pollute deterministic snapshot output.
os.environ.setdefault("TOKENPAK_SNAPSHOT_GEN", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "tokenpak" / "_snapshots" / "telemetry-schema.json"

# User-facing SQLite stores tracked by Std 30 §7. ``path`` is the documented
# user-facing location; ``materializer`` names the product initializer used to
# create the current schema in an isolated temporary directory. Snapshot
# generation must never inspect the operator's ambient home databases.
TRACKED_STORES = [
    {
        "path": "~/.tpk/telemetry.db",
        "purpose": "User-facing telemetry counters",
        "materializer": "telemetry",
    },
    {
        "path": "~/.tokenpak/spend_guard.db",
        "purpose": "TIP Spend Guard audit and pending-request log",
        "materializer": "spend_guard",
    },
    {
        "path": "~/.tpk/monitor.db",
        "purpose": "Proxy request, cost, savings, and timing ledger",
        "materializer": "monitor",
    },
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


def _materialize_telemetry(db_path: Path) -> None:
    from tokenpak.telemetry.storage import TelemetryDB

    store = TelemetryDB(db_path)
    store.close()


def _materialize_spend_guard(db_path: Path) -> None:
    from tokenpak.proxy.spend_guard.audit import query_recent
    from tokenpak.proxy.spend_guard.pending import (
        PendingStore,
        reset_schema_cache_for_testing,
    )
    from tokenpak.proxy.spend_guard.reservation import _schema as reservation_schema

    # Components share one store and own distinct schema objects. Materialize
    # each schema, including opt-in reservations, without inserting request data.
    reset_schema_cache_for_testing()
    try:
        PendingStore(str(db_path)).get_by_session("__snapshot__")
        query_recent(str(db_path), limit=1)
        with sqlite3.connect(db_path) as conn:
            reservation_schema(conn)
    finally:
        reset_schema_cache_for_testing()


def _materialize_monitor(db_path: Path) -> None:
    from tokenpak.companion.stream import _ensure_events_table
    from tokenpak.proxy.monitor import Monitor

    monitor = Monitor(db_path=str(db_path))
    if not monitor.stop(timeout=5.0):
        raise RuntimeError("monitor snapshot writer did not stop cleanly")

    # monitor.db has more than one schema owner. The proxy Monitor initializes
    # the request ledger, while the companion stream guard owns the additive
    # provider-event ledger in the same canonical store.
    with sqlite3.connect(db_path) as conn:
        _ensure_events_table(conn)


_MATERIALIZERS = {
    "telemetry": _materialize_telemetry,
    "spend_guard": _materialize_spend_guard,
    "monitor": _materialize_monitor,
}


def build_snapshot() -> dict:
    stores = []
    with tempfile.TemporaryDirectory(prefix="tokenpak-schema-snapshot-") as temp_dir:
        root = Path(temp_dir)
        for index, spec in enumerate(TRACKED_STORES):
            db_path = root / f"{index}-{Path(spec['path']).name}"
            _MATERIALIZERS[spec["materializer"]](db_path)
            ddl = collect_ddl(db_path)
            if ddl is None or "error" in ddl:
                raise RuntimeError(f"failed to materialize {spec['path']}: {ddl}")
            stores.append(
                {
                    "path": spec["path"],
                    "purpose": spec["purpose"],
                    "exists": True,
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
