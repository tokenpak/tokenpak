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

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

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
    return isinstance(exc, httpx.TransportError) and not isinstance(
        exc, _EVICT_EXCLUDED_ERRORS
    )


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
    """

    max_connections: int = 20
    max_keepalive_connections: int = 10
    keepalive_expiry: float = 30.0
    connect_timeout: float = 10.0
    read_timeout: float = 300.0
    http2: bool = True
    evict_on_transport_error: bool = True
    retire_close_grace_seconds: float = 900.0
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

    def _get_client(self, netloc: str) -> httpx.Client:
        """
        Return (or lazily create) the ``httpx.Client`` for *netloc*.

        Thread-safe — uses a double-checked lock pattern to avoid holding
        the global lock while constructing the client.
        """
        # Fast path — client already exists
        client = self._clients.get(netloc)
        if client is not None:
            return client

        # Slow path — create under lock
        with self._lock:
            # Re-check after acquiring lock (another thread may have created it)
            if netloc not in self._clients:
                self._clients[netloc] = self._make_client()
            return self._clients[netloc]

    def _get_session_client(self, netloc: str, session_key: str) -> httpx.Client:
        """
        Return a dedicated ``httpx.Client`` for ``(netloc, session_key)``.

        Gives each logical session its own upstream HTTP/2 connection — so
        two concurrent sessions get two independent concurrency slots at the
        provider instead of sharing one multiplexed connection. Idle clients
        are reaped; pool is capped at ``session_client_max`` (LRU-evicted).
        """
        cfg = self._config
        now = time.monotonic() if hasattr(time, "monotonic") else time.time()
        key = (netloc, session_key)
        with self._session_lock:
            # Reap idle clients. Retire instead of closing inline: last_used
            # is a checkout stamp, so a client mid-way through a long stream
            # can look idle — closing it here corrupts the live request.
            idle_cutoff = now - cfg.session_client_idle_seconds
            stale_keys = [
                k for k, (_, last) in self._session_clients.items() if last < idle_cutoff
            ]
            for k in stale_keys:
                client, _ = self._session_clients.pop(k)
                self._retire(client)

            entry = self._session_clients.get(key)
            if entry is not None:
                client, _ = entry
                self._session_clients[key] = (client, now)
                return client

            # Enforce cap — LRU-evict oldest (retired, same mid-flight hazard)
            while len(self._session_clients) >= cfg.session_client_max:
                oldest_key = min(
                    self._session_clients.items(), key=lambda kv: kv[1][1]
                )[0]
                client, _ = self._session_clients.pop(oldest_key)
                self._retire(client)

            client = self._make_client()
            self._session_clients[key] = (client, now)
            return client

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
            self._retired_clients.append((client, time.monotonic()))

    def _evict_client(
        self, netloc: str, session_key: Optional[str], client: httpx.Client
    ) -> bool:
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
                    evicted = True
        else:
            with self._lock:
                if self._clients.get(netloc) is client:
                    self._clients.pop(netloc)
                    evicted = True
        if evicted:
            self._retire(client)
            with self._metrics_lock:
                self._metrics.evicted_clients += 1
        self._reap_retired()
        return evicted

    def _reap_retired(self, force: bool = False) -> None:
        """Close retired clients whose in-flight grace period has passed."""
        cutoff = time.monotonic() - self._config.retire_close_grace_seconds
        to_close: List[httpx.Client] = []
        with self._retired_lock:
            keep: List[Tuple[httpx.Client, float]] = []
            for client, retired_at in self._retired_clients:
                if force or retired_at <= cutoff:
                    to_close.append(client)
                else:
                    keep.append((client, retired_at))
            self._retired_clients = keep
        for client in to_close:
            try:
                client.close()
            except Exception:
                pass

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
            self._get_session_client(netloc, session_key)
            if session_key
            else self._get_client(netloc)
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
            if session_key:
                self._touch_session(netloc, session_key)
            return response
        except Exception as exc:
            with self._metrics_lock:
                self._metrics.errors += 1
                self._metrics.new_connections += 1
            if _is_evictable_transport_error(exc):
                self._evict_client(netloc, session_key, client)
            raise

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
        client = (
            self._get_session_client(netloc, session_key)
            if session_key
            else self._get_client(netloc)
        )

        with self._metrics_lock:
            self._metrics.total_requests += 1

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
                (lambda: self._touch_session(netloc, session_key))
                if session_key
                else None
            ),
        )

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
        with self._metrics_lock:
            data = self._metrics.to_dict()
        with self._retired_lock:
            data["retired_pending_close"] = len(self._retired_clients)
        return data

    def reset_metrics(self) -> None:
        """Reset all pool counters to zero."""
        with self._metrics_lock:
            self._metrics = PoolMetrics()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all pooled clients and release TCP/TLS resources."""
        with self._lock:
            for client in self._clients.values():
                try:
                    client.close()
                except Exception:
                    pass
            self._clients.clear()
        with self._session_lock:
            for client, _ in self._session_clients.values():
                try:
                    client.close()
                except Exception:
                    pass
            self._session_clients.clear()
        self._reap_retired(force=True)

    def session_client_snapshot(self) -> Dict[str, Any]:
        """Return a diagnostic snapshot of the per-session client pool."""
        with self._session_lock:
            return {
                "count": len(self._session_clients),
                "cap": self._config.session_client_max,
                "idle_secs": self._config.session_client_idle_seconds,
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
    ) -> None:
        self._ctx = client.stream(method, url, content=content, headers=headers)
        self._metrics = metrics
        self._lock = lock
        self._response: Optional[httpx.Response] = None
        self._on_transport_error = on_transport_error
        self._on_complete = on_complete
        self._error_recorded = False

    def __enter__(self) -> httpx.Response:
        try:
            self._response = self._ctx.__enter__()
        except Exception as exc:
            self._record_error(exc)
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
