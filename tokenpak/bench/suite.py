# SPDX-License-Identifier: Apache-2.0
"""Suite orchestrator for `tokenpak bench`.

Implements `--quick` per Standard 24 §5.1. Loads + verifies the manifest,
runs each metric runner, appends every metric to history.jsonl, and emits
a human-readable summary (and optional JSON).

Per Standard 24 §9, this runner does NOT enforce gates yet — the standard
is draft. CI may surface warnings, but merge/release-blocking gating is
deferred until Standard 24 is ratified.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Any

from .history import append_record, make_run_context
from .manifest import load_and_verify
from .runners import byte_fidelity, cold_start, compression, latency, tip_conformance


@dataclass
class QuickReport:
    suite_version: str
    tokenpak_version: str
    tokenpak_commit: str
    host: str
    run_id: str
    duration_ms: float
    records: list[dict[str, Any]]
    summary_lines: list[str]


def _emit(line: str, *, quiet: bool) -> None:
    if not quiet:
        print(line)


def run_quick(*, quiet: bool = False, json_output: bool = False) -> QuickReport:
    """Run the `--quick` tier and append every metric to history.jsonl.

    Returns a QuickReport object usable by the CLI for human/JSON formatting.
    """
    t_run_start = time.perf_counter()
    fixtures = load_and_verify()
    ctx = make_run_context(suite_version=fixtures.suite_version, tier="quick")

    summary_lines: list[str] = []
    records: list[dict[str, Any]] = []

    _emit(f"\nbench-suite-v{fixtures.suite_version}  tokenpak {ctx['tokenpak_version']} ({ctx['tokenpak_commit']})", quiet=json_output)
    _emit(f"host={ctx['host']}  run_id={ctx['run_id']}  tier=quick\n", quiet=json_output)

    # ─── V1/V2/V3 compression ────────────────────────────────────────────
    _emit("─── compression (V1, V2, V3) ───", quiet=json_output)
    for r in compression.run():
        rec = append_record(
            ctx,
            metric_id=r.metric_id,
            metric_name=r.metric_name,
            fixture=r.fixture,
            value=r.reduction_pct,
            unit="%",
            duration_ms=r.duration_ms,
            extra={"tokens_in": r.tokens_in, "tokens_out": r.tokens_out},
        )
        records.append(rec.__dict__)
        line = f"  {r.metric_id}  {r.fixture:<26} {r.reduction_pct:>6.2f}%  ({r.tokens_in:>5} → {r.tokens_out:<5} tokens)  {r.duration_ms:>5.0f}ms"
        summary_lines.append(line)
        _emit(line, quiet=json_output)

    # ─── V8/V9 latency (mock upstream) ───────────────────────────────────
    _emit("\n─── latency (V8, V9) — mock upstream baseline ───", quiet=json_output)
    for r in latency.run():
        value = r.p50_ms if r.metric_id == "V8" else r.p95_ms
        rec = append_record(
            ctx,
            metric_id=r.metric_id,
            metric_name=r.metric_name,
            fixture=None,
            value=value,
            unit="ms",
            duration_ms=r.duration_ms,
            extra={"samples": r.samples, "note": r.note},
        )
        records.append(rec.__dict__)
        line = f"  {r.metric_id}  {r.metric_name:<32} {value:>6.2f}ms  ({r.samples} samples)  [{r.note}]"
        summary_lines.append(line)
        _emit(line, quiet=json_output)

    # ─── C1 byte-fidelity ────────────────────────────────────────────────
    _emit("\n─── byte fidelity (C1) ───", quiet=json_output)
    bf = byte_fidelity.run()
    rec = append_record(
        ctx,
        metric_id=bf.metric_id,
        metric_name=bf.metric_name,
        fixture=bf.fixture,
        value=bf.pass_pct,
        unit="%",
        duration_ms=bf.duration_ms,
        extra={"pass_count": bf.pass_count, "total_count": bf.total_count, "failures": bf.failures[:5]},
    )
    records.append(rec.__dict__)
    line = f"  C1  {bf.fixture:<26} {bf.pass_pct:>6.2f}%  ({bf.pass_count}/{bf.total_count} entries)  {bf.duration_ms:>5.0f}ms"
    summary_lines.append(line)
    _emit(line, quiet=json_output)
    if bf.failures:
        _emit(f"      first failure: {bf.failures[0]}", quiet=json_output)

    # ─── C2 TIP runtime conformance ──────────────────────────────────────
    _emit("\n─── TIP runtime conformance (C2) ───", quiet=json_output)
    tc = tip_conformance.run()
    rec = append_record(
        ctx,
        metric_id=tc.metric_id,
        metric_name=tc.metric_name,
        fixture=None,
        value=1.0 if tc.passed else 0.0,
        unit="bool",
        duration_ms=tc.duration_ms,
        extra={
            "pass_count": tc.pass_count,
            "fail_count": tc.fail_count,
            "error_count": tc.error_count,
            "total_count": tc.total_count,
            "failures": tc.failure_summary,
        },
    )
    records.append(rec.__dict__)
    status = "PASS" if tc.passed else f"FAIL ({tc.fail_count}f/{tc.error_count}e)"
    line = f"  C2  conformance ({tc.pass_count}/{tc.total_count} checks)      {status:<14}    {tc.duration_ms:>5.0f}ms"
    summary_lines.append(line)
    _emit(line, quiet=json_output)
    for f in tc.failure_summary[:3]:
        _emit(f"      {f}", quiet=json_output)

    # ─── O3 cold start ───────────────────────────────────────────────────
    _emit("\n─── cold start (O3) ───", quiet=json_output)
    cs = cold_start.run()
    rec = append_record(
        ctx,
        metric_id=cs.metric_id,
        metric_name=cs.metric_name,
        fixture=None,
        value=cs.median_ms,
        unit="ms",
        duration_ms=cs.duration_ms,
        extra={"samples_ms": cs.samples_ms},
    )
    records.append(rec.__dict__)
    line = f"  O3  cold_start_import_median       {cs.median_ms:>6.0f}ms  ({len(cs.samples_ms)} spawns)  {cs.duration_ms:>5.0f}ms"
    summary_lines.append(line)
    _emit(line, quiet=json_output)

    total_ms = (time.perf_counter() - t_run_start) * 1000
    _emit(f"\n─── total: {total_ms / 1000:.2f}s   {len(records)} records appended to history.jsonl ───", quiet=json_output)

    return QuickReport(
        suite_version=fixtures.suite_version,
        tokenpak_version=ctx["tokenpak_version"],
        tokenpak_commit=ctx["tokenpak_commit"],
        host=ctx["host"],
        run_id=ctx["run_id"],
        duration_ms=total_ms,
        records=records,
        summary_lines=summary_lines,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m tokenpak.bench`."""
    import argparse

    p = argparse.ArgumentParser(prog="tokenpak.bench")
    p.add_argument("--quick", action="store_true", help="Run the quick tier (default)")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")
    args = p.parse_args(argv)

    report = run_quick(json_output=args.json)
    if args.json:
        sys.stdout.write(json.dumps({
            "suite_version": report.suite_version,
            "tokenpak_version": report.tokenpak_version,
            "tokenpak_commit": report.tokenpak_commit,
            "host": report.host,
            "run_id": report.run_id,
            "duration_ms": report.duration_ms,
            "records": report.records,
        }, indent=2))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
