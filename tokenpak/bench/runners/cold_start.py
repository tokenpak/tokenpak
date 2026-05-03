# SPDX-License-Identifier: Apache-2.0
"""O3 — cold-start time: spawn a fresh interpreter, import tokenpak, exit.

Median of 5 spawns. Each spawn is a fresh process so cached bytecode and
module objects from the test runner don't bias the number.
"""
from __future__ import annotations

import statistics
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ColdStartResult:
    metric_id: str
    metric_name: str
    median_ms: float
    samples_ms: list[float]
    duration_ms: float


def run(*, samples: int = 3) -> ColdStartResult:
    times: list[float] = []
    t_total_start = time.perf_counter()
    for _ in range(samples):
        t0 = time.perf_counter()
        subprocess.run(
            [sys.executable, "-c", "import tokenpak"],
            check=True,
            capture_output=True,
            timeout=10.0,
        )
        times.append((time.perf_counter() - t0) * 1000)
    total_ms = (time.perf_counter() - t_total_start) * 1000
    return ColdStartResult(
        metric_id="O3",
        metric_name="cold_start_import_ms",
        median_ms=statistics.median(times),
        samples_ms=times,
        duration_ms=total_ms,
    )
