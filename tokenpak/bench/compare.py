# SPDX-License-Identifier: Apache-2.0
"""Compare two benchmark runs from history.jsonl.

Per Standard 24 §7.3, comparison refuses to diff records across MAJOR
suite-version boundaries. This is the implementation of that rule.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from typing import Any

from .history import load_records


def _parse_suite_major(suite_version: str) -> int:
    return int(suite_version.split(".")[0])


def _select_run_records(records: list[dict[str, Any]], selector: str) -> list[dict[str, Any]]:
    """Selector matches by tokenpak_version exact, run_id prefix, or 'latest' (per host)."""
    if selector == "latest":
        # latest run_id by ts
        if not records:
            return []
        latest_ts = max(r["ts"] for r in records)
        latest_run = next(r["run_id"] for r in records if r["ts"] == latest_ts)
        return [r for r in records if r["run_id"] == latest_run]

    by_run_id = [r for r in records if r["run_id"].startswith(selector)]
    if by_run_id:
        # use the matched run_id
        run_id = by_run_id[0]["run_id"]
        return [r for r in records if r["run_id"] == run_id]

    by_version = [r for r in records if r["tokenpak_version"] == selector]
    if not by_version:
        return []
    # Use the latest run for that version.
    latest_ts = max(r["ts"] for r in by_version)
    latest_run = next(r["run_id"] for r in by_version if r["ts"] == latest_ts)
    return [r for r in records if r["run_id"] == latest_run]


def compare(a_selector: str, b_selector: str) -> int:
    records = load_records()
    a = _select_run_records(records, a_selector)
    b = _select_run_records(records, b_selector)

    if not a:
        print(f"compare: no records match {a_selector!r}", file=sys.stderr)
        return 2
    if not b:
        print(f"compare: no records match {b_selector!r}", file=sys.stderr)
        return 2

    a_suite = a[0]["suite_version"]
    b_suite = b[0]["suite_version"]
    if _parse_suite_major(a_suite) != _parse_suite_major(b_suite):
        print(
            f"compare: refusing to diff across suite MAJOR boundary "
            f"({a_suite} vs {b_suite}). See Standard 24 §7.3.",
            file=sys.stderr,
        )
        return 3

    a_idx = {r["metric_id"] + "|" + (r["fixture"] or ""): r for r in a}
    b_idx = {r["metric_id"] + "|" + (r["fixture"] or ""): r for r in b}
    keys = sorted(set(a_idx) | set(b_idx))

    a_label = f"{a[0]['tokenpak_version']} ({a[0]['tokenpak_commit']})"
    b_label = f"{b[0]['tokenpak_version']} ({b[0]['tokenpak_commit']})"
    print(f"\nbench-suite-v{a_suite}")
    print(f"  A: {a_label}  run_id={a[0]['run_id'][:8]}  host={a[0]['host']}")
    print(f"  B: {b_label}  run_id={b[0]['run_id'][:8]}  host={b[0]['host']}\n")

    print(f"  {'metric':<32} {'A':>14} {'B':>14} {'Δ':>10} {'unit':>6}")
    print("  " + "─" * 80)
    for k in keys:
        ar = a_idx.get(k)
        br = b_idx.get(k)
        metric_id, fixture = k.split("|", 1)
        label = f"{metric_id} {fixture}".strip()
        if ar and br:
            delta = br["value"] - ar["value"]
            print(
                f"  {label:<32} {ar['value']:>14.3f} {br['value']:>14.3f} {delta:>+10.3f} {ar['unit']:>6}"
            )
        elif ar:
            print(f"  {label:<32} {ar['value']:>14.3f} {'(missing)':>14} {'-':>10} {ar['unit']:>6}")
        else:
            print(f"  {label:<32} {'(missing)':>14} {br['value']:>14.3f} {'-':>10} {br['unit']:>6}")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tokenpak.bench.compare")
    p.add_argument("a", help="run-id prefix, tokenpak_version, or 'latest'")
    p.add_argument("b", help="run-id prefix, tokenpak_version, or 'latest'")
    args = p.parse_args(argv)
    return compare(args.a, args.b)


if __name__ == "__main__":
    sys.exit(main())
