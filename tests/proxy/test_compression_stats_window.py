"""CompressionStats rolling-sample bound.

The per-request ratio/latency samples previously grew unbounded (one list
entry per request for the life of the process); ROLLING_WINDOW existed but
was never applied. Pin the bound and that totals keep counting past it.
"""

from __future__ import annotations

from tokenpak.proxy.stats import ROLLING_WINDOW, CompressionStats


def test_samples_bounded_by_rolling_window(tmp_path):
    stats = CompressionStats(log_path=tmp_path / "compression.log")
    n = ROLLING_WINDOW + 250
    for i in range(n):
        stats.record_compression(
            model="m", tokens_in=100, tokens_out=10, ratio=0.5, latency_ms=i, status="ok"
        )

    assert len(stats._ratios) == ROLLING_WINDOW
    assert len(stats._latencies) == ROLLING_WINDOW
    # Window keeps the most recent samples.
    assert stats._latencies[-1] == n - 1
    assert stats._latencies[0] == n - ROLLING_WINDOW

    snapshot = stats.get_stats()
    assert snapshot["requests_total"] == n  # totals unaffected by the window
    assert snapshot["avg_ratio"] == 0.5
