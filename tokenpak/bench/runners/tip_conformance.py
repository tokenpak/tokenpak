# SPDX-License-Identifier: Apache-2.0
"""C2 — TIP runtime conformance smoke.

Calls `tokenpak.services.diagnostics.conformance.run_conformance_checks()`
in-process. The same runner backs `tokenpak doctor --conformance`.

Records pass/fail and a count summary.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class TipConformanceResult:
    metric_id: str
    metric_name: str
    passed: bool
    pass_count: int
    fail_count: int
    error_count: int
    total_count: int
    duration_ms: float
    failure_summary: list[str]


def run() -> TipConformanceResult:
    t0 = time.perf_counter()
    try:
        from tokenpak.services.diagnostics.conformance import (
            exit_code_for,
            run_conformance_checks,
            summarize,
        )

        results = run_conformance_checks()
        counts = summarize(results)
        code = exit_code_for(results)
        passed = code == 0

        failure_summary: list[str] = []
        for r in results:
            status = getattr(r.status, "value", str(r.status))
            if status not in ("ok", "OK"):
                failure_summary.append(f"{getattr(r, 'name', '?')}: {status} — {getattr(r, 'summary', '')}")

        return TipConformanceResult(
            metric_id="C2",
            metric_name="tip_runtime_conformance_pass",
            passed=passed,
            pass_count=int(counts.get("ok", 0)),
            fail_count=int(counts.get("fail", 0)),
            error_count=int(counts.get("warn", 0)),
            total_count=int(sum(counts.values())) if isinstance(counts, dict) else len(results),
            duration_ms=(time.perf_counter() - t0) * 1000,
            failure_summary=failure_summary[:5],
        )
    except Exception as e:
        return TipConformanceResult(
            metric_id="C2",
            metric_name="tip_runtime_conformance_pass",
            passed=False,
            pass_count=0,
            fail_count=0,
            error_count=1,
            total_count=0,
            duration_ms=(time.perf_counter() - t0) * 1000,
            failure_summary=[f"runner-error: {type(e).__name__}: {e}"],
        )
