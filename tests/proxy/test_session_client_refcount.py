"""
Tests for session-client in-use leases (refcounted checkout/release)

The session-client pool reaps idle clients and LRU-evicts beyond the cap,
but ``last_used`` is only refreshed at checkout — a legal long stream (the
per-chunk read timeout alone is 300s) can look "idle" while it is mid-flight.
These tests pin the lease semantics that keep live clients safe:

- a checked-out (leased) client survives the idle reap pass
- an idle, unleased client is still reaped (retired)
- LRU overflow with every pooled client leased hands out a temporary
  overflow client instead of evicting a live one; the overflow client is
  closed on release
- release re-stamps last_used (idle measured from stream END, not checkout)
- request() and stream() release their lease on every exit path
"""

from __future__ import annotations

from typing import Iterator

import httpx
import pytest

from tokenpak.proxy.connection_pool import ConnectionPool, PoolConfig

URL = "http://upstream.test/v1/messages"
NETLOC = "upstream.test"


def _ok_transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))


class _FailingTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset by peer")


class _ExplodingStream(httpx.SyncByteStream):
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


def _pool(transport: httpx.BaseTransport, **cfg_kwargs) -> ConnectionPool:
    pool = ConnectionPool(PoolConfig(http2=False, **cfg_kwargs))
    pool._make_client = lambda: httpx.Client(transport=transport)  # type: ignore[method-assign]
    return pool


def _refs(pool: ConnectionPool, client: httpx.Client) -> int:
    return pool._session_client_refs.get(client, 0)


# ---------------------------------------------------------------------------
# Reap pass vs leases
# ---------------------------------------------------------------------------

def test_leased_client_survives_idle_reap_pass():
    """A client mid-request must not be reaped even when it looks idle."""
    pool = _pool(_ok_transport(), session_client_idle_seconds=0.0)
    held = pool._get_session_client(NETLOC, "sess-live", checkout=True)
    assert _refs(pool, held) == 1

    # Any later checkout triggers the idle reap pass (idle window is 0).
    pool._get_session_client(NETLOC, "sess-other", checkout=True)

    assert (NETLOC, "sess-live") in pool._session_clients
    assert pool._session_clients[(NETLOC, "sess-live")][0] is held
    assert held.is_closed is False
    pool.close()


def test_idle_unleased_client_is_reaped():
    pool = _pool(_ok_transport(), session_client_idle_seconds=0.0)
    client = pool._get_session_client(NETLOC, "sess-idle", checkout=True)
    pool._release_session_client(NETLOC, "sess-idle", client)
    assert _refs(pool, client) == 0

    pool._get_session_client(NETLOC, "sess-trigger", checkout=True)

    assert (NETLOC, "sess-idle") not in pool._session_clients
    # Reaped clients are retired (grace-closed), not closed inline.
    assert pool.metrics()["retired_pending_close"] >= 1
    pool.close()
    assert client.is_closed is True


# ---------------------------------------------------------------------------
# LRU cap vs leases
# ---------------------------------------------------------------------------

def test_lru_overflow_with_all_leased_does_not_close_live_clients():
    pool = _pool(_ok_transport(), session_client_max=2)
    a = pool._get_session_client(NETLOC, "sess-a", checkout=True)
    b = pool._get_session_client(NETLOC, "sess-b", checkout=True)

    c = pool._get_session_client(NETLOC, "sess-c", checkout=True)

    # Overflow: c is temporary, a and b stay pooled and open.
    assert (NETLOC, "sess-c") not in pool._session_clients
    assert set(pool._session_clients) == {(NETLOC, "sess-a"), (NETLOC, "sess-b")}
    assert a.is_closed is False and b.is_closed is False
    assert pool.metrics()["retired_pending_close"] == 0
    assert pool.session_client_snapshot()["overflow_active"] == 1

    # Overflow client is create-and-close-after-use.
    pool._release_session_client(NETLOC, "sess-c", c)
    assert c.is_closed is True
    assert pool.session_client_snapshot()["overflow_active"] == 0

    # Pooled clients stay cached (open) after release.
    pool._release_session_client(NETLOC, "sess-a", a)
    pool._release_session_client(NETLOC, "sess-b", b)
    assert a.is_closed is False and b.is_closed is False
    pool.close()


def test_lru_evicts_unleased_entry_when_available():
    pool = _pool(_ok_transport(), session_client_max=2)
    a = pool._get_session_client(NETLOC, "sess-a", checkout=True)
    pool._release_session_client(NETLOC, "sess-a", a)  # unleased → evictable
    b = pool._get_session_client(NETLOC, "sess-b", checkout=True)

    pool._get_session_client(NETLOC, "sess-c", checkout=True)

    assert (NETLOC, "sess-a") not in pool._session_clients
    assert (NETLOC, "sess-b") in pool._session_clients
    assert (NETLOC, "sess-c") in pool._session_clients
    assert b.is_closed is False
    pool.close()


# ---------------------------------------------------------------------------
# Release semantics
# ---------------------------------------------------------------------------

def test_release_restamps_last_used():
    pool = _pool(_ok_transport())
    client = pool._get_session_client(NETLOC, "sess-1", checkout=True)
    key = (NETLOC, "sess-1")
    pool._session_clients[key] = (client, 0.0)  # artificially ancient

    pool._release_session_client(NETLOC, "sess-1", client)
    assert pool._session_clients[key][1] > 0.0, "release must re-stamp last_used"
    pool.close()


def test_nested_leases_release_pairwise():
    pool = _pool(_ok_transport())
    c1 = pool._get_session_client(NETLOC, "sess-1", checkout=True)
    c2 = pool._get_session_client(NETLOC, "sess-1", checkout=True)
    assert c1 is c2
    assert _refs(pool, c1) == 2
    pool._release_session_client(NETLOC, "sess-1", c1)
    assert _refs(pool, c1) == 1
    pool._release_session_client(NETLOC, "sess-1", c1)
    assert _refs(pool, c1) == 0
    assert (NETLOC, "sess-1") in pool._session_clients  # stays cached
    pool.close()


# ---------------------------------------------------------------------------
# request()/stream() bracket the lease on all exit paths
# ---------------------------------------------------------------------------

def test_request_releases_lease_on_success():
    pool = _pool(_ok_transport())
    resp = pool.request("POST", URL, content=b"{}", session_key="sess-1")
    assert resp.status_code == 200
    assert pool._session_client_refs == {}
    pool.close()


def test_request_releases_lease_on_transport_error():
    pool = _pool(_FailingTransport())
    with pytest.raises(httpx.ReadError):
        pool.request("POST", URL, content=b"{}", session_key="sess-1")
    assert pool._session_client_refs == {}
    pool.close()


def test_stream_releases_lease_on_exit_and_midstream_error():
    pool = _pool(_ok_transport())
    with pool.stream("POST", URL, content=b"{}", session_key="sess-1") as resp:
        assert _refs(pool, pool._session_clients[(NETLOC, "sess-1")][0]) == 1
        resp.read()
    assert pool._session_client_refs == {}

    failing = _pool(_MidStreamFailTransport())
    with pytest.raises(httpx.ReadError):
        with failing.stream("POST", URL, content=b"{}", session_key="sess-1") as resp:
            for _ in resp.iter_bytes(chunk_size=8):
                pass
    assert failing._session_client_refs == {}
    pool.close()
    failing.close()


def test_stream_releases_lease_when_enter_fails():
    pool = _pool(_FailingTransport())
    with pytest.raises(httpx.ReadError):
        with pool.stream("POST", URL, content=b"{}", session_key="sess-1"):
            pass  # pragma: no cover — __enter__ raises
    assert pool._session_client_refs == {}
    pool.close()


def test_close_clears_leases_and_overflow():
    pool = _pool(_ok_transport(), session_client_max=1)
    a = pool._get_session_client(NETLOC, "sess-a", checkout=True)
    overflow = pool._get_session_client(NETLOC, "sess-b", checkout=True)
    pool.close()
    assert a.is_closed is True
    assert overflow.is_closed is True
    assert pool._session_client_refs == {}
    assert pool._overflow_clients == set()


def test_stream_construction_failure_releases_lease_exactly_once(monkeypatch):
    # Regression for a double-release: stream()'s except-path used to call
    # on_release() a second time even though _StreamingContext.__init__ already
    # releases the lease (idempotently) when the client.stream() construction
    # fails. The extra release decremented the session-client refcount past
    # baseline and closed a client another concurrent lease still held.
    pool = _pool(_ok_transport())
    # A concurrent holder keeps a lease on the SAME session client (ref == 1).
    held = pool._get_session_client(NETLOC, "sess-1", checkout=True)
    assert _refs(pool, held) == 1

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("stream construction failed")

    # Make the reused client's stream() construction fail.
    monkeypatch.setattr(held, "stream", _boom)

    with pytest.raises(httpx.ConnectError):
        pool.stream("POST", URL, content=b"{}", session_key="sess-1")

    # Exactly ONE release for the failed stream: the concurrent holder's lease
    # is intact (baseline, not double-decremented) and its client was not
    # prematurely closed.
    assert _refs(pool, held) == 1
    assert held.is_closed is False
    pool.close()
