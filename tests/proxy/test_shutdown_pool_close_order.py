"""
Tests for shutdown ordering: the connection pool must close AFTER the drain

ProxyServer.stop() previously closed the connection pool first ("always
close the pool, even if server wasn't started") and only then began the
drain — so a SIGTERM killed every in-flight request's upstream connection
and then "drained" the corpses. These tests pin the corrected order:

- running server: shutdown.begin() and wait_for_drain() happen BEFORE
  pool.close(); an in-flight request observes the pool still open while
  it completes during the drain window
- never-started server: stop() still closes the pool (resource release)
  and skips the drain machinery entirely
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from tokenpak.proxy.connection_pool import ConnectionPool, PoolConfig
from tokenpak.proxy.server import ProxyServer


def _make_server(**kwargs) -> ProxyServer:
    # Port is never bound — start() is not called in these tests.
    return ProxyServer(host="127.0.0.1", port=18999, **kwargs)


def _record_calls(ps: ProxyServer, order: list) -> None:
    """Wrap the stop()-relevant collaborators with call-order recorders."""
    real_begin = ps.shutdown.begin
    real_drain = ps.shutdown.wait_for_drain
    real_close = ps._connection_pool.close

    def begin():
        order.append("shutdown_begin")
        return real_begin()

    def drain(timeout=None):
        order.append("drain")
        return real_drain(timeout=timeout)

    def close():
        order.append("pool_close")
        return real_close()

    ps.shutdown.begin = begin  # type: ignore[method-assign]
    ps.shutdown.wait_for_drain = drain  # type: ignore[method-assign]
    ps._connection_pool.close = close  # type: ignore[method-assign]


def test_stop_closes_pool_only_after_drain():
    ps = _make_server(shutdown_timeout=2.0)
    order: list = []
    _record_calls(ps, order)
    ps._server = MagicMock()  # simulate a running server

    ps.stop()

    assert "pool_close" in order and "drain" in order
    assert order.index("shutdown_begin") < order.index("drain")
    assert order.index("drain") < order.index("pool_close"), (
        f"pool must close after the drain, got order {order}"
    )
    assert order.count("pool_close") == 1
    ps._server = None


def test_stop_with_inflight_request_drains_before_pool_close():
    """An in-flight request must see the pool open until it completes."""
    ps = _make_server(shutdown_timeout=5.0)
    order: list = []
    _record_calls(ps, order)
    ps._server = MagicMock()

    started = threading.Event()
    release = threading.Event()
    pool_closed_during_request = []

    def inflight():
        with ps.shutdown.track_request():
            started.set()
            release.wait(timeout=5)
            # Would the pool have served this request? Before the fix,
            # stop() had already closed it by now.
            pool_closed_during_request.append("pool_close" in order)

    t = threading.Thread(target=inflight)
    t.start()
    started.wait(timeout=2)

    stopper = threading.Thread(target=ps.stop)
    stopper.start()
    # Give stop() time to reach the drain wait, then let the request finish.
    time.sleep(0.3)
    release.set()
    t.join(timeout=5)
    stopper.join(timeout=10)

    assert pool_closed_during_request == [False], (
        "pool was closed while a request was still in flight"
    )
    assert order.index("drain") < order.index("pool_close")
    ps._server = None


def test_stop_never_started_closes_pool_and_skips_drain():
    ps = _make_server()
    order: list = []
    _record_calls(ps, order)
    assert ps._server is None

    ps.stop()

    assert order == ["pool_close"], (
        f"never-started stop() should only release the pool, got {order}"
    )


def test_stop_twice_is_safe():
    ps = _make_server(shutdown_timeout=1.0)
    order: list = []
    _record_calls(ps, order)
    ps._server = MagicMock()

    ps.stop()
    ps.stop()  # second call: _server is None → pool close only

    assert order.count("drain") == 1
    assert order.count("pool_close") == 2


def test_stop_reaches_server_shutdown_when_pool_client_close_hangs():
    """A wedged transport close cannot consume the server-stop step."""
    ps = _make_server(shutdown_timeout=0.1)
    server = MagicMock()
    ps._server = server
    ps._flush_telemetry = MagicMock()  # type: ignore[method-assign]
    ps._connection_pool = ConnectionPool(PoolConfig(http2=False, close_timeout_seconds=0.05))
    close_started = threading.Event()
    release_close = threading.Event()
    close_finished = threading.Event()

    class _BlockingCloseClient:
        def close(self):
            close_started.set()
            release_close.wait()
            close_finished.set()

    ps._connection_pool._clients["upstream.test"] = _BlockingCloseClient()  # type: ignore[assignment]

    try:
        started = time.monotonic()
        ps.stop()
        elapsed = time.monotonic() - started

        assert close_started.is_set()
        assert elapsed < 0.5
        server.shutdown.assert_called_once_with()
        assert ps._server is None
    finally:
        release_close.set()
        assert close_finished.wait(timeout=1.0)
