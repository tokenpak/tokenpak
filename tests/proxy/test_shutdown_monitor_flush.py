"""
Regression: the shutdown telemetry-flush step must drain the monitor queue.

The monitor's DB writer is a daemon thread; on a clean process exit it is
killed abruptly, so any request rows still queued are lost (recorded spend <
real spend, which under-fires the rolling caps). ProxyServer._flush_telemetry()
must call monitor.flush() so the async write queue is drained before exit — it
previously flushed only the compression-stats sink.

The monitor drain is ordered BEFORE the compression-stats flush, so a failure
in that (out-of-scope) sink cannot skip the drain; this test pins that too.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.server import ProxyServer


def _make_server() -> ProxyServer:
    # Port is never bound — start() is not called in this test.
    return ProxyServer(host="127.0.0.1", port=18991)


def _row_count(db_path, table: str = "requests") -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_flush_telemetry_drains_queued_monitor_rows(tmp_path):
    db = tmp_path / "monitor.db"
    ps = _make_server()
    # Point the server's monitor at a scratch DB we can count.
    ps.monitor = Monitor(db_path=str(db))

    # The compression-stats shutdown sink is out of scope here; simulate it
    # FAILING so this test also proves the monitor drain runs regardless of a
    # downstream sink error (the drain is ordered first).
    ps.compression_stats = MagicMock()
    ps.compression_stats.flush_shutdown_record.side_effect = RuntimeError(
        "compression sink unavailable"
    )

    # Spy on flush so the regression fails deterministically if the shutdown
    # path ever stops draining the monitor (not just when rows happen to race).
    flush_calls: list[bool] = []
    real_flush = ps.monitor.flush

    def _spy_flush(*args, **kwargs):
        flush_calls.append(True)
        return real_flush(*args, **kwargs)

    ps.monitor.flush = _spy_flush  # type: ignore[method-assign]

    n = 12
    for _ in range(n):
        ps.monitor.log(
            model="claude-sonnet-4-6",
            input_tokens=1,
            output_tokens=1,
            cost=0.0,
            latency_ms=0.0,
            status_code=200,
            endpoint="chat",
        )

    try:
        # A failing compression sink must NOT prevent the monitor drain.
        try:
            ps._flush_telemetry()
        except RuntimeError:
            pass  # out-of-scope downstream compression-sink error

        # (1) the stop path drained the monitor queue (T-3 wiring), and
        # (2) every queued row is committed to disk afterwards.
        assert flush_calls, "_flush_telemetry must drain the monitor write queue (T-3)"
        assert _row_count(db) == n
    finally:
        # Stop the module-level writer thread so later tests start clean.
        ps.monitor.stop(timeout=5.0)
