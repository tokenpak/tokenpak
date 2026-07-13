"""
Tests for connection-pool client eviction on transport errors

Covers:
- request() transport error evicts the netloc client (retry gets a fresh one)
- retry loop recovers after eviction (the incident regression case)
- session-keyed client eviction
- eviction disabled via config keeps the failed client pooled
- PoolTimeout is excluded from eviction (local saturation, not a dead link)
- stale client reference never evicts the replacement client
- evicted clients are retired (kept open for in-flight grace), then closed
- streaming: connect/send failure and mid-stream failure both evict
- downstream (non-httpx) errors in the streaming body do not evict
- from_env() parses the new timeout/eviction env vars
- metrics expose evicted_clients and retired_pending_close
"""

from __future__ import annotations

import os
import threading
import time
from typing import Iterator
from unittest.mock import patch

import httpx
import pytest

from tokenpak.proxy.connection_pool import ConnectionPool, PoolConfig

URL = "http://upstream.test/v1/messages"
NETLOC = "upstream.test"


class _FlakyTransport(httpx.BaseTransport):
    """Raises a transport error for the first *fail_times* requests, then succeeds."""

    def __init__(self, fail_times: int = 1, exc_factory=None):
        self.calls = 0
        self.fail_times = fail_times
        self.exc_factory = exc_factory or (
            lambda: httpx.ReadError("[Errno 104] Connection reset by peer")
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc_factory()
        return httpx.Response(200, json={"ok": True})


class _ExplodingStream(httpx.SyncByteStream):
    """Yields one chunk, then dies like a reset upstream connection."""

    def __iter__(self) -> Iterator[bytes]:
        yield b'data: {"type":"ping"}\n\n'
        raise httpx.ReadError("connection reset mid-stream")


class _MidStreamFailTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_ExplodingStream(),
            headers={"Content-Type": "text/event-stream"},
        )


def _pool_with_transport(transport: httpx.BaseTransport, **cfg_kwargs) -> ConnectionPool:
    """Pool whose clients all use *transport* (no real sockets)."""
    pool = ConnectionPool(PoolConfig(http2=False, **cfg_kwargs))
    pool._make_client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]
    return pool


def _wait_closed(pool: ConnectionPool, client: httpx.Client, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not client.is_closed and time.monotonic() < deadline:
        time.sleep(0.005)
    assert client.is_closed is True


def _wait_pending(pool: ConnectionPool, expected: int = 0, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while pool.metrics()["retired_pending_close"] != expected and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pool.metrics()["retired_pending_close"] == expected


# ---------------------------------------------------------------------------
# request() path
# ---------------------------------------------------------------------------


def test_request_transport_error_evicts_client():
    transport = _FlakyTransport(fail_times=1)
    pool = _pool_with_transport(transport)
    first = pool._get_client(NETLOC)

    with pytest.raises(httpx.ReadError):
        pool.request("POST", URL, content=b"{}")

    assert NETLOC not in pool._clients, "failed client should be evicted"
    second = pool._get_client(NETLOC)
    assert second is not first
    m = pool.metrics()
    assert m["evicted_clients"] == 1
    assert m["errors"] == 1
    pool.close()


def test_retry_loop_recovers_after_eviction():
    """The incident regression: a retry after a dead-connection failure must
    reach upstream on a fresh client instead of re-failing on the same one."""
    transport = _FlakyTransport(fail_times=1)
    pool = _pool_with_transport(transport)

    resp = None
    for _attempt in range(2):
        try:
            resp = pool.request("POST", URL, content=b"{}")
            break
        except httpx.TransportError:
            continue

    assert resp is not None and resp.status_code == 200
    assert transport.calls == 2
    assert pool.metrics()["evicted_clients"] == 1
    pool.close()


def test_session_client_eviction_and_recovery():
    transport = _FlakyTransport(fail_times=1)
    pool = _pool_with_transport(transport)
    first = pool._get_session_client(NETLOC, "sess-1")

    with pytest.raises(httpx.ReadError):
        pool.request("POST", URL, content=b"{}", session_key="sess-1")

    assert (NETLOC, "sess-1") not in pool._session_clients
    second = pool._get_session_client(NETLOC, "sess-1")
    assert second is not first

    resp = pool.request("POST", URL, content=b"{}", session_key="sess-1")
    assert resp.status_code == 200
    pool.close()


def test_eviction_disabled_via_config():
    transport = _FlakyTransport(fail_times=1)
    pool = _pool_with_transport(transport, evict_on_transport_error=False)
    first = pool._get_client(NETLOC)

    with pytest.raises(httpx.ReadError):
        pool.request("POST", URL, content=b"{}")

    assert pool._clients.get(NETLOC) is first, "client must stay pooled when disabled"
    assert pool.metrics()["evicted_clients"] == 0
    pool.close()


def test_pool_timeout_is_not_evicted():
    transport = _FlakyTransport(fail_times=1, exc_factory=lambda: httpx.PoolTimeout("busy"))
    pool = _pool_with_transport(transport)
    first = pool._get_client(NETLOC)

    with pytest.raises(httpx.PoolTimeout):
        pool.request("POST", URL, content=b"{}")

    assert pool._clients.get(NETLOC) is first
    assert pool.metrics()["evicted_clients"] == 0
    pool.close()


def test_stale_reference_does_not_evict_replacement():
    transport = _FlakyTransport(fail_times=0)
    pool = _pool_with_transport(transport)
    first = pool._get_client(NETLOC)

    assert pool._evict_client(NETLOC, None, first) is True
    replacement = pool._get_client(NETLOC)
    assert replacement is not first

    # A second eviction attempt with the stale reference must be a no-op.
    assert pool._evict_client(NETLOC, None, first) is False
    assert pool._clients.get(NETLOC) is replacement
    assert pool.metrics()["evicted_clients"] == 1
    pool.close()


def test_close_cannot_miss_concurrent_client_retirement(monkeypatch):
    """Regression: shutdown and eviction cannot strand a post-close retire."""
    pool = _pool_with_transport(
        _FlakyTransport(fail_times=0),
        retire_close_grace_seconds=3600.0,
    )
    client = pool._get_client(NETLOC)
    retire_entered = threading.Event()
    allow_retire = threading.Event()
    original_retire = pool._retire

    def paused_retire(retired_client):
        retire_entered.set()
        allow_retire.wait()
        original_retire(retired_client)

    monkeypatch.setattr(pool, "_retire", paused_retire)
    evicter = threading.Thread(
        target=pool._evict_client,
        args=(NETLOC, None, client),
        daemon=True,
    )
    closer = None
    try:
        evicter.start()
        assert retire_entered.wait(timeout=1.0)

        closer = threading.Thread(target=pool.close, daemon=True)
        closer.start()
        closer.join(timeout=0.05)
        allow_retire.set()
        evicter.join(timeout=1.0)
        closer.join(timeout=1.0)
    finally:
        allow_retire.set()
        evicter.join(timeout=1.0)
        if closer is not None:
            closer.join(timeout=1.0)

    assert evicter.is_alive() is False
    assert closer is not None
    assert closer.is_alive() is False
    assert client.is_closed is True
    assert pool.metrics()["retired_pending_close"] == 0


# ---------------------------------------------------------------------------
# Retire/close lifecycle
# ---------------------------------------------------------------------------


def test_zero_ref_shared_client_closes_without_waiting_for_grace():
    transport = _FlakyTransport(fail_times=1)
    pool = _pool_with_transport(transport, retire_close_grace_seconds=3600.0)
    first = pool._get_client(NETLOC)

    with pytest.raises(httpx.ReadError):
        pool.request("POST", URL, content=b"{}")

    _wait_closed(pool, first)
    assert pool.metrics()["retired_pending_close"] == 0
    pool.close()


def test_shared_client_with_active_lease_survives_reap_until_release():
    transport = _FlakyTransport(fail_times=0)
    pool = _pool_with_transport(transport, retire_close_grace_seconds=0.0)
    first = pool._get_client(NETLOC, checkout=True)

    assert pool._evict_client(NETLOC, None, first) is True
    pool._reap_retired()
    assert first.is_closed is False
    assert pool.metrics()["retired_pending_close"] == 1

    pool._release_client(first)
    _wait_closed(pool, first)
    assert pool.metrics()["retired_pending_close"] == 0
    pool.close()


# ---------------------------------------------------------------------------
# Streaming path
# ---------------------------------------------------------------------------


def test_streaming_connect_failure_evicts():
    transport = _FlakyTransport(fail_times=1)
    pool = _pool_with_transport(transport)
    first = pool._get_client(NETLOC)

    with pytest.raises(httpx.ReadError):
        with pool.stream("POST", URL, content=b"{}") as resp:
            resp.read()

    assert NETLOC not in pool._clients
    m = pool.metrics()
    assert m["evicted_clients"] == 1
    assert m["errors"] == 1
    assert pool._get_client(NETLOC) is not first
    pool.close()


def test_streaming_midstream_failure_evicts():
    pool = _pool_with_transport(_MidStreamFailTransport())
    first = pool._get_client(NETLOC)

    chunks = []
    with pytest.raises(httpx.ReadError):
        with pool.stream("POST", URL, content=b"{}") as resp:
            # chunk_size smaller than the first upstream chunk so iter_bytes
            # flushes it before the mid-stream failure surfaces.
            for chunk in resp.iter_bytes(chunk_size=8):
                chunks.append(chunk)

    assert chunks, "the pre-failure chunk should have been delivered"
    assert NETLOC not in pool._clients
    m = pool.metrics()
    assert m["evicted_clients"] == 1
    assert m["errors"] == 1
    assert pool._get_client(NETLOC) is not first
    pool.close()


def test_streaming_downstream_error_does_not_evict():
    """A non-httpx error inside the with-body (e.g. the downstream client
    hanging up on us) says nothing about upstream health — no eviction."""
    transport = _FlakyTransport(fail_times=0)
    pool = _pool_with_transport(transport)
    first = pool._get_client(NETLOC)

    with pytest.raises(BrokenPipeError):
        with pool.stream("POST", URL, content=b"{}"):
            raise BrokenPipeError("downstream client went away")

    assert pool._clients.get(NETLOC) is first
    m = pool.metrics()
    assert m["evicted_clients"] == 0
    assert m["errors"] == 0
    pool.close()


def test_non_session_entered_stream_survives_evict_reap_until_exit():
    """Shared clients take the same lease protection as session clients."""
    transport = _FlakyTransport(fail_times=0)
    pool = _pool_with_transport(transport, retire_close_grace_seconds=0.0)

    with pool.stream("POST", URL, content=b"{}") as response:
        held = pool._clients[NETLOC]
        assert pool._session_client_refs[held] == 1
        assert pool._evict_client(NETLOC, None, held) is True
        pool._reap_retired()
        assert held.is_closed is False
        assert pool.metrics()["retired_pending_close"] == 1
        response.read()

    _wait_closed(pool, held)
    _wait_pending(pool)
    pool.close()


# ---------------------------------------------------------------------------
# Session reaper / LRU disposal safety
# ---------------------------------------------------------------------------


def test_idle_reap_closes_zero_reference_client_promptly():
    """An idle client with no active lease must not wait for retire grace."""
    transport = _FlakyTransport(fail_times=0)
    pool = _pool_with_transport(
        transport, session_client_idle_seconds=0.0, retire_close_grace_seconds=3600.0
    )
    first = pool._get_session_client(NETLOC, "sess-old")

    # Any later checkout triggers the idle reap of sess-old.
    pool._get_session_client(NETLOC, "sess-new")

    assert (NETLOC, "sess-old") not in pool._session_clients
    _wait_closed(pool, first)
    assert pool.metrics()["retired_pending_close"] == 0
    pool.close()


def test_lru_eviction_closes_zero_reference_client_promptly():
    transport = _FlakyTransport(fail_times=0)
    pool = _pool_with_transport(transport, session_client_max=1, retire_close_grace_seconds=3600.0)
    first = pool._get_session_client(NETLOC, "sess-a")
    pool._get_session_client(NETLOC, "sess-b")  # cap 1 → LRU-evicts sess-a

    assert (NETLOC, "sess-a") not in pool._session_clients
    _wait_closed(pool, first)
    assert pool.metrics()["retired_pending_close"] == 0
    pool.close()


def test_blocking_idle_close_never_blocks_replacement_checkout():
    entered = threading.Event()
    release = threading.Event()

    class _BlockingCloseClient:
        is_closed = False

        def close(self):
            entered.set()
            release.wait(timeout=5.0)
            self.is_closed = True

    blocking = _BlockingCloseClient()
    replacement = httpx.Client(transport=_FlakyTransport(fail_times=0))
    clients = iter([blocking, replacement])
    pool = ConnectionPool(
        PoolConfig(http2=False, session_client_max=1, session_client_idle_seconds=0.0)
    )
    pool._make_client = lambda: next(clients)  # type: ignore[method-assign]

    pool._get_session_client(NETLOC, "old")
    started = time.monotonic()
    got = pool._get_session_client(NETLOC, "new")
    elapsed = time.monotonic() - started

    try:
        assert got is replacement
        assert entered.wait(timeout=1.0)
        assert elapsed < 0.1
        assert pool.metrics()["cleanup_in_progress"] == 1
    finally:
        release.set()
        _wait_pending(pool)
        pool.close()


def test_fixed_cleanup_workers_and_hard_slots_bound_blocked_close_churn():
    release = threading.Event()
    entered_lock = threading.Lock()
    entered = 0

    class _BlockingCloseClient:
        is_closed = False

        def close(self):
            nonlocal entered
            with entered_lock:
                entered += 1
            release.wait(timeout=5.0)
            self.is_closed = True

    pool = ConnectionPool(
        PoolConfig(http2=False, session_client_max=2, session_client_idle_seconds=0.0)
    )
    pool._make_client = _BlockingCloseClient  # type: ignore[method-assign]

    # Eight hard slots: one current client plus seven cleanup-owned clients.
    for index in range(8):
        pool._get_session_client(NETLOC, f"session-{index}")
    with pytest.raises(httpx.PoolTimeout):
        pool._get_session_client(NETLOC, "capacity-rejected")

    metrics = pool.metrics()
    assert metrics["client_slots_used"] == metrics["client_slots_max"] == 8
    assert metrics["retired_pending_close"] == 8
    assert metrics["cleanup_workers_alive"] == 4
    assert metrics["cleanup_in_progress"] <= 4
    assert metrics["client_capacity_rejections_total"] == 1
    assert metrics["cleanup_saturated"] is True

    release.set()
    _wait_pending(pool, timeout=2.0)
    replacement = pool._get_session_client(NETLOC, "capacity-restored")
    assert replacement is not None
    pool.close()


def test_cleanup_failure_remains_visible_and_retries_to_success():
    class _FailOnceCloseClient:
        is_closed = False
        attempts = 0

        def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transient close failure")
            self.is_closed = True

    failing = _FailOnceCloseClient()
    replacement = httpx.Client(transport=_FlakyTransport(fail_times=0))
    clients = iter([failing, replacement])
    pool = ConnectionPool(
        PoolConfig(http2=False, session_client_max=1, session_client_idle_seconds=0.0)
    )
    pool._make_client = lambda: next(clients)  # type: ignore[method-assign]

    pool._get_session_client(NETLOC, "old")
    pool._get_session_client(NETLOC, "new")
    _wait_closed(pool, failing)
    _wait_pending(pool)

    metrics = pool.metrics()
    assert failing.attempts == 2
    assert metrics["cleanup_failures_total"] == 1
    assert metrics["client_slots_used"] == 1
    pool.close()


def test_duplicate_cleanup_submission_closes_exactly_once():
    class _CountingCloseClient:
        is_closed = False
        closes = 0

        def close(self):
            self.closes += 1
            self.is_closed = True

    client = _CountingCloseClient()
    pool = ConnectionPool(PoolConfig(http2=False))
    assert pool._schedule_close(client) is True
    assert pool._schedule_close(client) is True
    _wait_pending(pool)
    assert client.closes == 1
    pool.close()


def test_health_metrics_recovers_pending_close_after_worker_start_failure(monkeypatch):
    real_start = threading.Thread.start
    failed_once = False

    def _fail_first_cleanup_start(thread):
        nonlocal failed_once
        if thread.name.startswith("tokenpak-connection-close-") and not failed_once:
            failed_once = True
            raise RuntimeError("transient thread capacity exhaustion")
        return real_start(thread)

    pool = _pool_with_transport(
        _FlakyTransport(fail_times=1),
        close_timeout_seconds=0.5,
    )
    client = pool._get_client(NETLOC)
    monkeypatch.setattr(threading.Thread, "start", _fail_first_cleanup_start)

    with pytest.raises(httpx.ReadError):
        pool.request("POST", URL, content=b"{}")

    # Inspect directly while starts are still failing: metrics() is itself a
    # normal-operation recovery kick and must be called only after capacity is
    # restored.
    with pool._close_cv:
        assert len(pool._close_pending) == 1
        assert pool._close_backlog
        assert not pool._close_workers

    monkeypatch.setattr(threading.Thread, "start", real_start)
    metrics = pool.metrics()
    assert metrics["cleanup_worker_start_failures_total"] == 1
    _wait_closed(pool, client)
    _wait_pending(pool)
    pool.close()


def test_dead_cleanup_worker_records_are_replaced():
    class _CountingCloseClient:
        is_closed = False
        closes = 0

        def close(self):
            self.closes += 1
            self.is_closed = True

    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join(timeout=1.0)
    assert dead.is_alive() is False

    pool = ConnectionPool(PoolConfig(http2=False))
    with pool._close_cv:
        pool._close_workers = [dead] * 4

    client = _CountingCloseClient()
    pool._schedule_close(client)
    _wait_pending(pool)
    assert client.closes == 1
    assert client.is_closed is True
    pool.close()


def test_final_release_cannot_stop_workers_before_close_handoff_completes():
    pool = _pool_with_transport(
        _FlakyTransport(fail_times=0),
        close_timeout_seconds=1.0,
    )
    client = pool._get_session_client(NETLOC, "leased", checkout=True)

    # Start idle cleanup workers so the regression covers the exact failure:
    # a final release used to tell these workers to exit before close() could
    # detach and enqueue the still-cached client.
    with pool._close_cv:
        pool._ensure_cleanup_workers_locked()
        assert len(pool._close_workers) == 4

    close_done = threading.Event()
    close_errors = []

    def _close_pool():
        try:
            pool.close()
        except BaseException as exc:  # pragma: no cover - assertion aid
            close_errors.append(exc)
        finally:
            close_done.set()

    pool._lock.acquire()
    closer = threading.Thread(target=_close_pool)
    closer.start()
    try:
        deadline = time.monotonic() + 1.0
        while not pool._closed and time.monotonic() < deadline:
            time.sleep(0.001)
        assert pool._closed is True

        pool._release_session_client(NETLOC, "leased", client)
        with pool._close_cv:
            assert pool._cleanup_handoff_complete is False
            assert pool._cleanup_shutdown is False
    finally:
        pool._lock.release()

    assert close_done.wait(timeout=2.0)
    closer.join(timeout=1.0)
    assert close_errors == []
    _wait_closed(pool, client)
    _wait_pending(pool)
    metrics = pool.metrics()
    assert metrics["client_slots_used"] == 0


def test_close_returns_within_configured_bound_when_client_close_hangs():
    """Regression: pool shutdown must not inherit a stuck transport close."""
    entered = threading.Event()
    release = threading.Event()
    blocking_done = threading.Event()
    blocking_close_was_daemon = []

    class _BlockingCloseClient:
        def close(self):
            blocking_close_was_daemon.append(threading.current_thread().daemon)
            entered.set()
            release.wait(timeout=5.0)
            blocking_done.set()

    class _FastCloseClient:
        closed = False

        def close(self):
            self.closed = True

    pool = ConnectionPool(PoolConfig(http2=False, close_timeout_seconds=0.05))
    pool._clients[NETLOC] = _BlockingCloseClient()  # type: ignore[assignment]
    fast = _FastCloseClient()
    pool._clients["other.test"] = fast  # type: ignore[assignment]
    session_lock_acquired = []

    class _SessionLockProbeClient:
        def close(self):
            acquired = pool._session_lock.acquire(timeout=0.1)
            session_lock_acquired.append(acquired)
            if acquired:
                pool._session_lock.release()

    probe = _SessionLockProbeClient()
    pool._session_clients[(NETLOC, "probe")] = (probe, time.monotonic())  # type: ignore[assignment]

    try:
        started = time.monotonic()
        pool.close()
        elapsed = time.monotonic() - started

        assert entered.is_set()
        assert elapsed < 0.5
        assert fast.closed is True, "one stuck client must not starve other closes"
        assert blocking_close_was_daemon == [True]
        assert session_lock_acquired == [True]
        assert pool._clients == {}
        assert pool._lock.acquire(timeout=0.1)
        pool._lock.release()
        assert pool._session_lock.acquire(timeout=0.1)
        pool._session_lock.release()
        assert pool._retired_lock.acquire(timeout=0.1)
        pool._retired_lock.release()
    finally:
        release.set()
        assert blocking_done.wait(timeout=1.0)


def test_request_completion_restamps_session_last_used():
    transport = _FlakyTransport(fail_times=0)
    pool = _pool_with_transport(transport)
    client = pool._get_session_client(NETLOC, "sess-1")
    key = (NETLOC, "sess-1")
    pool._session_clients[key] = (client, 0.0)  # artificially ancient

    pool.request("POST", URL, content=b"{}", session_key="sess-1")
    assert pool._session_clients[key][1] > 0.0

    pool._session_clients[key] = (client, 0.0)
    with pool.stream("POST", URL, content=b"{}", session_key="sess-1") as resp:
        resp.read()
    assert pool._session_clients[key][1] > 0.0, "stream exit must re-stamp"
    pool.close()


# ---------------------------------------------------------------------------
# Config / metrics surface
# ---------------------------------------------------------------------------


def test_from_env_parses_new_vars():
    env = {
        "TOKENPAK_POOL_CONNECT_TIMEOUT": "5",
        "TOKENPAK_POOL_READ_TIMEOUT": "120",
        "TOKENPAK_POOL_EVICT_ON_TRANSPORT_ERROR": "0",
        "TOKENPAK_POOL_RETIRE_CLOSE_GRACE_SECS": "60",
        "TOKENPAK_POOL_CLOSE_TIMEOUT_SECS": "1.5",
    }
    with patch.dict(os.environ, env):
        cfg = PoolConfig.from_env()
    assert cfg.connect_timeout == 5.0
    assert cfg.read_timeout == 120.0
    assert cfg.evict_on_transport_error is False
    assert cfg.retire_close_grace_seconds == 60.0
    assert cfg.close_timeout_seconds == 1.5


def test_from_env_defaults_for_new_vars():
    saved = {
        k: os.environ.pop(k, None)
        for k in (
            "TOKENPAK_POOL_CONNECT_TIMEOUT",
            "TOKENPAK_POOL_READ_TIMEOUT",
            "TOKENPAK_POOL_EVICT_ON_TRANSPORT_ERROR",
            "TOKENPAK_POOL_RETIRE_CLOSE_GRACE_SECS",
            "TOKENPAK_POOL_CLOSE_TIMEOUT_SECS",
        )
    }
    try:
        cfg = PoolConfig.from_env()
        assert cfg.connect_timeout == 10.0
        assert cfg.read_timeout == 300.0
        assert cfg.evict_on_transport_error is True
        assert cfg.retire_close_grace_seconds == 900.0
        assert cfg.close_timeout_seconds == 1.0
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_metrics_expose_eviction_counters():
    pool = ConnectionPool(PoolConfig(http2=False))
    m = pool.metrics()
    assert m["evicted_clients"] == 0
    assert m["retired_pending_close"] == 0
    pool.close()
