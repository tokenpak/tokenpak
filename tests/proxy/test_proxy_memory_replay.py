"""Replay: proxy memory and monitor.db descriptors stay flat under large bodies.

Drives ``tests/proxy/_memory_replay.py`` through a real proxy process and a
local fake upstream (no network): 1,500 Claude Code-shaped ``/v1/messages``
requests whose bodies grow from 0.5 to 2.5 MB across 120 sessions, about 16
open at a time, with a session-economics call every 10 requests and a
dashboard read every 100. One client sends them in order.

Bounds, measured on the proxy's own process:

- RSS grows by less than ``RSS_GROWTH_BOUND_MB`` from the end of the
  200-request warm-up to the end of the run;
- ``monitor.db`` descriptors stay at or below ``MONITOR_DB_FD_BOUND`` at every
  sample (the ledger writer plus one descriptor SQLite keeps for reuse by the
  next read-write connection; see ``test_monitor_db_descriptors.py``);
- every request succeeds and nothing tries to leave loopback.

Opt-in (``needs_proxy``, ``slow``): the run takes 40-80 minutes of one core.
The numbers are printed (visible with ``-s``) and, when
``TOKENPAK_REPLAY_REPORT`` names a file, written there as JSON.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.proxy._memory_replay import ReplayConfig, run_replay

pytestmark = [pytest.mark.needs_proxy, pytest.mark.slow, pytest.mark.timeout(3 * 3600)]

RSS_GROWTH_BOUND_MB = 20.0
MONITOR_DB_FD_BOUND = 2


def test_replay_keeps_rss_and_monitor_db_descriptors_flat() -> None:
    result = run_replay(ReplayConfig(trace=False))
    summary = result.summary()
    text = json.dumps(summary, indent=1)
    print(text)
    report = os.environ.get("TOKENPAK_REPLAY_REPORT", "")
    if report:
        Path(report).write_text(text, encoding="utf-8")

    failures = {k: v for k, v in result.outcomes.items() if not k.endswith("_200")}
    assert not failures, f"requests failed: {failures}\n{text}"
    assert result.net_denied == 0, text
    assert result.rss_growth_mb() < RSS_GROWTH_BOUND_MB, text
    assert result.peak_db_fds <= MONITOR_DB_FD_BOUND, text
    assert all(s.db_fds <= MONITOR_DB_FD_BOUND for s in result.samples), text
