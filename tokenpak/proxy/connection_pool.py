"""
TokenPak Connection Pool
========================

Manages per-provider ``httpx.Client`` instances with HTTP/2 and persistent
connection reuse.  Eliminates the TCP+TLS handshake overhead incurred by the
previous per-request ``http.client.HTTPSConnection`` approach.

Architecture
------------
- One ``httpx.Client`` per provider netloc (e.g. ``api.anthropic.com``).
- Clients are created lazily and kept alive for the lifetime of the pool.
- HTTP/2 is enabled by default (requires the ``h2`` package — graceful
  fallback to HTTP/1.1 if ``h2`` is not installed).
- All access is protected by a per-pool ``threading.Lock`` so it is safe to
  share a single ``ConnectionPool`` across the threaded proxy server.

Env vars (all optional)
-----------------------
``TOKENPAK_POOL_MAX_CONNECTIONS``
    Maximum concurrent connections per provider (default: ``20``).
``TOKENPAK_POOL_MAX_KEEPALIVE``
    Maximum keep-alive connections per provider (default: ``10``).
``TOKENPAK_POOL_KEEPALIVE_EXPIRY``
    Seconds before idle keep-alive connections are evicted (default: ``30``).
``TOKENPAK_HTTP2``
    Set to ``0`` to disable HTTP/2 (default: ``1`` — enabled).
``TOKENPAK_POOL_CONNECT_TIMEOUT``
    Seconds to wait for a new TCP connection (default: ``10``).
``TOKENPAK_POOL_READ_TIMEOUT``
    Seconds to wait for upstream response bytes (default: ``300``).
``TOKENPAK_POOL_EVICT_ON_TRANSPORT_ERROR``
    Set to ``0`` to keep a client pooled after a transport error
    (default: ``1`` — evict so retries get a fresh connection).
``TOKENPAK_POOL_RETIRE_CLOSE_GRACE_SECS``
    Seconds an evicted client is kept open for in-flight requests before
    being closed (default: ``900``).
``TOKENPAK_POOL_CLOSE_TIMEOUT_SECS``
    Maximum seconds pool shutdown waits for client close calls (default: ``1``).

Performance impact
------------------
Without pooling:
    TCP 3-way handshake  ~10–30 ms
    TLS negotiation      ~30–50 ms
    ─────────────────────────────
    Total per request    ~50–100 ms

With pooling (after first request):
    Connection reuse     <5 ms
    HTTP/2 multiplexing  <5 ms
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_CLOSE_WORKER_COUNT = 4

# ---------------------------------------------------------------------------
# HTTP/2 availability check
# ---------------------------------------------------------------------------


def _http2_available() -> bool:
    """Return True if the ``h2`` package is installed."""
    try:
        import h2  # noqa: F401

        return True
    except ImportError:
        return False


_H2_AVAILABLE: bool = _http2_available()


# ---------------------------------------------------------------------------
# Transport-error classification for client eviction
# ---------------------------------------------------------------------------

# PoolTimeout means the local pool was saturated, not that a connection is
# broken — evicting the whole client would discard healthy connections.
_EVICT_EXCLUDED_ERRORS: tuple = (httpx.PoolTimeout,)


def _is_evictable_transport_error(exc: BaseException) -> bool:
    """True when *exc* suggests the client's pooled connection(s) may be dead."""
    return isinstance(exc, httpx.TransportError) and not isinstance(exc, _EVICT_EXCLUDED_ERRORS)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class PoolConfig:
    """
    Connection pool configuration.

    Attributes
    ----------
    max_connections : int
        Maximum total connections per provider (default: 20).
    max_keepalive_connections : int
        Maximum keep-alive connections per provider (default: 10).
    keepalive_expiry : float
        Seconds before an idle keep-alive connection is evicted (default: 30).
    connect_timeout : float
        Seconds to wait for a new TCP connection (default: 10).
    read_timeout : float
        Seconds to wait for a response (default: 300 — LLM responses can be slow).
    http2 : bool
        Enable HTTP/2 when ``h2`` is installed (default: True).
    evict_on_transport_error : bool
        Evict a client from the pool when a request on it raises a transport
        error, so retries get a fresh client/connection instead of the same
        dead one (default: True).
    retire_close_grace_seconds : float
        How long an evicted client is retained (unclosed) so requests still
        in flight on it can finish before ``close()`` (default: 900).
    close_timeout_seconds : float
        Maximum time pool shutdown waits for client close calls (default: 1).
    """

    max_connections: int = 20
    max_keepalive_connections: int = 10
    keepalive_expiry: float = 30.0
    connect_timeout: float = 10.0
    read_timeout: float = 300.0
    http2: bool = True
    evict_on_transport_error: bool = True
    retire_close_grace_seconds: float = 900.0
    close_timeout_seconds: float = 1.0
    # Per-session upstream client pool — used when the caller passes a
    # session_key to stream()/request(). Each unique session_key gets its
    # own httpx.Client (and thus its own HTTP/2 connection to upstream),
    # which mirrors the native-CLI topology where each CLI process holds
    # its own persistent connection and therefore its own concurrency slot
    # at the provider. Idle clients are reaped after session_client_idle_s
    # and the total pool is capped at session_client_max (LRU evict).
    session_client_max: int = 32
    session_client_idle_seconds: float = 300.0

    @classmethod
    def from_env(cls) -> "PoolConfig":
        """Build a PoolConfig from environment variables."""
        return cls(
            max_connections=int(os.environ.get("TOKENPAK_POOL_MAX_CONNECTIONS", "20")),
            max_keepalive_connections=int(os.environ.get("TOKENPAK_POOL_MAX_KEEPALIVE", "10")),
            keepalive_expiry=float(os.environ.get("TOKENPAK_POOL_KEEPALIVE_EXPIRY", "30")),
            connect_timeout=float(os.environ.get("TOKENPAK_POOL_CONNECT_TIMEOUT", "10")),
            read_timeout=float(os.environ.get("TOKENPAK_POOL_READ_TIMEOUT", "300")),
            http2=os.environ.get("TOKENPAK_HTTP2", "1") != "0",
            evict_on_transport_error=(
                os.environ.get("TOKENPAK_POOL_EVICT_ON_TRANSPORT_ERROR", "1") != "0"
            ),
            retire_close_grace_seconds=float(
                os.environ.get("TOKENPAK_POOL_RETIRE_CLOSE_GRACE_SECS", "900")
            ),
            close_timeout_seconds=float(os.environ.get("TOKENPAK_POOL_CLOSE_TIMEOUT_SECS", "1")),
            session_client_max=int(os.environ.get("TOKENPAK_SESSION_CLIENTS_MAX", "32")),
            session_client_idle_seconds=float(
                os.environ.get("TOKENPAK_SESSION_CLIENT_IDLE_SECS", "300")
            ),
        )


# ---------------------------------------------------------------------------
# Pool metrics — lightweight counters (no deps)
# ---------------------------------------------------------------------------


@dataclass
class PoolMetrics:
    """Rolling counters for connection pool health checks."""

    total_requests: int = 0
    reused_connections: int = 0
    new_connections: int = 0
    errors: int = 0
    evicted_clients: int = 0

    @property
    def reuse_rate(self) -> float:
        """Fraction of requests that reused an existing connection (0–1)."""
        if self.total_requests == 0:
            return 0.0
        return round(self.reused_connections / self.total_requests, 4)

    def to_dict(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "reused_connections": self.reused_connections,
            "new_connections": self.new_connections,
            "errors": self.errors,
            "evicted_clients": self.evicted_clients,
            "reuse_rate": self.reuse_rate,
        }


# ---------------------------------------------------------------------------
# ConnectionPool
# ---------------------------------------------------------------------------


class ConnectionPool:
    """
    Thread-safe, per-provider ``httpx.Client`` pool.

    Usage
    -----
    ::

        pool = ConnectionPool()

        # Non-streaming request
        with pool.request("POST", "https://api.anthropic.com/v1/messages",
                          content=body, headers=headers) as response:
            data = response.read()

        # Streaming request (SSE)
        with pool.stream("POST", "https://api.anthropic.com/v1/messages",
                         content=body, headers=headers) as response:
            for chunk in response.iter_bytes(chunk_size=4096):
                ...

    Lifecycle
    ---------
    Call ``pool.close()`` to release all connections (e.g. on proxy shutdown).
    """

    def __init__(self, config: Optional[PoolConfig] = None) -> None:
        self._config = config or PoolConfig.from_env()
        self._clients: Dict[str, httpx.Client] = {}
        self._lock = threading.Lock()
        self._metrics = PoolMetrics()
        self._metrics_lock = threading.Lock()
        # Per-session client pool: key = (netloc, session_key), value =
        # (client, last_used_monotonic). Separate lock keeps session lookups
        # off the main pool lock's critical path.
        self._session_clients: Dict[tuple, tuple] = {}
        self._session_lock = threading.Lock()
        # Clients evicted after a transport error. They are not closed
        # immediately — other threads may still be mid-request on them —
        # but retired here and closed once the grace period has passed.
        self._retired_clients: List[Tuple[httpx.Client, float]] = []
        self._retired_lock = threading.Lock()
        # In-use lease counts for every pooled client (identity-keyed, guarded
        # by _session_lock). last_used is only a checkout stamp, so a client
        # mid-way through a long stream (per-chunk read timeout is 300s — a
        # legal stream can far exceed the idle window) looks idle to the
        # reaper. The lease count makes in-use clients visible: the reaper
        # and the LRU cap evict only clients with zero active leases.
        # Entries are removed when the count returns to zero, so the dict
        # stays bounded by live concurrency.
        self._session_client_refs: Dict[httpx.Client, int] = {}
        # Overflow clients handed out when the pool is at cap and every
        # pooled client is in use. Never stored in _session_clients; closed
        # on release (create-and-close-after-use) instead of closing a live
        # pooled client.
        self._overflow_clients: set = set()
        # Client.close() is normally fast, but it is transport code and can
        # wedge. Request handlers therefore enqueue closes onto a small fixed
        # daemon-worker set instead of running them inline. The outstanding
        # set is capped during normal operation; once saturated, checkout
        # fails fast instead of creating more clients/FDs. Shutdown may enqueue
        # every already-owned client, but never creates one thread per client.
        self._close_cv = threading.Condition()
        self._close_backlog: Deque[Any] = deque()
        self._close_pending: Dict[int, Any] = {}
        self._close_pending_since: Dict[int, float] = {}
        self._close_in_progress: set[int] = set()
        self._close_attempts: Dict[int, int] = {}
        self._close_workers: List[threading.Thread] = []
        self._close_completed = 0
        self._close_failures = 0
        self._cleanup_worker_start_failures = 0
        self._cleanup_shutdown = False
        # Worker shutdown is safe only after close() has detached every pool
        # map and handed every zero-reference client to the cleanup queue.
        # A final lease release can race with that handoff, so ``_closed``
        # alone is not a sufficient terminal signal.
        self._cleanup_handoff_complete = False
        self._closed = False
        # Every constructed client owns one hard slot until close succeeds.
        # This bounds live + pooled + retired + cleanup-owned clients/FDs even
        # if all cleanup workers wedge. The close queue therefore always has
        # room to take ownership of an existing client and never drops one.
        self._client_slot_limit = max(8, self._config.session_client_max * 2)
        self._client_slots = threading.BoundedSemaphore(self._client_slot_limit)
        self._client_slot_lock = threading.Lock()
        self._client_slot_clients: Dict[int, Any] = {}
        self._client_slot_rejections = 0

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    def _make_client(self) -> httpx.Client:
        """Create a new ``httpx.Client`` with pooling and HTTP/2."""
        cfg = self._config
        use_http2 = cfg.http2 and _H2_AVAILABLE

        limits = httpx.Limits(
            max_connections=cfg.max_connections,
            max_keepalive_connections=cfg.max_keepalive_connections,
            keepalive_expiry=cfg.keepalive_expiry,
        )
        timeout = httpx.Timeout(
            connect=cfg.connect_timeout,
            read=cfg.read_timeout,
            write=cfg.read_timeout,
            pool=cfg.connect_timeout,
        )

        return httpx.Client(
            http2=use_http2,
            limits=limits,
            timeout=timeout,
            follow_redirects=False,
            verify=True,  # enforce TLS certificate validation
        )

    def _new_client(self) -> httpx.Client:
        """Construct one client under the pool-wide hard resource bound."""
        if not self._client_slots.acquire(blocking=False):
            with self._client_slot_lock:
                self._client_slot_rejections += 1
            raise httpx.PoolTimeout("connection client-slot cap is saturated")
        try:
            client = self._make_client()
        except Exception:
            self._client_slots.release()
            raise
        with self._client_slot_lock:
            client_id = id(client)
            if client_id in self._client_slot_clients:
                # Test doubles may deliberately return the same object more
                # than once; one object owns exactly one permit.
                self._client_slots.release()
            else:
                self._client_slot_clients[client_id] = client
        return client

    def _release_client_slot(self, client: Any) -> None:
        """Release a constructed client's permit exactly once after close."""
        with self._client_slot_lock:
            if self._client_slot_clients.pop(id(client), None) is None:
                return
        self._client_slots.release()

    def _get_client(self, netloc: str, checkout: bool = False) -> httpx.Client:
        """
        Return (or lazily create) the ``httpx.Client`` for *netloc*.

        Thread-safe — uses a double-checked lock pattern to avoid holding
        the global lock while constructing the client.
        """
        # Normal traffic is also the liveness path after transient cleanup
        # worker-start exhaustion. Kick before taking any pool lock so cleanup
        # never introduces a reverse lock edge.
        self._kick_cleanup()

        # Keep lookup/create and lease acquisition atomic with eviction. An
        # evicter cannot retire/reap the selected shared client between these
        # steps.
        with self._lock:
            if self._closed:
                raise RuntimeError("connection pool is closed")
            if netloc not in self._clients:
                self._clients[netloc] = self._new_client()
            client = self._clients[netloc]
            if checkout:
                with self._session_lock:
                    self._lease_locked(client)
            return client

    def _get_session_client(
        self, netloc: str, session_key: str, checkout: bool = False
    ) -> httpx.Client:
        """
        Return a dedicated ``httpx.Client`` for ``(netloc, session_key)``.

        Gives each logical session its own upstream HTTP/2 connection — so
        two concurrent sessions get two independent concurrency slots at the
        provider instead of sharing one multiplexed connection. Idle clients
        are reaped; pool is capped at ``session_client_max`` (LRU-evicted).

        Reap/LRU eviction only touches clients with zero active leases —
        a client that is currently checked out (mid-request or mid-stream)
        is never evicted from under its request.

        When ``checkout=True`` (used by :meth:`request` / :meth:`stream`),
        the returned client is leased: its in-use count is incremented under
        the same lock, and the caller MUST pair the call with
        :meth:`_release_session_client` on every exit path. If the pool is
        at cap and every pooled client is leased, a temporary overflow
        client is returned instead of evicting a live one; it is closed on
        release. Plain (non-checkout) calls preserve the legacy peek
        behavior for existing callers and tests.
        """
        cfg = self._config
        now = time.monotonic() if hasattr(time, "monotonic") else time.time()
        key = (netloc, session_key)
        result: Optional[httpx.Client] = None
        self._kick_cleanup()
        with self._session_lock:
            if self._closed:
                raise RuntimeError("connection pool is closed")
            # Retry any overflow close that previously hit the bounded cleanup
            # cap. Keeping it tracked here prevents an FD from being dropped.
            for client in list(self._overflow_clients):
                if self._session_client_refs.get(client, 0) <= 0 and self._schedule_close(client):
                    self._overflow_clients.discard(client)

            # Reap idle clients. Leased clients are skipped outright:
            # "idle" plus an active lease means a long-running request, not
            # reclaimable garbage. A zero-reference client has no in-flight
            # user, so close it now instead of accumulating it for 900 seconds.
            idle_cutoff = now - cfg.session_client_idle_seconds
            stale_keys = [
                k
                for k, (c, last) in self._session_clients.items()
                if last < idle_cutoff and self._session_client_refs.get(c, 0) <= 0
            ]
            for k in stale_keys:
                client, _ = self._session_clients[k]
                if self._schedule_close(client):
                    self._session_clients.pop(k, None)

            entry = self._session_clients.get(key)
            if entry is not None:
                client, _ = entry
                self._session_clients[key] = (client, now)
                if checkout:
                    self._lease_locked(client)
                result = client
            else:
                # Enforce cap — LRU-evict the oldest UNLEASED entry. Leased
                # entries are never closed; the legacy peek fallback retires
                # one behind the active-lease guard instead.
                while len(self._session_clients) >= cfg.session_client_max:
                    evictable = [
                        (k, e)
                        for k, e in self._session_clients.items()
                        if self._session_client_refs.get(e[0], 0) <= 0
                    ]
                    if not evictable:
                        if checkout:
                            # Every pooled client is mid-request. Hand out a
                            # temporary overflow client rather than closing a
                            # live one; release closes the overflow client.
                            if len(self._overflow_clients) >= cfg.session_client_max:
                                raise httpx.PoolTimeout("session overflow client cap is saturated")
                            result = self._new_client()
                            self._overflow_clients.add(result)
                            self._lease_locked(result)
                            break
                        # A non-leased private peek has no release callback and
                        # therefore cannot safely displace a live client.
                        raise httpx.PoolTimeout("all session clients are leased")
                    oldest_key = min(evictable, key=lambda kv: kv[1][1])[0]
                    client, _ = self._session_clients[oldest_key]
                    if self._session_client_refs.get(client, 0) > 0:
                        self._retire(client)
                        self._session_clients.pop(oldest_key, None)
                    else:
                        if not self._schedule_close(client):
                            raise httpx.PoolTimeout("connection cleanup unavailable")
                        self._session_clients.pop(oldest_key, None)

                if result is None:
                    result = self._new_client()
                    self._session_clients[key] = (result, now)
                    if checkout:
                        self._lease_locked(result)
        assert result is not None
        return result

    def _lease_locked(self, client: httpx.Client) -> None:
        """Increment *client*'s in-use lease count. Caller holds _session_lock."""
        self._session_client_refs[client] = self._session_client_refs.get(client, 0) + 1

    def _release_session_client(self, netloc: str, session_key: str, client: httpx.Client) -> None:
        """
        Release a lease taken by ``_get_session_client(..., checkout=True)``.

        Re-stamps ``last_used`` on release, so a client that just finished a
        long stream is measured idle from stream END — not from checkout.
        Overflow clients (handed out when the pool was at cap with every
        client leased) are closed here once their last lease is gone;
        evicted/retired clients are left to the retire/grace machinery.
        """
        key = (netloc, session_key)
        close_retired_now = False
        with self._session_lock:
            refs = self._session_client_refs.get(client, 0) - 1
            if refs > 0:
                self._session_client_refs[client] = refs
            else:
                self._session_client_refs.pop(client, None)
            entry = self._session_clients.get(key)
            if entry is not None and entry[0] is client:
                self._session_clients[key] = (client, time.monotonic())
            elif refs <= 0 and client in self._overflow_clients:
                if self._schedule_close(client):
                    self._overflow_clients.discard(client)
            elif refs <= 0:
                close_retired_now = True
        if close_retired_now:
            self._close_retired_client_now(client)
        self._maybe_finish_cleanup_shutdown()

    def _release_client(self, client: httpx.Client) -> None:
        """Release a lease on a non-session shared client."""
        close_retired_now = False
        with self._session_lock:
            refs = self._session_client_refs.get(client, 0) - 1
            if refs > 0:
                self._session_client_refs[client] = refs
            else:
                self._session_client_refs.pop(client, None)
                close_retired_now = True
        if close_retired_now:
            self._close_retired_client_now(client)
        self._maybe_finish_cleanup_shutdown()

    def _maybe_finish_cleanup_shutdown(self) -> None:
        """Let fixed workers exit once a closed pool has no live leases."""
        if not self._closed:
            return
        with self._session_lock:
            has_leases = any(refs > 0 for refs in self._session_client_refs.values())
        if not has_leases:
            with self._close_cv:
                if not self._cleanup_handoff_complete:
                    return
                self._cleanup_shutdown = True
                self._close_cv.notify_all()

    def _touch_session(self, netloc: str, session_key: str) -> None:
        """Re-stamp a session client's last_used after a request completes,
        so long-running streams don't age it into the idle reaper's window."""
        key = (netloc, session_key)
        now = time.monotonic()
        with self._session_lock:
            entry = self._session_clients.get(key)
            if entry is not None:
                self._session_clients[key] = (entry[0], now)

    def _retire(self, client: httpx.Client) -> None:
        """Queue *client* to be closed after the in-flight grace period."""
        with self._retired_lock:
            if not any(retired is client for retired, _ in self._retired_clients):
                self._retired_clients.append((client, time.monotonic()))

    def _close_client(self, client: httpx.Client) -> bool:
        """Close one client and report success without raising."""
        try:
            client.close()
            return True
        except Exception:
            logger.warning("connection-pool client close failed", exc_info=True)
            return False

    def _ensure_cleanup_workers_locked(self) -> None:
        """Start the fixed daemon cleanup set. Caller holds ``_close_cv``."""
        # A worker removes itself under this same condition immediately before
        # exit. Pruning is still required for unexpected thread termination and
        # for historical dead entries created by older code/test doubles.
        self._close_workers = [worker for worker in self._close_workers if worker.is_alive()]
        target = min(_CLOSE_WORKER_COUNT, self._client_slot_limit)
        while len(self._close_workers) < target:
            index = len(self._close_workers)
            worker = threading.Thread(
                target=self._cleanup_worker,
                name=f"tokenpak-connection-close-{index}",
                daemon=True,
            )
            # Append before start while holding _close_cv. A newly started
            # worker cannot enter its condition section until this lock is
            # released, and a failed start is removed below.
            self._close_workers.append(worker)
            try:
                worker.start()
            except Exception:
                self._close_workers.remove(worker)
                self._cleanup_worker_start_failures += 1
                logger.warning("connection-pool cleanup worker failed to start", exc_info=True)
                break

    def _kick_cleanup(self) -> None:
        """Recover cleanup capacity whenever owned backlog is observable."""
        with self._close_cv:
            if not self._close_backlog:
                return
            self._ensure_cleanup_workers_locked()
            self._close_cv.notify_all()

    def _cleanup_worker(self) -> None:
        """Drain client closes without ever occupying a request-handler thread."""
        while True:
            with self._close_cv:
                while not self._close_backlog and not self._cleanup_shutdown:
                    self._close_cv.wait()
                if not self._close_backlog and self._cleanup_shutdown:
                    current = threading.current_thread()
                    if current in self._close_workers:
                        self._close_workers.remove(current)
                    self._close_cv.notify_all()
                    return
                client = self._close_backlog.popleft()
                client_id = id(client)
                self._close_in_progress.add(client_id)
                attempt = self._close_attempts.get(client_id, 0) + 1
                self._close_attempts[client_id] = attempt
            succeeded = self._close_client(client)
            with self._close_cv:
                self._close_in_progress.discard(client_id)
                if succeeded:
                    self._close_pending.pop(client_id, None)
                    self._close_pending_since.pop(client_id, None)
                    self._close_attempts.pop(client_id, None)
                    self._close_completed += 1
                    self._release_client_slot(client)
                else:
                    self._close_failures += 1
                self._close_cv.notify_all()
            if not succeeded:
                time.sleep(min(1.0, 0.01 * (2 ** min(attempt - 1, 7))))
                with self._close_cv:
                    if client_id in self._close_pending:
                        self._close_backlog.append(client)
                        self._close_cv.notify()

    def _schedule_close(self, client: httpx.Client, *, force: bool = False) -> bool:
        """Enqueue one idempotent close on fixed workers.

        Every existing client already owns a hard construction permit, so the
        deduplicated cleanup registry can always accept it. ``force`` remains
        accepted for compatibility with the shutdown caller but does not alter
        lease eligibility.
        """
        try:
            if client.is_closed:
                self._release_client_slot(client)
                return True
        except Exception:
            pass
        with self._close_cv:
            client_id = id(client)
            if client_id in self._close_pending:
                # A prior start attempt may have failed during thread/resource
                # exhaustion. Every duplicate handoff is a recovery kick.
                self._ensure_cleanup_workers_locked()
                self._close_cv.notify()
                return True
            # The fast-path observation above is not authoritative. A cleanup
            # worker can close the client and remove it from ``_close_pending``
            # while this caller is waiting for the condition. Re-check under
            # the same condition before enqueueing so that interleaving cannot
            # schedule a second close for an already-closed client.
            try:
                if client.is_closed:
                    self._release_client_slot(client)
                    return True
            except Exception:
                pass
            self._close_pending[client_id] = client
            self._close_pending_since[client_id] = time.monotonic()
            self._close_backlog.append(client)
            self._ensure_cleanup_workers_locked()
            self._close_cv.notify()
            return True

    def _close_retired_client_now(self, client: httpx.Client) -> None:
        """Close *client* immediately if it is retired and no longer leased."""
        with self._retired_lock:
            keep: List[Tuple[httpx.Client, float]] = []
            for retired_client, retired_at in self._retired_clients:
                if retired_client is client:
                    if not self._schedule_close(client):
                        keep.append((retired_client, retired_at))
                else:
                    keep.append((retired_client, retired_at))
            self._retired_clients = keep

    def _evict_client(self, netloc: str, session_key: Optional[str], client: httpx.Client) -> bool:
        """
        Remove *client* from the pool after a transport error so the next
        checkout builds a fresh client (and therefore fresh connections).

        Identity-checked: if the pool already holds a replacement client for
        the slot, nothing is evicted — a burst of failures on one dead client
        evicts it exactly once. The evicted client is retired, not closed,
        so requests still in flight on it can finish; retired clients are
        closed by :meth:`_reap_retired` after the grace period.
        """
        if not self._config.evict_on_transport_error:
            return False
        evicted = False
        if session_key:
            key = (netloc, session_key)
            with self._session_lock:
                entry = self._session_clients.get(key)
                if entry is not None and entry[0] is client:
                    self._session_clients.pop(key)
                    self._retire(client)
                    evicted = True
        else:
            with self._lock:
                if self._clients.get(netloc) is client:
                    self._clients.pop(netloc)
                    self._retire(client)
                    evicted = True
        if evicted:
            with self._metrics_lock:
                self._metrics.evicted_clients += 1
        self._reap_retired()
        return evicted

    def _reap_retired(self, force: bool = False) -> None:
        """Close retired clients whose in-flight grace period has passed."""
        cutoff = time.monotonic() - self._config.retire_close_grace_seconds
        with self._session_lock:
            leased = {client for client, refs in self._session_client_refs.items() if refs > 0}
        with self._retired_lock:
            keep: List[Tuple[httpx.Client, float]] = []
            for client, retired_at in self._retired_clients:
                if (force or retired_at <= cutoff) and client not in leased:
                    if not self._schedule_close(client, force=force):
                        keep.append((client, retired_at))
                else:
                    keep.append((client, retired_at))
            self._retired_clients = keep

    # ------------------------------------------------------------------
    # Public request interface
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        content: Optional[bytes] = None,
        headers: Optional[dict] = None,
        session_key: Optional[str] = None,
    ) -> httpx.Response:
        """
        Send a non-streaming HTTP request via the pool.

        Returns the full ``httpx.Response`` with ``.content`` already read.
        Unlike the streaming variant, the caller does NOT need a context manager.

        Parameters
        ----------
        method : str
            HTTP method (``"GET"``, ``"POST"``, etc.).
        url : str
            Full URL including scheme.
        content : bytes, optional
            Request body.
        headers : dict, optional
            Request headers.  **Must** include ``Host`` and auth headers.

        Returns
        -------
        httpx.Response
            Response with body content loaded in memory.
        """
        parsed = httpx.URL(url)
        netloc = parsed.host
        client = (
            self._get_session_client(netloc, session_key, checkout=True)
            if session_key
            else self._get_client(netloc, checkout=True)
        )

        with self._metrics_lock:
            self._metrics.total_requests += 1

        try:
            response = client.request(
                method,
                url,
                content=content,
                headers=headers,
            )
            # Track reuse heuristic: HTTP/2 always reuses; HTTP/1.1 reuses
            # when keep-alive is confirmed.
            with self._metrics_lock:
                proto = response.http_version  # "HTTP/1.1" or "HTTP/2"
                if (
                    proto == "HTTP/2"
                    or response.headers.get("connection", "").lower() == "keep-alive"
                ):
                    self._metrics.reused_connections += 1
                else:
                    self._metrics.new_connections += 1
            return response
        except Exception as exc:
            with self._metrics_lock:
                self._metrics.errors += 1
                self._metrics.new_connections += 1
            if _is_evictable_transport_error(exc):
                self._evict_client(netloc, session_key, client)
            raise
        finally:
            # Release the lease on every exit path. The response body is
            # already fully read by client.request(), so the client is no
            # longer needed for this request. Release also re-stamps
            # last_used (subsumes the success-path _touch_session call).
            if session_key:
                self._release_session_client(netloc, session_key, client)
            else:
                self._release_client(client)

    def stream(
        self,
        method: str,
        url: str,
        *,
        content: Optional[bytes] = None,
        headers: Optional[dict] = None,
        session_key: Optional[str] = None,
    ):
        """
        Send a streaming HTTP request via the pool.

        Returns an ``httpx`` streaming response context manager.
        The caller is responsible for using it as a context manager::

            with pool.stream("POST", url, content=body, headers=h) as resp:
                for chunk in resp.iter_bytes(chunk_size=4096):
                    ...

        Parameters
        ----------
        method, url, content, headers
            Same as :meth:`request`.
        """
        parsed = httpx.URL(url)
        netloc = parsed.host
        if session_key:
            client = self._get_session_client(netloc, session_key, checkout=True)

            def on_release() -> None:
                self._release_session_client(netloc, session_key, client)
        else:
            client = self._get_client(netloc, checkout=True)

            def on_release() -> None:
                self._release_client(client)

        with self._metrics_lock:
            self._metrics.total_requests += 1

        try:
            return _StreamingContext(
                client,
                method,
                url,
                content,
                headers,
                self._metrics,
                self._metrics_lock,
                on_transport_error=lambda: self._evict_client(netloc, session_key, client),
                on_complete=(
                    (lambda: self._touch_session(netloc, session_key)) if session_key else None
                ),
                on_release=on_release,
            )
        except Exception:
            # Do NOT release here: _StreamingContext.__init__ already releases
            # the lease (idempotently, via _release()) on its only raising path —
            # the client.stream() construction — before re-raising. A second
            # on_release() would double-decrement the session-client refcount and
            # could close a client another concurrent stream still holds.
            raise

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def http2_enabled(self) -> bool:
        """True if HTTP/2 will be used (config says yes AND h2 is installed)."""
        return self._config.http2 and _H2_AVAILABLE

    @property
    def active_providers(self) -> list:
        """List of netloc strings for which a client has been created."""
        with self._lock:
            return list(self._clients.keys())

    def metrics(self) -> dict:
        """Return a copy of the current pool metrics."""
        # /health calls this surface, making health polling a safe recovery
        # kick after a transient inability to start cleanup workers.
        self._kick_cleanup()
        with self._metrics_lock:
            data = self._metrics.to_dict()
        with self._retired_lock:
            retired = len(self._retired_clients)
        now = time.monotonic()
        with self._close_cv:
            cleanup_pending = len(self._close_pending)
            cleanup_queued = len(self._close_backlog)
            cleanup_in_progress = len(self._close_in_progress)
            oldest = (
                max(0.0, now - min(self._close_pending_since.values()))
                if self._close_pending_since
                else 0.0
            )
            data.update(
                {
                    "cleanup_pending_close": cleanup_pending,
                    "cleanup_queued": cleanup_queued,
                    "cleanup_in_progress": cleanup_in_progress,
                    "cleanup_retrying": max(
                        0, cleanup_pending - cleanup_queued - cleanup_in_progress
                    ),
                    "cleanup_failures_total": self._close_failures,
                    "cleanup_worker_start_failures_total": (self._cleanup_worker_start_failures),
                    "cleanup_completed_total": self._close_completed,
                    "cleanup_oldest_pending_seconds": round(oldest, 3),
                    "cleanup_workers_alive": sum(
                        worker.is_alive() for worker in self._close_workers
                    ),
                }
            )
        with self._client_slot_lock:
            slots_used = len(self._client_slot_clients)
            data.update(
                {
                    "client_slots_used": slots_used,
                    "client_slots_max": self._client_slot_limit,
                    "client_capacity_rejections_total": self._client_slot_rejections,
                    "cleanup_saturated": slots_used >= self._client_slot_limit,
                }
            )
        data["retired_pending_close"] = retired + cleanup_pending
        return data

    def reset_metrics(self) -> None:
        """Reset all pool counters to zero."""
        with self._metrics_lock:
            self._metrics = PoolMetrics()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all pooled clients within the configured shutdown bound."""
        started = time.monotonic()
        configured_timeout = self._config.close_timeout_seconds
        timeout = (
            configured_timeout
            if math.isfinite(configured_timeout) and configured_timeout > 0.0
            else 0.0
        )
        deadline = started + timeout
        self._closed = True

        clients: List[Any] = []
        # Selection, lease visibility, and active retirement are atomic with
        # every checkout path. Active requests/streams are never force-closed;
        # their final release performs the cleanup handoff.
        with self._lock:
            with self._session_lock:
                pooled = list(self._clients.values())
                pooled.extend(client for client, _ in self._session_clients.values())
                pooled.extend(self._overflow_clients)
                leased = {client for client, refs in self._session_client_refs.items() if refs > 0}
                for client in pooled:
                    if client in leased:
                        self._retire(client)
                    else:
                        clients.append(client)
                self._clients.clear()
                self._session_clients.clear()
                self._overflow_clients.clear()

        # Retired clients with no live lease move to cleanup; active ones stay
        # visible until final release. Do not clear positive refcounts.
        with self._retired_lock:
            keep: List[Tuple[httpx.Client, float]] = []
            for client, retired_at in self._retired_clients:
                if client in leased:
                    keep.append((client, retired_at))
                else:
                    clients.append(client)
            self._retired_clients = keep

        for client in {id(client): client for client in clients}.values():
            self._schedule_close(client, force=True)

        # Publish handoff completion only after all pool maps are detached and
        # every eligible client is queued. A concurrent final lease release may
        # observe _closed before this point, but cannot terminate idle cleanup
        # workers until this flag becomes true.
        with self._close_cv:
            self._cleanup_handoff_complete = True
            if self._close_pending:
                self._ensure_cleanup_workers_locked()
                self._close_cv.notify_all()

        self._maybe_finish_cleanup_shutdown()
        with self._close_cv:
            while self._close_pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                self._close_cv.wait(timeout=remaining)
            pending = len(self._close_pending)
        if pending:
            logger.warning(
                "connection-pool close exceeded %.3fs for %d client(s); "
                "fixed daemon cleanup continues",
                timeout,
                pending,
            )

    def session_client_snapshot(self) -> Dict[str, Any]:
        """Return a diagnostic snapshot of the per-session client pool."""
        with self._session_lock:
            return {
                "count": len(self._session_clients),
                "cap": self._config.session_client_max,
                "idle_secs": self._config.session_client_idle_seconds,
                "in_use": sum(
                    1
                    for c, _ in self._session_clients.values()
                    if self._session_client_refs.get(c, 0) > 0
                ),
                "overflow_active": len(self._overflow_clients),
                "keys": [f"{netloc}::{sk}" for (netloc, sk) in self._session_clients],
            }

    def __repr__(self) -> str:
        providers = self.active_providers
        return (
            f"<ConnectionPool providers={providers} "
            f"http2={self.http2_enabled} "
            f"metrics={self._metrics.to_dict()}>"
        )


# ---------------------------------------------------------------------------
# Streaming context helper
# ---------------------------------------------------------------------------


class _StreamingContext:
    """
    Thin wrapper that tracks metrics around ``httpx.Client.stream()``.

    Implements the context manager protocol so callers can use:
    ``with pool.stream(...) as resp:``
    """

    def __init__(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        content: Optional[bytes],
        headers: Optional[dict],
        metrics: PoolMetrics,
        lock: threading.Lock,
        on_transport_error: Optional[Callable[[], Any]] = None,
        on_complete: Optional[Callable[[], Any]] = None,
        on_release: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._on_release = on_release
        self._released = False
        try:
            self._ctx = client.stream(method, url, content=content, headers=headers)
        except Exception:
            self._release()
            raise
        self._metrics = metrics
        self._lock = lock
        self._response: Optional[httpx.Response] = None
        self._on_transport_error = on_transport_error
        self._on_complete = on_complete
        self._error_recorded = False

    def _release(self) -> None:
        """Release the session-client lease exactly once."""
        if self._released:
            return
        self._released = True
        if self._on_release is not None:
            try:
                self._on_release()
            except Exception:
                pass

    def __enter__(self) -> httpx.Response:
        try:
            self._response = self._ctx.__enter__()
        except Exception as exc:
            self._record_error(exc)
            # __exit__ is never called when __enter__ raises — release here
            # so the lease doesn't leak (and the reaper can reclaim later).
            self._release()
            raise
        with self._lock:
            proto = self._response.http_version
            if (
                proto == "HTTP/2"
                or self._response.headers.get("connection", "").lower() == "keep-alive"
            ):
                self._metrics.reused_connections += 1
            else:
                self._metrics.new_connections += 1
        return self._response

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._ctx.__exit__(exc_type, exc, tb)
        finally:
            if exc is not None:
                self._record_error(exc)
            if self._on_complete is not None:
                try:
                    self._on_complete()
                except Exception:
                    pass
            self._release()

    def _record_error(self, exc: BaseException) -> None:
        """Count an upstream error once and evict the client if it looks dead.

        Only ``httpx.HTTPError`` counts — the streaming ``with`` body also
        writes to the downstream client socket, and a downstream disconnect
        (e.g. ``BrokenPipeError``) says nothing about upstream health.
        """
        if not isinstance(exc, httpx.HTTPError) or self._error_recorded:
            return
        self._error_recorded = True
        with self._lock:
            self._metrics.errors += 1
        if self._on_transport_error is not None and _is_evictable_transport_error(exc):
            try:
                self._on_transport_error()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Module-level singleton (used by the proxy server)
# ---------------------------------------------------------------------------

_GLOBAL_POOL: Optional[ConnectionPool] = None
_GLOBAL_POOL_LOCK = threading.Lock()


def get_global_pool() -> ConnectionPool:
    """Return (or lazily create) the module-level singleton pool."""
    global _GLOBAL_POOL
    if _GLOBAL_POOL is not None:
        return _GLOBAL_POOL
    with _GLOBAL_POOL_LOCK:
        if _GLOBAL_POOL is None:
            _GLOBAL_POOL = ConnectionPool()
    return _GLOBAL_POOL


def reset_global_pool() -> None:
    """Close and discard the global pool (used in tests)."""
    global _GLOBAL_POOL
    with _GLOBAL_POOL_LOCK:
        if _GLOBAL_POOL is not None:
            _GLOBAL_POOL.close()
            _GLOBAL_POOL = None
