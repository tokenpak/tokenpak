"""Scale: session economics stays cheap on a production-size ledger.

Drives ``tests/proxy/_economics_ledger_scale.py``: a 150,000-row ledger of
completed Claude Code-shaped sessions, then 200 session-economics calls, each
made right after a ledger write (a status line polling while its session
works). Every call used to re-read and re-group the whole ledger for the
calibration corpus, because each write changed the corpus cache key.

Bounds, on the current tree:

- the median wall time of the calls after the first is at most
  ``MEDIAN_RATIO_BOUND`` of the first call, which parses the full ledger as
  every call used to;
- the ``tracemalloc`` peak of every call after the first stays below
  ``PEAK_BOUND_MB``;
- RSS grows by less than ``RSS_GROWTH_BOUND_MB`` from the first call to the
  last (untraced run);
- every call returns a calibrated ``available`` forecast, so the corpus path
  ran, and the traced and untraced runs return identical payloads.

When ``TOKENPAK_ECON_SCALE_BASE`` names another source tree (for example a
``git archive`` export of the commit before the incremental corpus), that
tree runs the same ledger and write plan, and the test also requires
byte-identical payloads for every call and a median at most
``MEDIAN_RATIO_BOUND`` of that tree's median.

Opt-in (``slow``, ``needs_fast_host``): about 10 minutes of one core on the
current tree, and about 40 more with a base tree. The numbers are printed
(visible with ``-s``) and, when ``TOKENPAK_ECON_SCALE_REPORT`` names a file,
written there as JSON.
"""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

import pytest

from tests.proxy._economics_ledger_scale import ScaleConfig, build_ledger, run_scale

pytestmark = [pytest.mark.slow, pytest.mark.needs_fast_host, pytest.mark.timeout(4 * 3600)]

ROWS = 150_000
CALLS = 200
MEDIAN_RATIO_BOUND = 0.10
PEAK_BOUND_MB = 5.0
RSS_GROWTH_BOUND_MB = 20.0


def test_session_economics_stays_cheap_on_a_large_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.db"
    build_ledger(ledger, rows=ROWS, seed=ScaleConfig.seed)
    timed = run_scale(ScaleConfig(rows=ROWS, calls=CALLS, ledger=str(ledger)))
    traced = run_scale(ScaleConfig(rows=ROWS, calls=CALLS, ledger=str(ledger), trace=True))
    summary: dict[str, object] = {"timed": timed.summary(), "traced": traced.summary()}
    base_source = os.environ.get("TOKENPAK_ECON_SCALE_BASE", "")
    base = None
    if base_source:
        base = run_scale(
            ScaleConfig(source=base_source, rows=ROWS, calls=CALLS, ledger=str(ledger))
        )
        summary["base"] = base.summary()
    text = json.dumps(summary, indent=1)
    print(text)
    report = os.environ.get("TOKENPAK_ECON_SCALE_REPORT", "")
    if report:
        Path(report).write_text(text, encoding="utf-8")

    assert len(timed.warm()) == CALLS, text
    assert all(s.forecast == "available" for s in timed.samples), text
    assert [s.sha256 for s in traced.samples] == [s.sha256 for s in timed.samples], text
    median_ms = statistics.median(s.wall_ms for s in timed.warm())
    assert median_ms <= MEDIAN_RATIO_BOUND * timed.samples[0].wall_ms, text
    assert max(s.peak_mb or 0.0 for s in traced.warm()) < PEAK_BOUND_MB, text
    assert timed.rss_growth_mb() < RSS_GROWTH_BOUND_MB, text
    if base is not None:
        assert [s.sha256 for s in base.samples] == [s.sha256 for s in timed.samples], text
        base_median_ms = statistics.median(s.wall_ms for s in base.warm())
        assert median_ms <= MEDIAN_RATIO_BOUND * base_median_ms, text
