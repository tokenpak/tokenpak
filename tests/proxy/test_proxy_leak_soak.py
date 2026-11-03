"""Soak: the proxy's threads, monitor.db descriptors and memory stay bounded.

Drives the default ``tests/proxy/_leak_soak.py`` mix through a real proxy
process and a local fake upstream (no network): 2,000 Claude Code-shaped
requests from 16 concurrent client threads (streaming and non-streaming,
5% with 1-3 MB bodies, 10% of clients disconnecting mid-stream), four
companion status lines polling session economics every two seconds with a
0.5 s timeout, and one open dashboard tab.

Bounds, measured on the proxy's own process:

- ``monitor.db`` descriptors. While the WAL writer holds its connection open,
  SQLite keeps the descriptors of closed connections for reuse, so the count
  is the most connections ever open at once. It must stay at or below
  ``MONITOR_DB_FD_BOUND`` (two per concurrent client plus the writer and a
  few background readers) and must not grow with request count: the count at
  the end of the run is within ``MONITOR_DB_FD_GROWTH`` of the count halfway.
- Live threads return to within ``THREAD_SLACK`` of the pre-load baseline
  within ``DRAIN_TIMEOUT_S`` of the load ending.
- RSS ends at most ``RSS_GROWTH_BOUND_MB`` above the pre-load baseline.

Opt-in (``needs_proxy``, ``slow``): the run takes several minutes of CPU.
The measured numbers are printed (visible with ``-s``) and, when
``TOKENPAK_SOAK_REPORT`` names a file, written there as JSON.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.proxy._leak_soak import SoakConfig, run_soak

pytestmark = [pytest.mark.needs_proxy, pytest.mark.slow, pytest.mark.timeout(2400)]

CLIENTS = 16 + 4 + 1  # model clients, status lines, dashboard tab
MONITOR_DB_FD_BOUND = 2 * CLIENTS + 8
MONITOR_DB_FD_GROWTH = 4
THREAD_SLACK = 4
DRAIN_TIMEOUT_S = 90.0
RSS_GROWTH_BOUND_MB = 600.0


def test_soak_keeps_threads_descriptors_and_memory_bounded() -> None:
    result = run_soak(SoakConfig(drain_timeout=DRAIN_TIMEOUT_S, thread_slack=THREAD_SLACK))
    summary = json.dumps(result.summary(), indent=1)
    print(summary)
    report = os.environ.get("TOKENPAK_SOAK_REPORT", "")
    if report:
        Path(report).write_text(summary, encoding="utf-8")
    assert result.final is not None and result.end_of_load is not None, summary

    assert not result.load_timed_out, f"load did not finish in time\n{summary}"
    errors = {k: v for k, v in result.outcomes.items() if k.startswith("error_")}
    assert not errors, f"client errors {errors}\n{summary}"

    assert result.peak_db_fds <= MONITOR_DB_FD_BOUND, summary
    halfway = result.checkpoints[1].db_fds
    assert result.end_of_load.db_fds <= halfway + MONITOR_DB_FD_GROWTH, summary
    assert result.final.db_fds <= MONITOR_DB_FD_BOUND, summary

    assert result.final.threads <= result.baseline.threads + THREAD_SLACK, summary
    assert result.final.rss_mb - result.baseline.rss_mb <= RSS_GROWTH_BOUND_MB, summary
