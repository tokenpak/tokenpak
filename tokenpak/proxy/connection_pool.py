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
from typing import Any, Dict, Optional

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
    """

    max_connections: int = 20
    max_keepalive_connections: int = 10
    keepalive_expiry: float = 30.0
    connect_timeout: float = 10.0
    read_timeout: float = 300.0
    http2: bool = True
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
            http2=os.environ.get("TOKENPAK_HTTP2", "1") != "0",
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
            # Reap idle clients
            idle_cutoff = now - cfg.session_client_idle_seconds
            stale_keys = [
                k for k, (_, last) in self._session_clients.items() if last < idle_cutoff
            ]
            for k in stale_keys:
                client, _ = self._session_clients.pop(k)
                try:
                    client.close()
                except Exception:
                    pass

            entry = self._session_clients.get(key)
            if entry is not None:
                client, _ = entry
                self._session_clients[key] = (client, now)
                return client

            # Enforce cap — LRU-evict oldest
            while len(self._session_clients) >= cfg.session_client_max:
                oldest_key = min(
                    self._session_clients.items(), key=lambda kv: kv[1][1]
                )[0]
                client, _ = self._session_clients.pop(oldest_key)
                try:
                    client.close()
                except Exception:
                    pass

            client = self._make_client()
            self._session_clients[key] = (client, now)
            return client

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
            return response
        except Exception:
            with self._metrics_lock:
                self._metrics.errors += 1
                self._metrics.new_connections += 1
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
            client, method, url, content, headers, self._metrics, self._metrics_lock
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
            return self._metrics.to_dict()

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
    ) -> None:
        self._ctx = client.stream(method, url, content=content, headers=headers)
        self._metrics = metrics
        self._lock = lock
        self._response: Optional[httpx.Response] = None

    def __enter__(self) -> httpx.Response:
        self._response = self._ctx.__enter__()
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

    def __exit__(self, *args) -> None:
        self._ctx.__exit__(*args)


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
