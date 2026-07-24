"""
TokenPak Proxy Server — Modular Architecture

HTTP proxy server for LLM API traffic. Routes requests through the
tokenpak pipeline (vault injection, cost tracking, compression) based
on per-route policy (Claude Code byte-preserved, OpenClaw full pipeline,
SDK sanitized).

This is the canonical modular proxy server. The monolith at repo root
(proxy.py) is being incrementally decomposed into this module tree.

Env vars (all optional):
    TOKENPAK_PORT          (default 8766)
    TOKENPAK_MODE          (default hybrid) — strict|hybrid|aggressive
    TOKENPAK_COMPACT       (default 1) — master on/off switch
    TOKENPAK_COMPACT_THRESHOLD_TOKENS (default 1500 in the balanced profile)
    TOKENPAK_DB            (default .tokenpak/monitor.db)
    NOTIFY_SOCKET          systemd sd_notify socket path (set by systemd, not TokenPak)

See tokenpak/proxy/route_policy.py for the per-route behavior matrix.
"""

from __future__ import annotations

__all__ = (
    "CLAUDE_CODE_HEADER_ALLOWLIST",
    "CacheMetrics",
    "CompressionStats",
    "ConnectionPool",
    "DegradationEventType",
    "ExportAPI",
    "FilterParams",
    "ForwardProxyHandler",
    "GracefulShutdown",
    "INTERCEPT_HOSTS",
    "LEGACY_HEADER_ALLOWLIST",
    "MAX_UPSTREAM_RETRIES",
    "PassthroughConfig",
    "PipelineTrace",
    "PoolConfig",
    "ProviderRouter",
    "ProxyServer",
    "RequestStats",
    "SessionFilter",
    "StageTrace",
    "TraceStorage",
    "UpstreamRetryPolicy",
    "UpstreamTruncatedJSONError",
    "auto_detect_upstream",
    "build_terminal_recovery_payload",
    "detect_platform",
    "estimate_cost",
    "extract_sse_tokens",
    "extract_tip_plan_id",
    "format_startup_report",
    "forward_headers",
    "get_circuit_breaker_registry",
    "get_degradation_tracker",
    "get_policy",
    "get_rate_limit_registry",
    "get_stats_footer_enabled",
    "get_upstream_inflight_snapshot",
    "log_request",
    "main",
    "normalize_upstream_error",
    "persist_failed_request_metadata",
    "provider_from_url",
    "render_footer_oneline",
    "response_has_truncated_json",
    "run_startup_checks",
    "start_proxy",
    "validate_auth",
)


import gzip
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, TypedDict, cast
from urllib.parse import urlparse

import httpx

from tokenpak import __version__ as _tokenpak_version
from tokenpak import _paths  # scoped-home path resolver (honors TOKENPAK_HOME)
from tokenpak.cache.telemetry import CacheMetrics
from tokenpak.cache.telemetry import get_collector as _get_cache_collector
from tokenpak.core.config import get_stats_footer_enabled
from tokenpak.dashboard.export_api import ExportAPI
from tokenpak.dashboard.session_filter import (
    FilterParams,
    SessionFilter,
)
from tokenpak.proxy.monitor import Monitor as _DbMonitor
from tokenpak.sdk.registry import detect_platform
from tokenpak.telemetry.collector import RequestStats
from tokenpak.telemetry.footer import render_footer_oneline
from tokenpak.telemetry.monitoring.request_logger import log_request
from tokenpak.telemetry.monitoring.request_logger import new_request_id as _new_request_id

from .capsule_integration import RequestHook
from .circuit_breaker import (
    get_circuit_breaker_registry,
    get_rate_limit_registry,
    provider_from_url,
)
from .connection_pool import ConnectionPool, PoolConfig
from .creds_injection import maybe_inject as _creds_router_inject
from .degradation import DegradationEventType, get_degradation_tracker
from .error_response import normalize_upstream_error
from .headers import (
    CLAUDE_CODE_HEADER_ALLOWLIST,
    forward_headers,
)
from .memory_guard import create_memory_guard as _create_memory_guard
from .memory_guard import memory_guard_configuration_status as _memory_guard_configuration_status
from .passthrough import (
    LEGACY_HEADER_ALLOWLIST,
    PassthroughConfig,
    _classify_route,
    validate_auth,
)
from .proxy_auth import (
    check_proxy_auth as _check_proxy_auth,
)
from .proxy_auth import (
    strip_proxy_auth_for_upstream as _strip_proxy_auth_for_upstream,
)
from .route_policy import get_policy
from .router import INTERCEPT_HOSTS, ProviderRouter, estimate_cost
from .startup import format_startup_report, run_startup_checks
from .stats import CompressionStats
from .streaming import _extract_sse_stop_reason, extract_sse_tokens
from .upstream_retry import (
    UpstreamRetryPolicy,
    UpstreamTruncatedJSONError,
    build_terminal_recovery_payload,
    extract_tip_plan_id,
    persist_failed_request_metadata,
    response_has_truncated_json,
)

if TYPE_CHECKING:
    from tokenpak.proxy.admission import AgentConcurrencyGate


class _CodexCredentialsCache(TypedDict):
    mtime: float
    access_token: str
    account_id: str


class _SessionState(TypedDict):
    requests: int
    input_tokens: int
    sent_input_tokens: int
    saved_tokens: int
    protected_tokens: int
    output_tokens: int
    cost: float
    cost_saved: float
    errors: int
    start_time: float
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_read_client: int
    cache_read_proxy: int
    cache_read_unknown: int
    ingest_entries: int


# ---------------------------------------------------------------------------
# Upstream retry configuration.
#
# Retry behavior is factored into UpstreamRetryPolicy so the streaming and
# non-streaming paths share the same transient-error, deterministic-mode, and
# Retry-After rules.
# ---------------------------------------------------------------------------

# Deprecated compatibility alias — import compatibility only. Retry behavior
# is governed by UpstreamRetryPolicy, which reads TOKENPAK_UPSTREAM_RETRIES
# itself (the supported operator control); mutating this constant has no
# effect. It mirrors the policy's parse of TOKENPAK_UPSTREAM_RETRIES at
# import time and will be removed in a future minor release.
try:
    MAX_UPSTREAM_RETRIES: int = max(1, int(os.environ.get("TOKENPAK_UPSTREAM_RETRIES", "3")))
except ValueError:
    MAX_UPSTREAM_RETRIES = 3

# ---------------------------------------------------------------------------
# Per-(provider, session) outbound concurrency limiter.
#
# With session-isolated upstream clients, each session holds its own HTTP/2
# connection to the provider, so each session gets its own independent
# concurrency slot. This semaphore now caps parallelism *within* each
# session (mirroring the native-CLI topology where one CLI process talks
# on one connection, which typically allows a small number of multiplexed
# streams). Keying by (provider, session_key) prevents session A's burst
# from blocking session B.
# ---------------------------------------------------------------------------
_UPSTREAM_CONCURRENCY: int = int(os.environ.get("TOKENPAK_UPSTREAM_CONCURRENCY", "3"))
_UPSTREAM_ACQUIRE_TIMEOUT: float = float(os.environ.get("TOKENPAK_UPSTREAM_ACQUIRE_TIMEOUT", "30"))

import threading as _threading

_upstream_semaphores: dict[tuple[str, str], _threading.BoundedSemaphore] = {}
_upstream_sem_lock = _threading.Lock()
_upstream_inflight: dict[tuple[str, str], int] = {}
# Last touch (fetch or counter change) per key — drives idle eviction below.
_upstream_sem_last_activity: dict[tuple[str, str], float] = {}

# How long a (provider, session) entry must sit at zero in-flight with no
# activity before its semaphore object may be evicted. Evicting at the moment
# the counter hits zero is racy: acquisition is fetch-semaphore → acquire →
# increment (not atomic), so a release-to-zero eviction between another
# thread's fetch and acquire leaves that thread gating on the orphaned
# semaphore while the next request mints a fresh one for the same key —
# allowing up to 2x the per-(provider, session) concurrency cap. Deferring
# eviction until the entry has been idle far longer than any fetch→acquire
# window (bounded by the acquire timeout) removes the race in practice while
# still bounding memory growth from high-churn ip:port-derived session keys.
_UPSTREAM_SEM_IDLE_EVICT_SECONDS: float = max(
    float(os.environ.get("TOKENPAK_UPSTREAM_SEM_IDLE_EVICT_SECS", "600")),
    2.0 * _UPSTREAM_ACQUIRE_TIMEOUT,
)


def _get_upstream_semaphore(
    provider: str, session_key: str | None = None
) -> _threading.BoundedSemaphore:
    """Return (lazily creating) the per-(provider, session) concurrency semaphore.

    When session_key is None, falls back to a single shared semaphore per
    provider (legacy behavior) — used for callers that haven't opted into
    session isolation.
    """
    key = (provider or "_unknown", session_key or "_shared")
    with _upstream_sem_lock:
        sem = _upstream_semaphores.get(key)
        if sem is None:
            sem = _threading.BoundedSemaphore(_UPSTREAM_CONCURRENCY)
            _upstream_semaphores[key] = sem
            _upstream_inflight.setdefault(key, 0)
        _upstream_sem_last_activity[key] = time.monotonic()
    return sem


def _evict_idle_upstream_entries_locked(now: float) -> None:
    """Evict zero-in-flight entries idle past the eviction window.

    Caller must hold ``_upstream_sem_lock``.
    """
    cutoff = now - _UPSTREAM_SEM_IDLE_EVICT_SECONDS
    for key in [
        k
        for k, last in _upstream_sem_last_activity.items()
        if last < cutoff and _upstream_inflight.get(k, 0) <= 0
    ]:
        _upstream_sem_last_activity.pop(key, None)
        _upstream_inflight.pop(key, None)
        _upstream_semaphores.pop(key, None)


def _upstream_inflight_delta(provider: str, delta: int, session_key: str | None = None) -> int:
    """Adjust and return the in-flight counter for (provider, session).

    The semaphore object is deliberately NOT evicted the moment its counter
    returns to zero — that raced with acquisition (see the eviction-window
    comment above) and allowed 2x the concurrency cap. Instead, zero-count
    entries are swept only after _UPSTREAM_SEM_IDLE_EVICT_SECONDS without
    activity, which still reclaims the memory that high-churn ip:port session
    keys would otherwise leak (entries are recreated lazily on the next
    request for the same key).
    """
    key = (provider or "_unknown", session_key or "_shared")
    now = time.monotonic()
    with _upstream_sem_lock:
        count = max(0, _upstream_inflight.get(key, 0) + delta)
        _upstream_inflight[key] = count
        _upstream_sem_last_activity[key] = now
        if delta < 0 and count == 0:
            _evict_idle_upstream_entries_locked(now)
        return count


def get_upstream_inflight_snapshot() -> dict[str, int]:
    """Return a snapshot of current in-flight counts, for /health exposure.

    Keyed as ``"<provider>::<session>"`` for JSON-friendliness. The
    legacy shared path appears under ``"<provider>::_shared"``. Zero-count
    entries (kept alive briefly for the eviction-window guarantee above)
    are omitted — the snapshot reports actual in-flight work.
    """
    with _upstream_sem_lock:
        return {f"{prov}::{sess}": n for (prov, sess), n in _upstream_inflight.items() if n > 0}


# ---------------------------------------------------------------------------
# Circuit-breaker outcome classification
# ---------------------------------------------------------------------------

# Upstream statuses that count as provider failures for the provider circuit
# breaker: the retryable gateway statuses (502/503/504) plus 500 and 529
# (provider "overloaded") — all provider-side failure signals even when the
# HTTP exchange itself completes without an exception. 429 is deliberately
# EXCLUDED: rate limiting feeds the separate rate-limit circuit breaker.
_CB_FAILURE_STATUSES = frozenset({500, 502, 503, 504, 529})


def _cb_status_is_provider_failure(status_code: int | None) -> bool:
    """True when a completed exchange's final status is a provider failure."""
    return status_code in _CB_FAILURE_STATUSES


def _is_client_disconnect_error(exc: BaseException) -> bool:
    """True for socket errors from OUR downstream client, not the provider.

    Upstream I/O failures surface wrapped in httpx exception types, so a raw
    ``BrokenPipeError``/``ConnectionResetError`` escaping the handler comes
    from writing to ``self.wfile`` — i.e. the CLI/client vanished
    mid-response. The provider did nothing wrong; these must not count
    against the provider circuit breaker.
    """
    return isinstance(exc, (BrokenPipeError, ConnectionResetError))


# ---------------------------------------------------------------------------
# Codex OAuth credentials — read from ~/.codex/auth.json (cached, file-mtime-based)
# ---------------------------------------------------------------------------

_CODEX_AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
_CODEX_CREDS_CACHE: _CodexCredentialsCache = {
    "mtime": 0.0,
    "access_token": "",
    "account_id": "",
}
_CODEX_CREDS_LOCK = _threading.Lock()


def _load_codex_credentials() -> tuple[str, str]:
    """Load Codex OAuth token from ~/.codex/auth.json (file-mtime cached)."""
    try:
        st = os.stat(_CODEX_AUTH_PATH)
    except OSError:
        return "", ""
    with _CODEX_CREDS_LOCK:
        if st.st_mtime != _CODEX_CREDS_CACHE["mtime"]:
            try:
                with open(_CODEX_AUTH_PATH) as f:
                    data = json.load(f)
                tokens = data.get("tokens", {}) if isinstance(data, dict) else {}
                access_token = tokens.get("access_token", "")
                account_id = tokens.get("account_id", "")
                _CODEX_CREDS_CACHE["access_token"] = (
                    access_token if isinstance(access_token, str) else ""
                )
                _CODEX_CREDS_CACHE["account_id"] = account_id if isinstance(account_id, str) else ""
                _CODEX_CREDS_CACHE["mtime"] = st.st_mtime
            except (OSError, ValueError):
                return "", ""
        return _CODEX_CREDS_CACHE["access_token"], _CODEX_CREDS_CACHE["account_id"]


# ---------------------------------------------------------------------------
# Systemd integration — read sd_notify socket path from environment
# Transferred from monolith (TPK-CONSOLIDATION-A2a, lines 7577/7601)
# ---------------------------------------------------------------------------
_SD_NOTIFY_SOCKET: str = os.environ.get("NOTIFY_SOCKET", "")

# ---------------------------------------------------------------------------
# Pipeline trace types
# ---------------------------------------------------------------------------


@dataclass
class StageTrace:
    """Trace for a single pipeline stage."""

    name: str
    enabled: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_delta: int = 0
    duration_ms: float = 0.0
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass
class PipelineTrace:
    """Complete trace for a single request through the pipeline."""

    request_id: str
    timestamp: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_saved: int = 0
    cost_saved: float = 0.0
    total_cost: float = 0.0
    duration_ms: float = 0.0
    stages: list[StageTrace] = field(default_factory=list)
    status: str = "pending"

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["stages"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.stages]
        return cast(dict[str, object], d)


class TraceStorage:
    """Thread-safe storage for recent pipeline traces."""

    def __init__(self, max_traces: int = 10) -> None:
        self._traces: deque[PipelineTrace] = deque(maxlen=max_traces)
        self._lock = threading.Lock()
        self._by_id: dict[str, PipelineTrace] = {}

    def store(self, trace: PipelineTrace) -> None:
        with self._lock:
            self._traces.append(trace)
            self._by_id[trace.request_id] = trace
            if len(self._by_id) > len(self._traces) * 2:
                valid_ids = {t.request_id for t in self._traces}
                self._by_id = {k: v for k, v in self._by_id.items() if k in valid_ids}

    def get_last(self) -> PipelineTrace | None:
        with self._lock:
            return self._traces[-1] if self._traces else None

    def get_by_id(self, request_id: str) -> PipelineTrace | None:
        with self._lock:
            return self._by_id.get(request_id)

    def get_all(self) -> list[PipelineTrace]:
        with self._lock:
            return list(self._traces)


# ---------------------------------------------------------------------------
# Graceful shutdown manager
# ---------------------------------------------------------------------------


class GracefulShutdown:
    """
    Coordinates graceful shutdown for the proxy.

    Lifecycle
    ---------
    1. ``begin()``          — signal that shutdown has started (new requests → 503)
    2. ``track_request()``  — context manager: increment/decrement in-flight counter
    3. ``wait_for_drain()`` — block until all in-flight requests finish or timeout
    """

    def __init__(self) -> None:
        self._shutting_down: bool = False
        self._in_flight: int = 0
        self._lock = threading.Lock()
        self._all_done = threading.Event()
        self._all_done.set()  # starts "done" (no requests in flight)

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def begin(self) -> None:
        """Mark the start of shutdown. New requests will receive 503."""
        with self._lock:
            self._shutting_down = True

    @contextmanager
    def track_request(self) -> Iterator[None]:
        """Context manager that increments/decrements the in-flight counter."""
        with self._lock:
            self._in_flight += 1
            self._all_done.clear()
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._all_done.set()

    def in_flight_count(self) -> int:
        with self._lock:
            return self._in_flight

    def wait_for_drain(self, timeout: float = 30.0) -> bool:
        """
        Block until all in-flight requests complete or *timeout* seconds elapse.

        Returns True if drained cleanly, False if timed out.
        """
        return self._all_done.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


def _new_session() -> _SessionState:
    return {
        "requests": 0,
        "input_tokens": 0,
        "sent_input_tokens": 0,
        "saved_tokens": 0,
        "protected_tokens": 0,
        "output_tokens": 0,
        "cost": 0.0,
        "cost_saved": 0.0,
        "errors": 0,
        "start_time": time.time(),
        # Anthropic prompt caching stats
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        # Per-origin cache attribution — who placed the cache_control markers.
        #   client  = byte-preserved passthrough; upstream client (CC/Codex/etc.) owns them
        #   proxy   = proxy modified the body; tokenpak owns them
        #   unknown = attribution signal unavailable; treated as client for display
        "cache_read_client": 0,
        "cache_read_proxy": 0,
        "cache_read_unknown": 0,
        "ingest_entries": 0,
    }


# ---------------------------------------------------------------------------
# Request latency tracking (rolling window, used by /v1/messages/forecast)
# Shared with forecast_endpoint module so both reads and writes use same buffer.
# ---------------------------------------------------------------------------
from tokenpak.proxy.forecast_endpoint import (  # noqa: E402
    _forecast_latencies,
    _forecast_latency_lock,
)


def _is_app_endpoint_path(path: str) -> bool:
    """True for proxy-owned app API namespaces that must never fall through."""
    return path.startswith("/tpk/v1/") or path.startswith("/pak/v1/")


# ---------------------------------------------------------------------------
# Threaded HTTP server
# ---------------------------------------------------------------------------

# The overload-rejection body is built exactly once; the Content-Length header
# is computed from the actual body bytes so the wire framing is always exact.
_ADMISSION_REJECT_BODY = b'{"error":"managed_admission_capacity"}'
_ADMISSION_REJECT_RESPONSE = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: " + str(len(_ADMISSION_REJECT_BODY)).encode("ascii") + b"\r\n"
    b"Connection: close\r\n"
    b"\r\n" + _ADMISSION_REJECT_BODY
)

# Bounds for the pre-worker request-head peek. A model request's header block
# must fit within _ADMISSION_PEEK_MAX_BYTES and finish arriving within
# _ADMISSION_PEEK_DEADLINE_S of the first peek; otherwise the request is
# classified on whatever bytes arrived in time. A marker that does not arrive
# within the bounds classifies the request as unmarked — the same handling as
# any request without a marker (no admission lease, normal worker path).
_ADMISSION_FIRST_PEEK_TIMEOUT_S = 0.25
_ADMISSION_PEEK_DEADLINE_S = 1.0
_ADMISSION_PEEK_MAX_BYTES = 8192
_ADMISSION_PEEK_POLL_S = 0.005


class _ThreadedHTTPServer(HTTPServer):
    """HTTP server that dispatches each request to a daemon thread."""

    proxy_server: "ProxyServer"  # injected after construction

    def _peek_request_head(self, request: socket.socket) -> bytes:
        """Peek the request head without consuming bytes, under explicit bounds.

        For model-endpoint paths the peek continues until the end of the
        header block is visible, because a classification marker can arrive in
        a later TCP segment than the request line. Both an explicit byte bound
        and an explicit wall-time bound apply, so this never blocks
        indefinitely. On bound exhaustion the partial head is returned and the
        caller classifies the request on what actually arrived.
        """
        deadline = time.monotonic() + _ADMISSION_PEEK_DEADLINE_S
        peeked = b""
        try:
            request.settimeout(_ADMISSION_FIRST_PEEK_TIMEOUT_S)
            try:
                peeked = request.recv(_ADMISSION_PEEK_MAX_BYTES, socket.MSG_PEEK)
            except (OSError, ValueError):
                return b""
            if not peeked:
                return b""
            first_line = peeked.split(b"\r\n", 1)[0]
            parts = first_line.split(b" ", 2)
            path = parts[1].decode("latin-1", "ignore") if len(parts) > 1 else ""
            if not path.startswith("/v1/messages"):
                # Only model-endpoint requests are classified from the full
                # header block; everything else needs just the request line.
                return peeked
            while (
                b"\r\n\r\n" not in peeked
                and len(peeked) < _ADMISSION_PEEK_MAX_BYTES
                and time.monotonic() < deadline
            ):
                try:
                    chunk = request.recv(_ADMISSION_PEEK_MAX_BYTES, socket.MSG_PEEK)
                except (OSError, ValueError):
                    break
                if not chunk:
                    break  # peer closed — nothing more will arrive
                if len(chunk) <= len(peeked):
                    # No new bytes yet — wait a bounded interval before the
                    # next peek so the loop never busy-spins.
                    time.sleep(_ADMISSION_PEEK_POLL_S)
                peeked = chunk
        finally:
            try:
                request.settimeout(None)
            except (OSError, ValueError):
                pass
        return peeked

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: object,
    ) -> None:
        # Keep control-plane endpoints responsive while bounding model traffic.
        # Admission happens before a worker thread is created, so overload cannot
        # turn into an unbounded thread/socket population.
        accepted_socket = cast(socket.socket, request)
        accepted_address = cast(tuple[str, int], client_address)
        peeked = self._peek_request_head(accepted_socket)
        first_line = peeked.split(b"\r\n", 1)[0]
        parts = first_line.split(b" ", 2)
        path = parts[1].decode("latin-1", "ignore") if len(parts) > 1 else ""
        control = (
            path == "/health"
            or path.startswith("/health?")
            or path in {"/status", "/metrics"}
            or path.startswith("/tpk/v1/")
            or path.startswith("/pak/v1/")
        )
        managed = False
        if not control and path.startswith("/v1/messages"):
            from tokenpak.proxy.spend_guard.classifier import MANAGED, classify

            headers = {}
            for line in peeked.split(b"\r\n")[1:]:
                if not line:
                    break
                key, sep, value = line.partition(b":")
                if sep:
                    headers[key.decode("latin-1")] = value.decode("latin-1").strip()
            managed = classify(headers).request_class == MANAGED
        admitted = managed and self.proxy_server._admission.acquire(blocking=False)
        if managed and not admitted:
            try:
                accepted_socket.sendall(_ADMISSION_REJECT_RESPONSE)
            finally:
                self.shutdown_request(accepted_socket)
            self.proxy_server._admission_rejected += 1
            return
        try:
            t = threading.Thread(
                target=self._handle,
                args=(accepted_socket, accepted_address, managed),
            )
            t.daemon = True
            t.start()
        except Exception:
            # Worker creation failed after the admission decision: release the
            # lease (if one was acquired) and close the accepted socket so
            # neither leaks. Re-raise so the serving loop records the error.
            if admitted:
                self.proxy_server._admission.release()
            self.shutdown_request(accepted_socket)
            raise

    def _handle(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
        managed: bool = False,
    ) -> None:
        # The listener admission lease above bounds total held managed
        # connections; this per-connection gate (already inside its own
        # worker thread, so blocking it never stalls the accept loop or
        # control-plane traffic) further bounds how many run in parallel,
        # queueing the rest FIFO with a bounded wait.
        gate = self.proxy_server._agent_gate if managed else None
        gate_admitted = False
        try:
            if gate is not None:
                from tokenpak.proxy.admission import ADMITTED, build_busy_response

                outcome = gate.acquire()
                if outcome != ADMITTED:
                    request.sendall(build_busy_response(outcome))
                    return
                gate_admitted = True
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            if gate_admitted and gate is not None:
                gate.release()
            if managed:
                self.proxy_server._admission.release()


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _ProxyHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for the TokenPak proxy.

    Attributes injected by ProxyServer before serving:
        server.proxy_server  — back-reference to the ProxyServer instance
    """

    @property
    def _ps(self) -> "ProxyServer":
        """Typed accessor for the back-reference to ProxyServer."""
        return cast(_ThreadedHTTPServer, self.server).proxy_server

    def log_message(self, format: str, *args: object) -> None:  # silence access log
        pass

    # ------------------------------------------------------------------
    # Proxy-level auth gate (P0-06 / A6)
    # ------------------------------------------------------------------

    def _enforce_proxy_auth(self) -> bool:
        """Return True iff the request may proceed past the proxy auth gate.

        On deny, sends the 401/403 response itself and returns False — the
        verb handler must return immediately. On allow via the Bearer path,
        sets ``self._tokenpak_user_id`` (SHA-256 hex of the supplied token)
        and ``self._tokenpak_proxy_auth_header`` (the raw client header
        value) for downstream telemetry + I5 stripping.
        """
        client_ip = self.client_address[0] if self.client_address else ""
        decision = _check_proxy_auth(client_ip, self.headers)
        # Always initialise the slots so downstream code can rely on them.
        if not hasattr(self, "_tokenpak_user_id"):
            self._tokenpak_user_id: str | None = None
        if not hasattr(self, "_tokenpak_proxy_auth_header"):
            self._tokenpak_proxy_auth_header: str | None = None
        if not decision.allowed:
            try:
                self.send_response(decision.status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(decision.error_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(decision.error_body)
            except Exception:
                pass
            return False
        if decision.user_id_hash:
            self._tokenpak_user_id = decision.user_id_hash
            # Capture the raw Authorization value so the upstream-forwarding
            # path can strip exactly that string (I5 invariant).
            for k, v in self.headers.items():
                if isinstance(k, str) and k.lower() == "authorization":
                    self._tokenpak_proxy_auth_header = v
                    break
        return True

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        # Override the stdlib HTML error page. Every client hitting this
        # proxy is expecting an API-style JSON error, and leaking the
        # default HTML to a chat bot or SDK produces garbage output.
        try:
            short, long = self.responses.get(code, ("Unknown", "Unknown"))
        except Exception:
            short, long = "Error", ""
        body = json.dumps(
            {
                "error": {
                    "type": "proxy_error",
                    "code": code,
                    "message": message or short,
                    "detail": explain or long,
                }
            }
        ).encode("utf-8")
        try:
            self.send_response(code, message or short)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD" and code >= 200 and code not in (204, 304):
                self.wfile.write(body)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # CONNECT tunnelling (HTTPS MITM passthrough)
    # ------------------------------------------------------------------

    def do_CONNECT(self) -> None:
        if not self._enforce_proxy_auth():
            return
        host, _, port_str = self.path.partition(":")
        port = int(port_str) if port_str else 443
        self._tunnel(host, port)

    def _tunnel(self, host: str, port: int) -> None:
        try:
            remote = socket.create_connection((host, port), timeout=30)
        except Exception as e:
            self.send_error(502, f"Cannot connect to {host}:{port}: {e}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.connection.setblocking(False)
        remote.setblocking(False)
        last_activity = time.time()
        while time.time() - last_activity < 120:
            moved = False
            for src, dst in ((self.connection, remote), (remote, self.connection)):
                try:
                    data = src.recv(65536)
                    if data:
                        dst.sendall(data)
                        last_activity = time.time()
                        moved = True
                    elif data == b"":
                        remote.close()
                        return
                except BlockingIOError:
                    pass
                except Exception:
                    remote.close()
                    return
            if not moved:
                time.sleep(0.01)
        remote.close()

    # ------------------------------------------------------------------
    # HTTP verbs
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._enforce_proxy_auth():
            return
        ps = self._ps
        path = self.path

        # App-level /tpk/v1/* endpoints — proxy-owned resources (vault, budget,
        # journal, …) exposed over REST so the companion + external tools can
        # consume them without reaching into the Python package.
        # Registered BEFORE health so shutdown doesn't steal /tpk/v1/health.
        try:
            from tokenpak.proxy.app_endpoints import try_handle_get as _tp_try_get

            if _tp_try_get(self):
                return
        except Exception as _exc:
            import sys as _sys

            print(f"[tokenpak] app endpoint dispatch error: {_exc}", file=_sys.stderr)
            if _is_app_endpoint_path(path):
                self._send_app_endpoint_dispatch_error(_exc)
                return

        # Always allow /health during shutdown (needed for health-check polling)
        if path == "/health" or path.startswith("/health?"):
            from urllib.parse import parse_qs
            from urllib.parse import urlparse as _urlparse

            parsed_path = _urlparse(path)
            health_query = parse_qs(parsed_path.query)
            deep = health_query.get("deep", ["false"])[0].lower() in ("true", "1", "yes")
            self._send_json(ps.health(deep=deep))
            return

        if path == "/status":
            self._send_json(ps.status())
            return

        # Reject new proxied requests while shutting down
        if ps.shutdown.is_shutting_down and path.startswith("http"):
            self._send_503_shutdown()
            return
        if path == "/metrics":
            from tokenpak.telemetry.monitoring.metrics import ProxyMetricsCollector

            collector = ProxyMetricsCollector(proxy_server=ps)
            body = collector.collect().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/metrics/dashboard":
            self._handle_metrics_dashboard()
            return
        if path.startswith("/dashboard"):
            # Serve dashboard UI files. Strip query strings before filesystem
            # lookup so `/dashboard?mode=cli` still serves the local dashboard
            # shell; the page reads the mode query parameter client-side.
            import asyncio
            from urllib.parse import parse_qs
            from urllib.parse import urlparse as _urlparse

            from tokenpak.dashboard import CCI09_DASHBOARD_MODES, serve_dashboard_file

            dashboard_request = _urlparse(path)
            mode = parse_qs(dashboard_request.query).get("mode", [None])[0]
            if mode and mode not in CCI09_DASHBOARD_MODES:
                self.send_error(404)
                return

            route_path = dashboard_request.path
            dashboard_path = route_path[10:]  # Remove '/dashboard' prefix
            if not dashboard_path:
                dashboard_path = "/"

            # Serve the file
            result = asyncio.run(serve_dashboard_file(dashboard_path))
            if result:
                content, mime_type = result
                body = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", mime_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)
            return
        if path == "/degradation":
            from .degradation import get_degradation_tracker

            self._send_json(get_degradation_tracker().summary())
            return
        if path == "/circuit-breakers":
            registry = get_circuit_breaker_registry()
            try:
                _sess_pool_info = ps._connection_pool.session_client_snapshot()
            except Exception:
                _sess_pool_info = {"count": 0, "cap": 0, "idle_secs": 0.0, "keys": []}
            self._send_json(
                {
                    "enabled": registry.enabled,
                    "circuit_breakers": registry.all_statuses(),
                    "upstream_concurrency": {
                        "limit_per_provider": _UPSTREAM_CONCURRENCY,
                        "acquire_timeout_seconds": _UPSTREAM_ACQUIRE_TIMEOUT,
                        "in_flight": get_upstream_inflight_snapshot(),
                    },
                    "session_client_pool": _sess_pool_info,
                }
            )
            return
        if path == "/stats":
            self._send_json(ps.stats())
            return
        if path == "/stats/last":
            self._send_json(ps.last_request_stats())
            return
        if path == "/stats/session":
            self._send_json(ps.session_stats())
            return
        if path == "/cache-stats":
            self._send_json(_get_cache_collector().summary())
            return
        if path == "/api/goals":
            # Get all goals with progress
            from tokenpak.cli.goals import GoalManager

            try:
                manager = GoalManager()
                goals = manager.list_goals()
                response = {}
                for goal in goals:
                    progress = manager.get_progress(goal.goal_id)
                    if progress:
                        response[goal.goal_id] = {
                            "goal": goal.to_dict(),
                            "progress": progress.to_dict(),
                        }
                self._send_json(response)
            except Exception as e:
                self._send_json({"error": str(e)})
            return
        if path == "/traces":
            traces = ps.trace_storage.get_all()
            self._send_json({"traces": [t.to_dict() for t in traces], "count": len(traces)})
            return
        if path == "/trace/last":
            trace = ps.trace_storage.get_last()
            if trace:
                self._send_json(trace.to_dict())
            else:
                self._send_json({"error": "no_traces"})
            return
        if path.startswith("/trace/"):
            rid = path.split("/trace/", 1)[1]
            trace = ps.trace_storage.get_by_id(rid)
            if trace:
                self._send_json(trace.to_dict())
            else:
                self._send_json({"error": "not_found", "request_id": rid})
            return
        if path.startswith("/v1/sessions"):
            # Session filter + pagination endpoint
            # GET /v1/sessions?model=&from=&to=&status=&limit=&offset=
            session_query = ""
            if "?" in path:
                _, session_query = path.split("?", 1)
            ps = self._ps
            try:
                params = FilterParams.from_query_string(session_query)
            except (ValueError, TypeError) as exc:
                err = json.dumps({"error": "invalid_params", "detail": str(exc)}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(err)
                return
            sf = ps.session_filter
            session_result = sf.query(params)
            models = sf.distinct_models()
            session_result["models"] = models
            self._send_json(session_result)
            return
        if path.startswith("/v1/models"):
            route = ps.router.route(path, dict(self.headers))
            if route.auth_type == "oauth":
                # The OpenAI /v1/models endpoint requires API-platform scope,
                # which a Codex subscription OAuth session does not carry.
                # Native Codex already has a bundled model catalog, so report
                # that this upstream source contributed no additional models
                # instead of forwarding the bearer to an incompatible API-key
                # endpoint and surfacing a misleading 403.
                self._send_json({"models": []})
                return
            self._proxy_to(route.full_url, "GET")
            return
        if path.startswith("http"):
            self._proxy_to(path, "GET")
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if not self._enforce_proxy_auth():
            return
        ps = self._ps

        # App-level /tpk/v1/* POST endpoints — reserved for future compress,
        # optimize, budget event, journal write, etc. See app_endpoints.py.
        try:
            from tokenpak.proxy.app_endpoints import try_handle_post as _tp_try_post

            if _tp_try_post(self):
                return
        except Exception as _exc:
            import sys as _sys

            print(f"[tokenpak] app endpoint POST dispatch error: {_exc}", file=_sys.stderr)
            if _is_app_endpoint_path(self.path):
                self._send_app_endpoint_dispatch_error(_exc)
                return

        if ps.shutdown.is_shutting_down and (
            self.path.startswith("http") or self.path.startswith("/v1/")
        ):
            self._send_503_shutdown()
            return
        if self.path.startswith("http"):
            self._proxy_to(self.path, "POST")
        elif self.path == "/v1/export/csv":
            # CSV export endpoint — reads body, returns downloadable CSV
            ps = self._ps
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            traces = [t.to_dict() for t in ps.trace_storage.get_all()]
            stats = ps.session_stats()
            body, status, headers = ExportAPI.handle(
                raw_body=raw_body,
                traces=traces,
                session_stats=stats,
            )
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/ingest":
            import json as _json
            import uuid as _uuid

            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                _payload = _json.loads(raw_body)
            except Exception:
                _payload = {}
            _record_id = str(_uuid.uuid4())
            _resp = _json.dumps({"status": "ok", "ids": [_record_id]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_resp)))
            self.end_headers()
            self.wfile.write(_resp)
        elif self.path.split("?")[0] == "/v1/messages/count_tokens":
            self._handle_count_tokens()
        elif self.path.split("?")[0] == "/v1/messages/forecast":
            self._handle_cost_forecast()
        elif self.path.startswith("/v1/messages/"):
            # Default passthrough for unrecognised /v1/messages/* subpaths.
            # Forwards body + headers to upstream untouched (guards future Anthropic API additions).
            ps = self._ps
            route = ps.router.route(self.path, dict(self.headers))
            self._proxy_to(route.full_url, "POST")
        elif self.path.startswith("/v1/"):
            ps = self._ps
            route = ps.router.route(self.path, dict(self.headers))
            self._proxy_to(route.full_url, "POST")
        elif self.path.startswith("/codex/"):
            # ChatGPT Codex subscription backend. OpenClaw's
            # tokenpak-openai-codex provider posts to /codex/responses
            # with a placeholder bearer; _proxy_to_inner injects the real
            # JWT from ~/.codex/auth.json (see lines 1142-1160) and
            # rewrites the upstream to chatgpt.com/backend-api.
            self._proxy_to(f"https://chatgpt.com/backend-api{self.path}", "POST")
        else:
            self.send_error(404)

    def do_PUT(self) -> None:
        if not self._enforce_proxy_auth():
            return
        ps = self._ps
        if ps.shutdown.is_shutting_down and self.path.startswith("http"):
            self._send_503_shutdown()
            return
        if self.path.startswith("http"):
            self._proxy_to(self.path, "PUT")
        else:
            self.send_error(404)

    def do_DELETE(self) -> None:
        if not self._enforce_proxy_auth():
            return
        ps = self._ps
        if ps.shutdown.is_shutting_down and self.path.startswith("http"):
            self._send_503_shutdown()
            return
        if self.path.startswith("http"):
            self._proxy_to(self.path, "DELETE")
        else:
            self.send_error(404)

    def _send_503_shutdown(self) -> None:
        """Return 503 Service Unavailable during graceful shutdown drain."""
        body = json.dumps(
            {
                "error": {
                    "type": "service_unavailable",
                    "message": (
                        "TokenPak proxy is shutting down. "
                        "Please retry your request against a new proxy instance."
                    ),
                }
            }
        ).encode()
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Retry-After", "5")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # Core forwarding
    # ------------------------------------------------------------------

    def _proxy_to(self, target_url: str, method: str) -> None:
        ps = self._ps
        with ps.shutdown.track_request():
            self._proxy_to_inner(target_url, method)

    def _proxy_to_inner(self, target_url: str, method: str) -> None:
        t0 = time.time()
        # Request ID: honour X-Request-ID from client, else generate UUID
        _req_id = _new_request_id(dict(self.headers))
        ps = self._ps
        parsed = urlparse(target_url)

        should_log = any(h in target_url for h in INTERCEPT_HOSTS)
        is_model_request = any(
            endpoint in target_url for endpoint in ("/messages", "/chat/completions", "/responses")
        )

        content_length = int(self.headers.get("Content-Length", 0))
        body: bytes | None = self.rfile.read(content_length) if content_length > 0 else None
        _decoded_request_encoding = False
        if body:
            try:
                body, _decoded_request_encoding = _decode_request_entity(
                    body,
                    self.headers.get("Content-Encoding", ""),
                )
                if _decoded_request_encoding:
                    if self.headers.get("Content-Length") is not None:
                        self.headers.replace_header("Content-Length", str(len(body)))
                    for header_name in tuple(self.headers.keys()):
                        if header_name.lower() in {"content-encoding", "content-md5"}:
                            del self.headers[header_name]
            except ValueError:
                err_body = json.dumps(
                    {
                        "error": {
                            "type": "unsupported_request_encoding",
                            "message": (
                                "TokenPak could not decode the compressed request body. "
                                "Reinstall TokenPak or send identity, gzip, or zstd content."
                            ),
                        }
                    }
                ).encode()
                self.send_response(415)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
                return
        _original_body = body
        _retry_policy = UpstreamRetryPolicy.from_env(
            body=body,
            headers=dict(self.headers),
        )
        _tip_plan_id = extract_tip_plan_id(dict(self.headers), body, _req_id)

        model = "unknown"
        input_tokens = 0
        sent_input_tokens = 0
        protected_tokens = 0
        is_streaming = False
        cache_read_tokens = 0
        cache_creation_tokens = 0
        # Per-TTL prompt-cache attribution (additive telemetry; populated only
        # when the upstream response includes ``usage.cache_creation`` breakdown).
        cache_creation_1h_tokens = 0
        cache_creation_5m_tokens = 0
        # Provider stop_reason observed on the response path (read-only parse of
        # a response copy; forwarded bytes are never modified). '' = not observed.
        stop_reason = ""

        trace: PipelineTrace | None = None
        if should_log and is_model_request:
            trace = PipelineTrace(
                request_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )

        # ── Claude Code backend delegation ─────────────────────────────
        # X-TokenPak-Backend: claude-code → route through Claude Code CLI
        # instead of forwarding to Anthropic API. This uses subscription
        # billing (OAuth) instead of API keys.
        _backend_header = ""
        for _bk, _bv in self.headers.items():
            if _bk.lower() == "x-tokenpak-backend":
                _backend_header = _bv.strip().lower()
                break
        if _backend_header == "claude-code" and body:
            self._handle_claude_code_backend(body)
            return

        # Route classification and policy lookup
        _route = _classify_route(self.path, self.headers)
        _policy = get_policy(_route)
        _source_platform = _policy["platform_tag"]
        _is_byte_preserved = _policy.get("body") == "byte_preserved"

        # Platform adapter detection (feature-flagged via TOKENPAK_PLATFORM_ADAPTERS, default ON)
        _adapters_enabled = os.environ.get("TOKENPAK_PLATFORM_ADAPTERS", "1") != "0"
        if _adapters_enabled and should_log and is_model_request:
            import logging as _logging

            _platform = detect_platform()
            _logging.debug(
                "tokenpak.proxy: detected platform=%s for request to %s",
                _platform,
                target_url[:60],
            )

        # ── TIP Spend Guard: pre-send circuit breaker ─────────────────────────
        # Blocks risky requests BEFORE provider send. Returns structured
        # error.type=tokenpak_spend_guard_blocked (HTTP 402) when the policy
        # engine projects cost or token count above thresholds.
        # See tokenpak/proxy/spend_guard/ + standards/29-spend-guard-agent-contract.md
        # Disabled with: spend_guard.enabled=false in config.yaml
        #            or: TOKENPAK_SPEND_GUARD_ENABLED=0
        _sg_admission_ticket = None  # rolling-cap in-flight spend ticket
        if should_log and is_model_request and body:
            try:
                from tokenpak.proxy.request_pipeline import _resolve_session_id
                from tokenpak.proxy.spend_guard import evaluate as _sg_evaluate

                # Resolve model cheaply from the request body — full route
                # resolution happens later for the forward path.
                _sg_model = ""
                try:
                    _sg_body_json = json.loads(body.decode("utf-8", errors="replace"))
                    _sg_model = str(_sg_body_json.get("model") or "")
                except Exception:
                    pass
                # Empty-string fallback: the monitor.db row for this request
                # is written with the same resolver and an empty model
                # fallback, so the guard-side check and the recorded row
                # MUST agree on the session key for header-less traffic.
                # (A model-name pseudo-session summed zero recorded rows
                # forever, silently disabling session-cumulative caps.)
                _sg_session = _resolve_session_id(self.headers, "")
                _sg_outcome = _sg_evaluate(
                    body,
                    # Empty when the body carries no model. Pricing falls
                    # back to default-class rates (tokenpak.models.get_rates)
                    # without inventing a model id, and the context-window
                    # lookup falls back to cfg.block_tokens — so guard rows
                    # never record a fabricated model name.
                    _sg_model,
                    _sg_session,
                    dict(self.headers),
                )
                _sg_admission_ticket = getattr(_sg_outcome, "admission_ticket", None)
                # Forwarded outcomes update body for downstream pipeline.
                if _sg_outcome.kind in ("forward", "forward_modified"):
                    if _sg_outcome.body is not None:
                        body = _sg_outcome.body
                elif _sg_outcome.kind == "replay":
                    # Held request is being replayed — substitute body
                    # and headers, then continue down the normal forward path.
                    if _sg_outcome.body is not None:
                        body = _sg_outcome.body
                    if _sg_outcome.headers:
                        # Re-apply only the held request's NON-credential
                        # headers (content-type, api-version, etc.). Credential
                        # headers are never persisted to disk; the held
                        # request replays with the live approving request's own
                        # auth, already on self.headers — so we must not let a
                        # stale/absent stored value clobber it.
                        try:
                            from tokenpak.proxy.spend_guard.pending import (
                                redact_headers as _sg_redact,
                            )

                            _replay_hdrs = _sg_redact(_sg_outcome.headers)
                        except Exception:
                            _replay_hdrs = {}
                        for _hk, _hv in _replay_hdrs.items():
                            try:
                                self.headers[_hk] = _hv
                            except Exception:
                                pass
                else:
                    # block / hard_block / cancel / reprompt / estimate.
                    # Once the guard decided NOT to forward, a failure while
                    # writing the response to the client must never fall
                    # through to the forward path — hence the inner
                    # try/except with an unconditional return.
                    _sg_resp = _sg_outcome.response_body or b"{}"
                    try:
                        self.send_response(_sg_outcome.http_status or 402)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(_sg_resp)))
                        self.send_header("X-TokenPak-Spend-Guard", _sg_outcome.kind)
                        self.end_headers()
                        self.wfile.write(_sg_resp)
                    except Exception as _sg_wexc:
                        import logging as _sg_log

                        _sg_log.getLogger(__name__).warning(
                            "tokenpak.spend_guard: failed writing %s response "
                            "to client (%s: %s) — request stays blocked",
                            _sg_outcome.kind,
                            type(_sg_wexc).__name__,
                            _sg_wexc,
                        )
                    return
            except ImportError:
                pass  # spend guard not installed
            except Exception as _sg_exc:
                # The guard's own evaluate() converts internal evaluator
                # errors into fail-closed 402s, so an exception here is a
                # proxy-hook defect (header/session resolution, outcome
                # handling). Forwarding without the guard is deliberate for
                # that narrow case — but it must be LOUD, not a debug line.
                import logging as _sg_log

                _sg_log.getLogger(__name__).warning(
                    "tokenpak.spend_guard: unexpected proxy-hook error — "
                    "forwarding WITHOUT spend-guard evaluation: %s: %s",
                    type(_sg_exc).__name__,
                    _sg_exc,
                )

        # ── DLP outbound secret scan ──────────────────────────────────────────
        # Scans the raw request body for secrets before compression/forwarding.
        # Default: TOKENPAK_DLP_ENABLED=1, TOKENPAK_DLP_MODE=warn (log only).
        # Opt-out: TOKENPAK_DLP_ENABLED=0
        if (
            os.environ.get("TOKENPAK_DLP_ENABLED", "1") != "0"
            and should_log
            and is_model_request
            and body
        ):
            try:
                from tokenpak.security.dlp import DLPScanner

                _dlp = DLPScanner()
                _dlp_text = body.decode("utf-8", errors="replace")
                if _dlp.mode == "warn":
                    _dlp_findings = _dlp.scan(_dlp_text)
                    if _dlp_findings:
                        import logging as _dlp_log

                        _dlp_log.getLogger(__name__).warning(
                            "tokenpak.dlp: %d secret(s) detected in outbound request: %s"
                            " (set TOKENPAK_DLP_MODE=redact to auto-redact"
                            " or TOKENPAK_DLP_ENABLED=0 to disable)",
                            len(_dlp_findings),
                            ", ".join(f.rule_id for f in _dlp_findings),
                        )
                elif _dlp.mode == "redact":
                    _dlp_redacted = _dlp.redact(_dlp_text)
                    if _dlp_redacted != _dlp_text:
                        body = _dlp_redacted.encode("utf-8")
                elif _dlp.mode == "block":
                    if not _dlp.block_check(_dlp_text):
                        _dlp_findings = _dlp.scan(_dlp_text)
                        import logging as _dlp_log

                        _dlp_log.getLogger(__name__).warning(
                            "tokenpak.dlp: blocking request — %d secret(s) detected: %s",
                            len(_dlp_findings),
                            ", ".join(f.rule_id for f in _dlp_findings),
                        )
                        _dlp_err = json.dumps(
                            {
                                "error": {
                                    "type": "dlp_block",
                                    "message": (
                                        f"Request blocked by DLP scanner: "
                                        f"{len(_dlp_findings)} secret(s) detected in outbound "
                                        "prompt. Remove secrets before retrying."
                                    ),
                                    "rule_ids": [f.rule_id for f in _dlp_findings],
                                }
                            }
                        ).encode()
                        self.send_response(403)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(_dlp_err)))
                        self.end_headers()
                        self.wfile.write(_dlp_err)
                        return
            except ImportError:
                pass
            except Exception as _dlp_exc:
                import logging as _dlp_log

                _dlp_log.getLogger(__name__).debug(
                    "tokenpak.dlp: scan error (passthrough): %s: %s",
                    type(_dlp_exc).__name__,
                    _dlp_exc,
                )

        # Run compression pipeline hook if registered
        if should_log and is_model_request and body:
            try:
                route = ps.router.route(target_url, dict(self.headers), body)
                model = route.model
            except Exception:
                pass
            input_tokens = _estimate_tokens_from_body(body)

            # Observe-only optimization pipeline.
            # Pipeline composition lives in services/optimization/, which
            # owns all pipeline composition (a design invariant).
            # proxy/server.py only invokes it
            # over the byte-preserved request. Gated on
            # TOKENPAK_OPTIMIZATION_PIPELINE (default off); runs before
            # any body-mutating stage and never returns a different body
            # in observe-only mode. Trace is stashed locally for future
            # telemetry persistence.
            _optimization_trace = None
            try:
                from tokenpak.services.optimization import run_observe_only as _opt_run

                _body_before_opt = body
                body, _optimization_trace = _opt_run(
                    request_id=_req_id,
                    body=body,
                    method="POST",
                    path=self.path,
                    headers=dict(self.headers),
                    target_url=target_url,
                    platform=_source_platform,
                    route=_route,
                    policy=_policy,
                )
                # Defensive: assert the pipeline did not mutate the body.
                # Observe-only mode MUST be byte-identical.
                if body is not _body_before_opt and body != _body_before_opt:
                    import logging as _opt_log

                    _opt_log.getLogger(__name__).error(
                        "optimization.pipeline: observe-only mode mutated body "
                        "(in=%d out=%d) — restoring original",
                        len(_body_before_opt or b""),
                        len(body or b""),
                    )
                    body = _body_before_opt
            except Exception as _opt_exc:
                # Fail-open: optimization scaffolding must never break a request.
                import logging as _opt_log

                _opt_log.getLogger(__name__).debug(
                    "optimization.pipeline: skipped (%s: %s)",
                    type(_opt_exc).__name__,
                    _opt_exc,
                )

            # Cache invalidator detection (log-only).
            # Skip transparent mode — transparent must remain side-effect-free.
            # Runs on original (pre-compression) body so semantic fields are intact.
            if ps.compilation_mode != "transparent":
                try:
                    from tokenpak.proxy.cache_invalidator import (
                        _detect_cache_invalidators,
                        _get_session_cache,
                        _write_cache_invalidator_events,
                    )
                    from tokenpak.proxy.config import MONITOR_DB as _CI_DB
                    from tokenpak.proxy.request_pipeline import _resolve_session_id

                    _ci_session_id = _resolve_session_id(self.headers, model)
                    _ci_cache = _get_session_cache()
                    _ci_prev_body = _ci_cache.get(_ci_session_id)
                    if _ci_prev_body is not None:
                        _ci_events = _detect_cache_invalidators(_ci_prev_body, body)
                        if _ci_events:
                            _write_cache_invalidator_events(
                                _CI_DB, None, _ci_session_id, _ci_events
                            )
                    _ci_cache.put(_ci_session_id, body)
                except Exception:
                    pass  # fail-open: never break a request over telemetry

            if _is_byte_preserved:
                # Byte-preserved path (Claude Code): detect streaming from raw
                # bytes only — never json.loads/json.dumps the body.
                # Use the modular pipeline for vault injection (byte splice)
                # but skip compression entirely to preserve Anthropic billing routing.
                is_streaming = b'"stream":true' in body or b'"stream": true' in body
                try:
                    from tokenpak.proxy.pipeline import process_request as _pipeline_run
                    from tokenpak.proxy.request import ProxyRequest as _PReq
                    from tokenpak.proxy.request_pipeline import _resolve_session_id as _rsi

                    _pr = _PReq(
                        method="POST",
                        url=target_url,
                        headers=dict(self.headers),
                        body=body,
                        source_platform=_source_platform,
                        session_id=_rsi(self.headers, model),
                    )
                    _result = _pipeline_run(_pr, _policy, route=_route, client_has_auth=True)
                    body = _result.request.body
                except Exception:
                    pass  # fail-open: vault injection failure must never break a request
            else:
                # READ-ONLY parse — body bytes must NOT be reconstructed from this dict
                try:
                    data = json.loads(body)
                    is_streaming = data.get("stream", False)
                except Exception:
                    pass

            # Google streaming is signalled by URL, not body: path contains
            # streamGenerateContent or query param ?alt=sse.
            if not is_streaming and (
                "streamGenerateContent" in target_url or "alt=sse" in target_url
            ):
                is_streaming = True

            if ps.request_hook and not _is_byte_preserved:
                try:
                    body, sent_input_tokens, input_tokens, protected_tokens = ps.request_hook(
                        body, model, trace
                    )
                except Exception as hook_err:
                    # Graceful degradation: compression failed — forward original request unchanged.
                    # The user still gets a response; we log and track the event.
                    import logging as _logging

                    _logging.getLogger(__name__).warning(
                        "tokenpak: compression failed (passthrough mode active): %s: %s — "
                        "original request will be forwarded unchanged",
                        type(hook_err).__name__,
                        hook_err,
                    )
                    print(
                        f"  ⚠ Compression failed ({type(hook_err).__name__}): {hook_err}\n"
                        f"    → Forwarding original request (passthrough mode). "
                        f"Run `tokenpak doctor` for diagnostics."
                    )
                    get_degradation_tracker().record_compression_failure(hook_err)
                    sent_input_tokens = input_tokens
                    # body is unchanged (assignment failed, original value retained)

        if sent_input_tokens == 0:
            sent_input_tokens = input_tokens

        # ── Circuit breaker check ──────────────────────────────────────────
        # Fast-fail immediately if the target provider's circuit is OPEN.
        if should_log and is_model_request:
            _cb_provider = provider_from_url(target_url)
            _cb_registry = get_circuit_breaker_registry()
            if not _cb_registry.allow_request(_cb_provider):
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "tokenpak: circuit breaker OPEN for %s — fast-failing request",
                    _cb_provider,
                )
                err = json.dumps(
                    {
                        "error": {
                            "type": "circuit_breaker_open",
                            "message": (
                                f"Provider '{_cb_provider}' is currently unavailable. "
                                "The circuit breaker is open due to recent failures. "
                                "Request will be retried automatically after a brief cooldown."
                            ),
                            "provider": _cb_provider,
                            "hint": "Check GET /circuit-breakers for current state.",
                        }
                    }
                ).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.send_header("Retry-After", "30")
                self.end_headers()
                self.wfile.write(err)
                return
        else:
            _cb_provider = None
            _cb_registry = None

        # ── Rate-limit circuit breaker check ──────────────────────────────
        # Fast-fail with 503 if the provider's rate-limit circuit is open
        # (too many 429s in the rolling window).
        if should_log and is_model_request and _cb_provider:
            if get_rate_limit_registry().is_open(_cb_provider):
                import logging as _logging

                _logging.getLogger(__name__).warning(
                    "tokenpak: rate-limit circuit open for %s — returning 503",
                    _cb_provider,
                )
                err = json.dumps(
                    {
                        "error": {
                            "type": "rate_limit_circuit_open",
                            "message": (
                                f"Provider '{_cb_provider}' is rate-limiting requests. "
                                "The rate-limit circuit is open due to repeated 429 responses. "
                                "Request blocked to prevent further upstream hammering."
                            ),
                            "provider": _cb_provider,
                            "hint": "Check GET /circuit-breakers for current state.",
                        }
                    }
                ).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.send_header("Retry-After", "30")
                self.end_headers()
                self.wfile.write(err)
                return

        # Validate credentials for intercepted provider requests
        # Client-supplied key takes precedence over any environment-level key.
        if should_log and is_model_request:
            passthrough_cfg = PassthroughConfig(require_auth=True)
            auth_ok, auth_err = validate_auth(dict(self.headers), passthrough_cfg)
            if not auth_ok:
                import json as _json

                err_body = _json.dumps(
                    {
                        "error": {
                            "type": "authentication_error",
                            "message": auth_err,
                        }
                    }
                ).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
                return

            # --- Request schema validation (strict/warn/off) ---
            if body:
                try:
                    from tokenpak.core.validation.request_validator import (
                        get_request_validator,
                    )

                    _rv = get_request_validator()
                    if _rv.mode != "off":
                        try:
                            _route_for_validation = ps.router.route(
                                target_url, dict(self.headers), body
                            )
                            _provider = _route_for_validation.provider
                        except Exception:
                            _provider = "unknown"
                        _val_result = _rv.validate_bytes(body, target_url, _provider)
                        if not _val_result.valid and _rv.mode == "strict":
                            _err_payload = _val_result.to_error_response()
                            _err_body = json.dumps(_err_payload).encode()
                            self.send_response(400)
                            self.send_header("Content-Type", "application/json")
                            self.send_header("Content-Length", str(len(_err_body)))
                            self.end_headers()
                            self.wfile.write(_err_body)
                            return
                except Exception:
                    pass  # validation errors must never break the proxy
        else:
            passthrough_cfg = PassthroughConfig(require_auth=False)

        # Build forwarding headers (client-supplied auth forwarded unchanged)
        # For Anthropic routes apply a per-route allowlist (mirroring
        # the WS-path tuple).  All other providers keep the existing blocklist
        # path (forward_headers) — their forwarding behavior is unchanged.
        if provider_from_url(target_url) == "anthropic":
            _route = _classify_route(self.path, self.headers)
            _allowlist = (
                CLAUDE_CODE_HEADER_ALLOWLIST if _route == "claude-code" else LEGACY_HEADER_ALLOWLIST
            )
            fwd_headers = {}
            for _hk, _hv in self.headers.items():
                if _hk.lower() in _allowlist:
                    fwd_headers[_hk.lower()] = _hv
        else:
            incoming_headers = dict(self.headers)
            client_has_auth = any(
                name.lower() in {"authorization", "x-api-key"} for name in incoming_headers
            )
            fwd_headers = forward_headers(
                incoming_headers,
                _route,
                client_has_auth=client_has_auth,
            )
        if _decoded_request_encoding:
            for header_name in tuple(fwd_headers):
                if header_name.lower() in {"content-encoding", "content-md5"}:
                    fwd_headers.pop(header_name, None)
        fwd_headers["Host"] = parsed.netloc
        if body is not None:
            fwd_headers["Content-Length"] = str(len(body))

        # ── I5 header-allowlist: strip the proxy auth Bearer ──────────
        # When the proxy auth gate accepted the request via the Bearer path,
        # the Authorization header carries OUR proxy token, not an upstream
        # provider credential. It must not leak upstream. Subsequent injection
        # paths (creds_router, codex OAuth) may set their own Authorization;
        # those are upstream credentials and remain.
        _client_auth = getattr(self, "_tokenpak_proxy_auth_header", None)
        if _client_auth:
            _strip_proxy_auth_for_upstream(fwd_headers, _client_auth)

        # ── Router-based credential injection (feature-flagged) ──────
        # When TOKENPAK_CREDS_ROUTER_ENABLED=1, select a credential via
        # the creds router and inject it. On any failure this is a
        # no-op; the legacy Codex-auth path below then runs unchanged.
        _router_injected = False
        try:
            _router_injected = _creds_router_inject(fwd_headers, target_url, dict(self.headers))
        except Exception:
            _router_injected = False  # fail-open

        # ── Codex OAuth credential injection (legacy default path) ───
        # Legacy compatibility: inject Codex OAuth only when the client did
        # not already supply credentials. Native Codex owns and forwards its
        # authenticated session; TokenPak must not replace or persist it.
        _upstream_provider = provider_from_url(target_url)
        _client_supplied_upstream_auth = any(
            header_name.lower() in {"authorization", "x-api-key"}
            and bool(str(header_value).strip())
            for header_name, header_value in fwd_headers.items()
        )
        if (
            not _router_injected
            and not _client_supplied_upstream_auth
            and _upstream_provider == "openai"
            and (
                "/v1/responses" in target_url
                or "codex" in target_url.lower()
                or "codex" in model.lower()
                or fwd_headers.get("openai-beta", "") == "responses=experimental"
            )
        ):
            try:
                _codex_token, _codex_account = _load_codex_credentials()
                if _codex_token:
                    fwd_headers["Authorization"] = f"Bearer {_codex_token}"
                    for _ck in ("x-api-key", "X-Api-Key"):
                        fwd_headers.pop(_ck, None)
                    if _codex_account:
                        fwd_headers["chatgpt-account-id"] = _codex_account
                    fwd_headers.setdefault("OpenAI-Beta", "responses=experimental")
                    fwd_headers.setdefault("originator", "codex_cli_rs")
            except Exception:
                pass  # fail-open: codex credential loading must not break requests

        # The router injected the credential — still set the Codex-specific
        # beta/originator headers so ChatGPT backend routes the request.
        if (
            _router_injected
            and _upstream_provider == "openai"
            and (
                "/v1/responses" in target_url
                or "codex" in target_url.lower()
                or "codex" in model.lower()
            )
        ):
            fwd_headers.setdefault("OpenAI-Beta", "responses=experimental")
            fwd_headers.setdefault("originator", "codex_cli_rs")

        # Tracks whether response headers have been committed to the client.
        # When True, the global except handler must NOT call send_response()
        # again — doing so produces garbage bytes after the HTTP response
        # already started (e.g. `HTTP/1.0 200\n...SSE...HTTP/1.0 502\n...`),
        # which the CLI's SSE reader surfaces as "Unterminated string".
        _client_headers_sent = False

        # ── Per-session upstream client key ──────────────────────────────
        # Derive a stable identifier for this incoming connection so the
        # connection pool can hand us a dedicated httpx.Client (and thus a
        # dedicated HTTP/2 connection to upstream) for this session. This
        # mirrors the native Claude CLI topology, where each CLI process
        # maintains its own persistent upstream connection — so N concurrent
        # companion sessions get N independent concurrency slots at the
        # provider instead of multiplexing into one. Prefer an explicit
        # session header if the client sent one; else fall back to the
        # (ip, port) tuple of the incoming TCP connection, which is unique
        # per TCP connection from each CLI process.
        _session_key: str | None = None
        for _h in ("x-tokenpak-session-id", "x-session-id", "x-correlation-id"):
            _val = self.headers.get(_h)
            if _val:
                _session_key = _val.strip()
                break
        if not _session_key and self.client_address:
            _session_key = f"{self.client_address[0]}:{self.client_address[1]}"

        # ── Outbound concurrency gate ────────────────────────────────────
        # Bounded semaphore per upstream provider. When the limit is
        # saturated (e.g. 5 companion sessions all firing at once and
        # TOKENPAK_UPSTREAM_CONCURRENCY=3), extra requests wait briefly
        # here instead of piling onto Anthropic simultaneously. Blocks
        # for up to TOKENPAK_UPSTREAM_ACQUIRE_TIMEOUT seconds, then 503s.
        _sem_provider = _upstream_provider or provider_from_url(target_url)
        _upstream_sem = _get_upstream_semaphore(_sem_provider, _session_key)
        _sem_acquired = False
        if _upstream_sem.acquire(timeout=_UPSTREAM_ACQUIRE_TIMEOUT):
            _sem_acquired = True
            _upstream_inflight_delta(_sem_provider, +1, _session_key)
        else:
            # Saturated — fail fast rather than piling on upstream
            err_body = json.dumps(
                {
                    "error": {
                        "type": "upstream_concurrency_exhausted",
                        "message": (
                            f"Too many concurrent requests to '{_sem_provider}' "
                            f"(>{_UPSTREAM_CONCURRENCY} in flight). Retry shortly."
                        ),
                        "hint": (
                            "Raise TOKENPAK_UPSTREAM_CONCURRENCY or reduce the "
                            "number of concurrent companion sessions."
                        ),
                    }
                }
            ).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.send_header("Retry-After", "5")
            self.end_headers()
            self.wfile.write(err_body)
            return

        try:
            pool = self._ps._connection_pool
            _cb_success = False  # track whether request succeeded for circuit breaker
            _final_upstream_status: int | None = None
            resp_body = b""

            output_tokens = 0
            if is_streaming:
                # ── Streaming (SSE) path ──────────────────────────────────
                # Use pool.stream() so the connection is kept alive after SSE ends.
                # Retry transient upstream failures BEFORE any bytes are written
                # to the client — once the SSE stream has started flowing to the
                # CLI it's no longer safe to retry (would cause `Unterminated
                # string` JSON parse errors in the client's SSE reader).
                sse_buffer = b""
                sse_content_encoding = ""
                _stream_wrote_to_client = False
                for _ustream_attempt in range(_retry_policy.max_attempts):
                    _stream_retry = False
                    try:
                        with pool.stream(
                            method,
                            target_url,
                            content=body,
                            headers=fwd_headers,
                            session_key=_session_key,
                        ) as resp:
                            _final_upstream_status = resp.status_code
                            _retry_decision = _retry_policy.retry_for_response(
                                resp.status_code,
                                resp.headers,
                                _ustream_attempt,
                                stream_started=_stream_wrote_to_client,
                            )
                            if _retry_decision.should_retry:
                                # Drain body to release the pooled connection, then retry
                                for _ in resp.iter_bytes(chunk_size=4096):
                                    pass
                                _stream_retry = True
                            elif resp.status_code >= 400:
                                # Non-retryable upstream error — normalize to canonical JSON
                                _raw_err = b"".join(resp.iter_bytes(chunk_size=4096))
                                _norm_err_body = normalize_upstream_error(
                                    resp.status_code, _raw_err, provider_from_url(target_url)
                                )
                                _stream_wrote_to_client = True
                                _client_headers_sent = True
                                self.send_response(resp.status_code)
                                self.send_header("Content-Type", "application/json")
                                self.send_header("Content-Length", str(len(_norm_err_body)))
                                self.send_header("X-Request-ID", _req_id)
                                self.end_headers()
                                self.wfile.write(_norm_err_body)
                            else:
                                _stream_wrote_to_client = True
                                _client_headers_sent = True
                                self.send_response(resp.status_code)
                                has_content_type = False
                                has_cache_control = False
                                for h_key, h_val in resp.headers.items():
                                    h_lower = h_key.lower()
                                    if h_lower in (
                                        "connection",
                                        "keep-alive",
                                        "transfer-encoding",
                                        "content-length",
                                    ):
                                        continue
                                    if h_lower == "content-type":
                                        has_content_type = True
                                    if h_lower == "cache-control":
                                        has_cache_control = True
                                    self.send_header(h_key, h_val)
                                # SSE-required headers: enforce even if upstream omits them
                                if not has_content_type:
                                    self.send_header("Content-Type", "text/event-stream")
                                if not has_cache_control:
                                    self.send_header("Cache-Control", "no-cache")
                                # Always disable nginx buffering for streaming
                                self.send_header("X-Accel-Buffering", "no")
                                # Propagate request ID to client for correlation
                                self.send_header("X-Request-ID", _req_id)
                                self.end_headers()

                                sse_content_encoding = resp.headers.get("content-encoding", "")
                                for chunk in resp.iter_raw():
                                    if not chunk:
                                        continue
                                    try:
                                        self.wfile.write(chunk)
                                        self.wfile.flush()
                                    except (BrokenPipeError, ConnectionResetError):
                                        break
                                    if should_log and is_model_request:
                                        sse_buffer += chunk
                    except _retry_policy.retryable_exceptions as _stream_exc:
                        # Once we've committed to writing to the client, can't retry —
                        # the CLI's SSE parser would see a truncated-then-restarted stream.
                        _retry_decision = _retry_policy.retry_for_exception(
                            _stream_exc,
                            _ustream_attempt,
                            stream_started=_stream_wrote_to_client,
                        )
                        if not _retry_decision.should_retry:
                            raise
                        _stream_retry = True

                    if _stream_retry:
                        time.sleep(_retry_decision.delay_seconds)
                        continue
                    break

                if should_log and is_model_request and sse_buffer:
                    # Forwarding stays raw so entity bytes and Content-Encoding
                    # remain paired. Decode only this isolated telemetry copy.
                    sse_observation_buffer = sse_buffer
                    if sse_content_encoding:
                        try:
                            sse_observation_buffer = httpx.Response(
                                200,
                                headers={"Content-Encoding": sse_content_encoding},
                                content=sse_buffer,
                            ).content
                        except Exception:
                            sse_observation_buffer = b""
                    sse_usage = extract_sse_tokens(sse_observation_buffer)
                    # stop_reason from message_delta (read-only on the buffered
                    # copy - forwarded stream bytes already went out unmodified).
                    stop_reason = _extract_sse_stop_reason(sse_observation_buffer)
                    output_tokens = sse_usage.get("output_tokens", 0)
                    cache_read_tokens = sse_usage.get("cache_read_input_tokens", 0)
                    cache_creation_tokens = sse_usage.get("cache_creation_input_tokens", 0)
                    # Per-TTL prompt-cache attribution (additive telemetry).
                    cache_creation_1h_tokens = sse_usage.get(
                        "cache_creation_ephemeral_1h_input_tokens", 0
                    )
                    cache_creation_5m_tokens = sse_usage.get(
                        "cache_creation_ephemeral_5m_input_tokens", 0
                    )
            else:
                # ── Non-streaming path ────────────────────────────────────
                # Retry on transient upstream failures (RemoteProtocolError,
                # Server disconnected, 502/503/504). Safe because the client
                # has not yet received any bytes at this point.
                resp = None
                for _ustream_attempt in range(_retry_policy.max_attempts):
                    try:
                        resp = pool.request(
                            method,
                            target_url,
                            content=body,
                            headers=fwd_headers,
                            session_key=_session_key,
                        )
                        _retry_decision = _retry_policy.retry_for_response(
                            resp.status_code,
                            resp.headers,
                            _ustream_attempt,
                            stream_started=False,
                        )
                        if _retry_decision.should_retry:
                            try:
                                resp.close()
                            except Exception:
                                pass
                            time.sleep(_retry_decision.delay_seconds)
                            continue
                        if response_has_truncated_json(
                            resp.status_code,
                            resp.headers,
                            resp.content,
                        ):
                            _retry_decision = _retry_policy.retry_for_truncated_json(
                                _ustream_attempt,
                                stream_started=False,
                            )
                            if _retry_decision.should_retry:
                                try:
                                    resp.close()
                                except Exception:
                                    pass
                                time.sleep(_retry_decision.delay_seconds)
                                continue
                            raise UpstreamTruncatedJSONError(
                                "Upstream returned truncated JSON before response bytes were sent"
                            )
                        break
                    except _retry_policy.retryable_exceptions as _nonstream_exc:
                        _retry_decision = _retry_policy.retry_for_exception(
                            _nonstream_exc,
                            _ustream_attempt,
                            stream_started=False,
                        )
                        if not _retry_decision.should_retry:
                            raise
                        time.sleep(_retry_decision.delay_seconds)
                        continue
                assert resp is not None
                _final_upstream_status = resp.status_code

                # Normalize upstream 4xx/5xx to canonical error envelope before
                # sending headers so we can set the correct Content-Type.
                resp_body = resp.content
                _is_upstream_error = resp.status_code >= 400
                if _is_upstream_error:
                    resp_body = normalize_upstream_error(
                        resp.status_code, resp_body, provider_from_url(target_url)
                    )

                _client_headers_sent = True
                self.send_response(resp.status_code)
                for h_key, h_val in resp.headers.items():
                    h_lower = h_key.lower()
                    if h_lower in (
                        "connection",
                        "keep-alive",
                        "transfer-encoding",
                        "content-length",
                        "content-encoding",
                    ):
                        continue
                    if _is_upstream_error and h_lower == "content-type":
                        continue  # overridden below
                    self.send_header(h_key, h_val)
                if _is_upstream_error:
                    self.send_header("Content-Type", "application/json")
                # Debug header: stable prefix hash for cache determinism verification.
                # Emitted for all messages requests (not just intercepted hosts)
                # so integration tests and local stubs can verify determinism.
                if is_model_request:
                    _ph = _compute_stable_prefix_hash(body)
                    if _ph:
                        self.send_header("X-Tokenpak-Cache-Prefix-Hash", _ph)
                # Propagate request ID to client for correlation
                self.send_header("X-Request-ID", _req_id)
                self.end_headers()

                self.wfile.write(resp_body)
                self.wfile.flush()

                if should_log and is_model_request:
                    body_for_metrics = resp_body
                    if "gzip" in resp.headers.get("content-encoding", ""):
                        try:
                            body_for_metrics = gzip.decompress(resp_body)
                        except Exception:
                            pass
                    output_tokens = _extract_response_tokens(body_for_metrics)
                    # stop_reason from the response JSON copy (read-only -
                    # the client already received the original bytes above).
                    stop_reason = _extract_response_stop_reason(body_for_metrics)
                    try:
                        usage = json.loads(body_for_metrics).get("usage", {})
                        cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
                        # Per-TTL prompt-cache attribution (additive, read-only).
                        _cc_obj = usage.get("cache_creation")
                        if isinstance(_cc_obj, dict):
                            cache_creation_1h_tokens = int(
                                _cc_obj.get("ephemeral_1h_input_tokens") or 0
                            )
                            cache_creation_5m_tokens = int(
                                _cc_obj.get("ephemeral_5m_input_tokens") or 0
                            )
                    except Exception:
                        pass
            latency_ms = int((time.time() - t0) * 1000)
            with _forecast_latency_lock:
                _forecast_latencies.append(latency_ms)
            # "No exception escaped" is NOT "provider healthy": a retryable
            # 5xx that exhausted its retries still flows through the normal
            # send path above. Gate breaker success on the FINAL upstream
            # status so provider 5xx storms trip the breaker instead of
            # recording an unbroken success streak (and keeping /status
            # green while every request fails).
            _cb_success = not _cb_status_is_provider_failure(_final_upstream_status)

            # ── Request logging ───────────────────────────────────────────
            try:
                _resp_status = _final_upstream_status or 0
                _req_body_sz = content_length
                _resp_body_sz = len(resp_body)
                _comp_ratio = None
                if input_tokens > 0 and sent_input_tokens > 0:
                    _comp_ratio = sent_input_tokens / input_tokens
                _provider_name = ""
                if "anthropic" in target_url:
                    _provider_name = "anthropic"
                elif "openai" in target_url:
                    _provider_name = "openai"
                elif "googleapis" in target_url:
                    _provider_name = "google"
                _log_extra: dict[str, object] = {}
                _uid = getattr(self, "_tokenpak_user_id", None)
                if _uid:
                    _log_extra["user_id"] = _uid
                log_request(
                    request_id=_req_id,
                    client_ip=self.client_address[0] if self.client_address else "",
                    method=method,
                    endpoint=parsed.path,
                    request_body_size=_req_body_sz,
                    response_status=_resp_status,
                    response_body_size=_resp_body_sz,
                    compression_ratio=_comp_ratio,
                    latency_ms=latency_ms,
                    model=model,
                    provider=_provider_name,
                    extra=_log_extra or None,
                )
            except Exception:
                pass  # logging must never break the proxy

            if should_log and is_model_request and input_tokens > 0:
                if _resp_status != 200:
                    # Non-200 responses generate no tokens; log cost=0 to avoid
                    # phantom cost entries.  Fix per telemetry-gap-2026-03-07.md lines 77-78.
                    cost = 0.0
                    cost_without = 0.0
                    # Record 429 in the rate-limit circuit breaker so repeated
                    # rate-limit bursts trip the circuit and stop upstream hammering.
                    if _resp_status == 429 and _cb_provider:
                        get_rate_limit_registry().record_429(_cb_provider)
                else:
                    cost = estimate_cost(
                        model,
                        sent_input_tokens,
                        output_tokens,
                        cache_read_tokens,
                        cache_creation_tokens,
                    )
                    cost_without = estimate_cost(
                        model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
                    )
                saved = max(0, input_tokens - sent_input_tokens)
                cost_saved = max(0.0, cost_without - cost)

                # Settle the spend-guard in-flight admission now that this
                # request's actual cost is known (the monitor row below makes
                # it visible to DB-derived usage). Requests that die before
                # reaching this point are reclaimed by the counter's TTL.
                if _sg_admission_ticket:
                    try:
                        from tokenpak.proxy.spend_guard.rolling_caps import (
                            settle_pending_spend as _sg_settle,
                        )

                        _sg_settle(_sg_admission_ticket)
                    except Exception:
                        pass
                    _sg_admission_ticket = None

                # Cache attribution: who placed the cache_control markers that
                # produced these cache_read_tokens. Byte-preserved → client did;
                # otherwise the proxy touched the body and owns the markers.
                _cache_origin = "client" if _is_byte_preserved else "proxy"

                with ps._session_lock:
                    ps.session["requests"] += 1
                    ps.session["input_tokens"] += input_tokens
                    ps.session["sent_input_tokens"] += sent_input_tokens
                    ps.session["saved_tokens"] += saved
                    ps.session["protected_tokens"] += protected_tokens
                    ps.session["output_tokens"] += output_tokens
                    ps.session["cost"] += cost
                    ps.session["cost_saved"] += cost_saved
                    ps.session["cache_read_tokens"] += cache_read_tokens
                    ps.session["cache_creation_tokens"] += cache_creation_tokens
                    if _cache_origin == "client":
                        ps.session["cache_read_client"] += cache_read_tokens
                    else:
                        ps.session["cache_read_proxy"] += cache_read_tokens

                # Persist to monitor.db so `tokenpak status`, dashboards, and
                # cross-session reporting see this request. Async write queue
                # keeps this call <0.1ms. Fail-open.
                # Resolve the session id from request headers so monitor.db rows
                # are attributable per session (powers /stats/session). Pass an
                # empty model so the resolver returns "" (not the model-name
                # fallback) when no session header is present — never pollute
                # session attribution with model names.
                try:
                    from tokenpak.proxy.request_pipeline import (
                        _resolve_agent_id as _rai_mon,
                    )
                    from tokenpak.proxy.request_pipeline import (
                        _resolve_cycle_id as _rci_mon,
                    )
                    from tokenpak.proxy.request_pipeline import (
                        _resolve_session_id as _rsi_mon,
                    )

                    _mon_session_id = _rsi_mon(self.headers, "")
                    _mon_agent_id = _rai_mon(self.headers)
                    _mon_cycle_id = _rci_mon(self.headers)
                except Exception:
                    _mon_session_id = ""
                    _mon_agent_id = ""
                    _mon_cycle_id = ""
                # Honest platform-origin attribution (Path C): non-empty ONLY when
                # the origin is genuinely known (a recognized active-session file
                # or platform User-Agent). '' sentinel otherwise — never
                # fabricated, never attributed to the proxy itself. Read-only.
                try:
                    from tokenpak.services.routing_service.platform_bridge import (
                        _openclaw_extract as _oce_mon,
                    )

                    _mon_origin = _oce_mon(dict(self.headers), b"")
                    _mon_attribution_source = (
                        _mon_origin.attribution_source if _mon_origin is not None else ""
                    ) or ""
                except Exception:
                    _mon_attribution_source = ""
                if ps.monitor is not None:
                    try:
                        ps.monitor.log(
                            model=model,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cost=cost,
                            latency_ms=latency_ms,
                            status_code=_resp_status,
                            endpoint=target_url,
                            compilation_mode=ps.compilation_mode,
                            protected_tokens=protected_tokens,
                            compressed_tokens=max(0, input_tokens - sent_input_tokens),
                            injected_tokens=0,
                            injected_sources="",
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                            cache_creation_ephemeral_1h_tokens=cache_creation_1h_tokens,
                            cache_creation_ephemeral_5m_tokens=cache_creation_5m_tokens,
                            ttl_attribution=(
                                "mixed"
                                if cache_creation_1h_tokens > 0 and cache_creation_5m_tokens > 0
                                else "1h"
                                if cache_creation_1h_tokens > 0
                                else "5m"
                                if cache_creation_5m_tokens > 0
                                else "unknown"
                                if cache_creation_tokens > 0
                                else "none"
                            ),
                            would_have_saved=int(saved),
                            cache_origin=_cache_origin,
                            user_id=getattr(self, "_tokenpak_user_id", "") or "",
                            session_id=_mon_session_id,
                            agent_id=_mon_agent_id,
                            cycle_id=_mon_cycle_id,
                            attribution_source=_mon_attribution_source,
                            stop_reason=stop_reason,
                        )
                    except Exception:
                        pass  # DB errors must never break the request

                # Record cache telemetry
                try:
                    _stable_tokens = max(0, input_tokens - (input_tokens - sent_input_tokens))
                    _miss_reason: str | None = None
                    # Prefix-aware miss attribution. Read-only on the body: the
                    # diagnosis parses for analysis only and never feeds back
                    # into the forwarded bytes, so byte-preserved semantics are
                    # untouched. Only runs when caching is in play (cheap byte
                    # guard) and skips pathologically large bodies. Fail-open.
                    _has_cc = body is not None and b'"cache_control"' in body
                    if body is not None and _has_cc and len(body) <= 4_000_000:
                        try:
                            from tokenpak.proxy.cache_poison import diagnose_cache_miss
                            from tokenpak.proxy.request_pipeline import (
                                _resolve_session_id as _rsi_miss,
                            )

                            _sess_key = _rsi_miss(self.headers, model) or "default"
                            with ps._cache_prefix_lock:
                                _prior = ps._cache_prefix_state.get(_sess_key)
                            _diag = diagnose_cache_miss(
                                body,
                                prior_prefix_fingerprint=_prior[0] if _prior else None,
                                prior_prefix_id_hashes=_prior[1] if _prior else None,
                            )
                            # Update prior-prefix state on every request (hits too)
                            # so the next miss has an accurate immediate baseline.
                            with ps._cache_prefix_lock:
                                ps._cache_prefix_state[_sess_key] = (
                                    _diag.prefix_fingerprint,
                                    _diag.prefix_id_hashes,
                                )
                                ps._cache_prefix_state.move_to_end(_sess_key)
                                while len(ps._cache_prefix_state) > 512:
                                    ps._cache_prefix_state.popitem(last=False)
                            if cache_read_tokens == 0:
                                _miss_reason = _diag.reason
                                if os.environ.get("TOKENPAK_CACHE_MISS_DEBUG"):
                                    # Opt-in, redacted: derived metadata only.
                                    print(f"  ↪ {_diag.debug_line()}")
                        except Exception:
                            _miss_reason = None
                    _get_cache_collector().record(
                        CacheMetrics(
                            request_id=trace.request_id if trace else str(uuid.uuid4()),
                            stable_prefix_tokens=sent_input_tokens,
                            stable_cached=(cache_read_tokens > 0),
                            cache_miss_reason=_miss_reason,
                            volatile_tail_tokens=max(0, input_tokens - sent_input_tokens),
                            total_input_tokens=input_tokens,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                            cache_creation_ephemeral_1h_tokens=cache_creation_1h_tokens,
                            cache_creation_ephemeral_5m_tokens=cache_creation_5m_tokens,
                            ttl_attribution=(
                                "mixed"
                                if cache_creation_1h_tokens > 0 and cache_creation_5m_tokens > 0
                                else "1h"
                                if cache_creation_1h_tokens > 0
                                else "5m"
                                if cache_creation_5m_tokens > 0
                                else "unknown"
                                if cache_creation_tokens > 0
                                else "none"
                            ),
                            output_tokens=output_tokens,
                        )
                    )
                except Exception:
                    pass  # telemetry must never break request handling

                # ── Opt-in capture intake (MultiPak Pro capture pipeline) ───
                # Forward an opt-in-flagged model response to the loopback Pro
                # daemon for capture. Structurally safe: runs only AFTER the
                # response was already written to the client (above); guarded to
                # the non-streaming path where the full body is buffered;
                # fire-and-forget (daemon thread); OSS stores nothing. Inert
                # unless TOKENPAK_PROXY_CAPTURE_INTAKE=1 AND the request carried
                # `X-TokenPak-Capture: opt-in`. Fully fail-silent — cannot break
                # a request or touch the byte-preserved response.
                try:
                    if not is_streaming and "body_for_metrics" in dir():
                        from tokenpak.proxy.capture_intake import maybe_forward_capture

                        maybe_forward_capture(self.headers, body_for_metrics, model)
                except Exception:
                    pass  # capture intake must never break request handling

                # Track per-request compression ratio for rolling average
                if input_tokens > 0:
                    ratio = round(saved / input_tokens, 4)
                    with ps._compression_lock:
                        ps._compression_ratios.append(ratio)
                    # Write compression telemetry event
                    ps.compression_stats.record_compression(
                        model=model,
                        tokens_in=input_tokens,
                        tokens_out=output_tokens,
                        ratio=ratio,
                        latency_ms=latency_ms,
                        status="ok",
                    )

                if trace:
                    trace.model = model
                    trace.input_tokens = input_tokens
                    trace.output_tokens = output_tokens
                    trace.tokens_saved = saved
                    trace.cost_saved = cost_saved
                    trace.total_cost = cost
                    trace.duration_ms = latency_ms
                    trace.status = "complete"
                    ps.trace_storage.store(trace)

                with ps._last_lock:
                    ps._last_request = {
                        "request_id": trace.request_id if trace else "?",
                        "timestamp": datetime.now().isoformat(),
                        "model": model,
                        "input_tokens_raw": input_tokens,
                        "input_tokens_sent": sent_input_tokens,
                        "output_tokens": output_tokens,
                        "tokens_saved": saved,
                        "cost_saved": round(cost_saved, 6),
                        "percent_saved": round(saved / input_tokens * 100, 1)
                        if input_tokens
                        else 0.0,
                    }

                # ── Stats footer ──────────────────────────────────────────
                if get_stats_footer_enabled():
                    req_stats = RequestStats(
                        request_id=trace.request_id if trace else "?",
                        timestamp=datetime.now(),
                        input_tokens_raw=input_tokens,
                        input_tokens_sent=sent_input_tokens,
                        tokens_saved=saved,
                        percent_saved=round(saved / input_tokens * 100, 1) if input_tokens else 0.0,
                        cost_saved=round(cost_saved, 6),
                    )
                    print(render_footer_oneline(req_stats), file=sys.stderr)

            # ── Circuit breaker: record outcome ───────────────────────────
            if _cb_registry is not None and _cb_provider is not None:
                if _cb_success:
                    _cb_registry.record_success(_cb_provider)
                else:
                    # Provider-side 5xx that exhausted retries: the exchange
                    # completed, but it is a provider failure for breaker
                    # accounting.
                    _cb_registry.record_failure(_cb_provider)

        except Exception as exc:
            # ── Circuit breaker: record failure ───────────────────────────
            # ...unless OUR client's socket died (BrokenPipeError /
            # ConnectionResetError writing to self.wfile). That says nothing
            # about provider health — counting it opened the breaker for a
            # healthy provider whenever CLIs were killed mid-response.
            if (
                _cb_registry is not None
                and _cb_provider is not None
                and not _is_client_disconnect_error(exc)
            ):
                _cb_registry.record_failure(_cb_provider)

            with ps._session_lock:
                ps.session["errors"] += 1
            latency_ms = int((time.time() - t0) * 1000)
            # Record error event in compression telemetry if this was an intercepted request
            if should_log and is_model_request and input_tokens > 0:
                ps.compression_stats.record_compression(
                    model=model,
                    tokens_in=input_tokens,
                    tokens_out=0,
                    ratio=0.0,
                    latency_ms=latency_ms,
                    status="error",
                )
            exc_type = type(exc).__name__
            exc_msg = str(exc)
            # Log the failed request
            try:
                _err_extra: dict[str, object] = {
                    "error": exc_type,
                    "error_message": exc_msg[:200],
                }
                _uid = getattr(self, "_tokenpak_user_id", None)
                if _uid:
                    _err_extra["user_id"] = _uid
                log_request(
                    request_id=_req_id,
                    client_ip=self.client_address[0] if self.client_address else "",
                    method=method,
                    endpoint=parsed.path,
                    request_body_size=content_length,
                    response_status=502,
                    latency_ms=latency_ms,
                    model=model,
                    extra=_err_extra,
                )
            except Exception:
                pass  # logging must never break the proxy
            # Build an actionable error message depending on the exception type
            _is_retry_boundary_error = (
                _retry_policy.is_retryable_exception(exc)
                or isinstance(exc, UpstreamTruncatedJSONError)
                or (_client_headers_sent and is_streaming)
            )
            _recovery_record_path = None
            if _is_retry_boundary_error:
                _recovery_record_path = persist_failed_request_metadata(
                    request_id=_req_id,
                    tip_plan_id=_tip_plan_id,
                    target_url=target_url,
                    method=method,
                    headers=fwd_headers if "fwd_headers" in locals() else dict(self.headers),
                    body=body if body is not None else _original_body,
                    stream_started=bool(_client_headers_sent),
                    recovery_status="terminally_failed",
                    error_type=exc_type,
                    error_message=exc_msg,
                )
            if "timeout" in exc_type.lower() or isinstance(exc, TimeoutError):
                user_detail = (
                    "The upstream LLM provider did not respond in time. "
                    "Check your internet connection or try again in a moment."
                )
            elif "connection" in exc_type.lower() or "refused" in exc_msg.lower():
                # Determine provider for a more useful message
                _provider_hint = "the LLM provider"
                if "anthropic" in target_url:
                    _provider_hint = "api.anthropic.com"
                elif "openai" in target_url:
                    _provider_hint = "api.openai.com"
                elif "googleapis" in target_url:
                    _provider_hint = "generativelanguage.googleapis.com"
                user_detail = (
                    f"Cannot reach {_provider_hint}. "
                    "Check your API key and internet connection. "
                    "Run `tokenpak doctor` for diagnostics."
                )
            else:
                user_detail = (
                    f"Unexpected proxy error ({exc_type}). "
                    "Check `tokenpak status` for recent errors or run `tokenpak doctor`."
                )
            print(
                f"  ✖ Proxy error [{method} {target_url[:60]}]: {exc_type}: {exc_msg} | {latency_ms}ms\n"
                f"    → {user_detail}"
            )
            try:
                if _client_headers_sent:
                    # Response already started — can't send a new HTTP response.
                    # For SSE streams, emit a synthetic `event: error` (Anthropic's
                    # canonical mid-stream error signal) so the CLI's SSE reader
                    # sees a well-formed frame instead of raw HTTP bytes.
                    if is_streaming:
                        try:
                            # Prepend `\n\n` to terminate any partial SSE frame
                            # that was in flight when upstream disconnected. Without
                            # this, the partial `data: {...` line runs into our
                            # synthetic `event: error` header and the CLI's SSE
                            # parser surfaces it as "Unterminated string". Extra
                            # blank lines between frames are legal per the SSE spec
                            # and harmless when the upstream cut off cleanly.
                            _terminal_payload = build_terminal_recovery_payload(
                                request_id=_req_id,
                                tip_plan_id=_tip_plan_id,
                                error_type="upstream_stream_terminal_failure",
                                message=(
                                    "Upstream connection dropped mid-stream "
                                    f"({exc_type}). The stream was not replayed "
                                    "because client-visible output had already started."
                                ),
                                stream_started=True,
                                recovery_record=(
                                    str(_recovery_record_path)
                                    if _recovery_record_path is not None
                                    else None
                                ),
                            )
                            _sse_err = (
                                "\n\n"
                                "event: error\n"
                                "data: "
                                + json.dumps(
                                    {
                                        "type": "error",
                                        **_terminal_payload,
                                    }
                                )
                                + "\n\n"
                            ).encode("utf-8")
                            self.wfile.write(_sse_err)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                    # Non-streaming: headers+partial body already flushed; nothing
                    # safe to append. Just let the connection close.
                else:
                    if _is_retry_boundary_error:
                        err_payload = build_terminal_recovery_payload(
                            request_id=_req_id,
                            tip_plan_id=_tip_plan_id,
                            error_type="upstream_terminal_failure",
                            message=user_detail,
                            stream_started=False,
                            recovery_record=(
                                str(_recovery_record_path)
                                if _recovery_record_path is not None
                                else None
                            ),
                        )
                        err_payload["error"]["detail"] = exc_msg
                        err_payload["error"]["hint"] = (
                            "Retry later or use a future explicit recovery command; "
                            "TokenPak did not hide a replay."
                        )
                    else:
                        err_payload = {
                            "error": {
                                "type": "proxy_error",
                                "message": user_detail,
                                "detail": exc_msg,
                                "hint": (
                                    "Run `tokenpak doctor` for diagnostics or "
                                    "`tokenpak status` for recent errors."
                                ),
                            }
                        }
                    err = json.dumps(err_payload).encode()
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
            except Exception:
                pass
        finally:
            # Release the outbound concurrency slot no matter how we exit.
            if _sem_acquired:
                try:
                    _upstream_sem.release()
                    _upstream_inflight_delta(_sem_provider, -1, _session_key)
                except ValueError:
                    # BoundedSemaphore raises if released more times than acquired;
                    # swallow to keep the handler fail-safe.
                    pass

    def _handle_count_tokens(self) -> None:
        """Handle POST /v1/messages/count_tokens — compute token count locally.

        Parses the Anthropic Messages body, sums token counts across system/messages/tools
        via the local count_tokens() helper, and returns {"input_tokens": N}.
        No upstream round-trip. Honors anthropic-version and anthropic-beta headers
        (they do not affect local computation).
        """
        from tokenpak.proxy.token_cache import count_tokens as _count_tokens

        content_length = int(self.headers.get("Content-Length", 0))
        try:
            body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
            payload = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            err = json.dumps(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "message": f"Request body is not valid JSON: {exc}",
                    }
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            err = json.dumps(
                {
                    "error": {
                        "type": "invalid_request_error",
                        "message": "Request body must include a 'messages' array",
                    }
                }
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        total = 0

        # system — string or list of content blocks
        system = payload.get("system", "")
        if isinstance(system, str):
            total += _count_tokens(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        total += _count_tokens(text)
                elif isinstance(block, str):
                    total += _count_tokens(block)

        # messages[].content — string or list of content blocks
        for msg in payload["messages"]:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                total += _count_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "")
                        if isinstance(text, str):
                            total += _count_tokens(text)
                    elif isinstance(block, str):
                        total += _count_tokens(block)

        # tools[] — name, description, and input_schema
        for tool in payload.get("tools", []):
            if not isinstance(tool, dict):
                continue
            total += _count_tokens(tool.get("name", ""))
            total += _count_tokens(tool.get("description", ""))
            schema = tool.get("input_schema", {})
            if isinstance(schema, dict):
                total += _count_tokens(json.dumps(schema, separators=(",", ":")))

        resp = json.dumps({"input_tokens": total}, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(resp)

    def _handle_cost_forecast(self) -> None:
        """Handle POST /v1/messages/forecast — local cost forecast, no upstream call.

        Accepts the same body shape as /v1/messages, runs count_tokens locally,
        applies the model pricing config, and returns an estimated cost breakdown.
        No API key required; no upstream round-trip.

        AC2-compliant response shape:
          {
            estimated_cost_usd: float,
            input_tokens: int,
            cached_tokens: int,
            ttfb_estimate_ms: int,
            cache_hit_likelihood: float,
            model: str,
            breakdown: { ... }   # backward-compat nested form
          }
        """
        from tokenpak.proxy.config import MONITOR_DB
        from tokenpak.proxy.forecast_endpoint import build_forecast_response

        def _send_err(msg: str) -> None:
            body = json.dumps({"error": {"type": "invalid_request_error", "message": msg}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        content_length = int(self.headers.get("Content-Length", 0))
        try:
            body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
            payload = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            _send_err(f"Request body is not valid JSON: {exc}")
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            _send_err("Request body must include a 'messages' array")
            return

        try:
            session_id = self.headers.get("x-claude-code-session-id", "").strip()
            result = build_forecast_response(payload, MONITOR_DB, session_id=session_id)
        except Exception as exc:
            _send_err(f"forecast error: {exc}")
            return

        resp_body = json.dumps(result, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(resp_body)

    def _handle_claude_code_backend(self, body: bytes) -> None:
        """Route request through Claude Code CLI (subscription billing).

        Triggered by X-TokenPak-Backend: claude-code header from OpenClaw.
        Returns SSE-formatted response when request has stream:true (which
        OpenClaw always sends), or plain JSON otherwise.
        """
        import logging as _logging

        _log = _logging.getLogger(__name__)
        try:
            from tokenpak.sdk.openclaw import execute_via_claude_code

            body_data = json.loads(body)
            is_streaming = body_data.get("stream", False)

            oc_session = ""
            for _sk, _sv in self.headers.items():
                if _sk.lower() == "x-openclaw-session":
                    oc_session = _sv.strip()
                    break
            if not oc_session:
                oc_session = f"oc_{hash(str(body_data.get('messages', [])[:1])) & 0xFFFFFFFF:08x}"

            # Resolve agent workspace (OpenClaw default or header override)
            oc_workspace = ""
            for _wk, _wv in self.headers.items():
                if _wk.lower() == "x-openclaw-workspace":
                    oc_workspace = _wv.strip()
                    break

            _log.info(
                "claude-code backend: session=%s model=%s stream=%s workspace=%s",
                oc_session,
                body_data.get("model", "?"),
                is_streaming,
                oc_workspace or "(default)",
            )

            result = execute_via_claude_code(
                openclaw_session=oc_session,
                messages=body_data.get("messages", []),
                # Empty when the request carries no model: the CLI backend
                # then runs with its own configured default, and the
                # response/receipt reports the model as unknown rather
                # than a fabricated id.
                model=body_data.get("model") or "",
                system=body_data.get("system", ""),
                max_tokens=body_data.get("max_tokens", 4096),
                workspace=oc_workspace,
            )

            if result.get("type") == "error":
                err_body = json.dumps(result).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
            elif is_streaming:
                # OpenClaw expects Anthropic SSE format
                self._send_claude_code_sse(result)
            else:
                self._send_json(result)

            _log.info("claude-code backend: done session=%s", oc_session)
        except Exception as _cc_err:
            _log.error("claude-code backend error: %s", _cc_err, exc_info=True)
            err = json.dumps({"error": {"type": "backend_error", "message": str(_cc_err)}}).encode()
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass

    def _send_claude_code_sse(self, result: Mapping[str, object]) -> None:
        """Convert a complete Anthropic-format response dict to SSE stream.

        Emits the three events OpenClaw expects:
          1. message_start — contains the message shell + usage.input_tokens
          2. content_block_delta — contains the assistant text
          3. message_delta — contains stop_reason + usage.output_tokens
        """
        raw_msg_id = result.get("id", "msg_unknown")
        msg_id = raw_msg_id if isinstance(raw_msg_id, str) else "msg_unknown"
        # Never invent a model id — echo whatever the backend reported,
        # empty if unknown, so downstream logging/cost attribution cannot
        # key on a fabricated model name.
        raw_model = result.get("model")
        model = raw_model if isinstance(raw_model, str) else ""
        raw_usage = result.get("usage", {})
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        raw_content = result.get("content", [])
        content = raw_content if isinstance(raw_content, list) else []
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw_text = block.get("text", "")
                text = raw_text if isinstance(raw_text, str) else ""
                break

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def _sse(event: str, data: Mapping[str, object]) -> None:
            line = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(line.encode())
            self.wfile.flush()

        # 1. message_start
        _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": usage.get("input_tokens", 0),
                        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                        "output_tokens": 0,
                    },
                },
            },
        )

        # 2. content_block_start
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )

        # 3. content_block_delta — send text in one chunk
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        )

        # 4. content_block_stop
        _sse(
            "content_block_stop",
            {
                "type": "content_block_stop",
                "index": 0,
            },
        )

        # 5. message_delta — stop reason + output tokens
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": result.get("stop_reason", "end_turn"),
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": usage.get("output_tokens", 0)},
            },
        )

        # 6. message_stop
        _sse("message_stop", {"type": "message_stop"})

        # Signal end of stream and close connection
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        try:
            self.wfile.close()
        except Exception:
            pass

    def _send_json(self, data: object) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_app_endpoint_dispatch_error(self, exc: Exception) -> None:
        body = json.dumps(
            {
                "error": "app_endpoint_dispatch_failed",
                "detail": type(exc).__name__,
            }
        ).encode()
        try:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _handle_metrics_dashboard(self) -> None:
        """GET /metrics/dashboard — dashboard JSON with top-20 sessions panel.

        Returns a JSON object with a `sessions` array of the top 20 sessions
        by request count. Each entry contains the columns documented in
        Spec Component 11.
        """
        import sqlite3 as _sqlite3

        ps = self._ps
        monitor = ps.monitor
        db_path = monitor.db_path if monitor is not None else str(_paths.under("monitor.db"))

        sessions: list[dict[str, object]] = []
        try:
            conn = _sqlite3.connect(str(db_path), timeout=3.0)
            conn.row_factory = _sqlite3.Row
            # Top 20 sessions by request count, with aggregated token/cost columns.
            rows = conn.execute("""
                SELECT
                    session_id,
                    SUM(input_tokens)           AS input_tokens,
                    SUM(output_tokens)          AS output_tokens,
                    SUM(cache_read_tokens)      AS cache_read_input_tokens,
                    SUM(cache_creation_tokens)  AS cache_creation_input_tokens,
                    SUM(estimated_cost)         AS cost,
                    COUNT(*)                    AS request_count,
                    MAX(attribution_source)     AS platform
                FROM requests
                WHERE session_id IS NOT NULL AND session_id != ''
                GROUP BY session_id
                ORDER BY request_count DESC
                LIMIT 20
            """).fetchall()

            for row in rows:
                sid = row["session_id"]
                # p50 latency: fetch ordered latencies and pick median.
                lat_rows = conn.execute(
                    "SELECT latency_ms FROM requests "
                    "WHERE session_id=? AND latency_ms IS NOT NULL "
                    "ORDER BY latency_ms",
                    (sid,),
                ).fetchall()
                if lat_rows:
                    vals = [r[0] for r in lat_rows]
                    n = len(vals)
                    mid = n // 2
                    p50 = vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) // 2
                else:
                    p50 = 0

                sessions.append(
                    {
                        "session_id": sid,
                        "input_tokens": row["input_tokens"] or 0,
                        "output_tokens": row["output_tokens"] or 0,
                        "cache_read_input_tokens": row["cache_read_input_tokens"] or 0,
                        "cache_creation_input_tokens": row["cache_creation_input_tokens"] or 0,
                        "cost": round(row["cost"] or 0.0, 6),
                        "request_count": row["request_count"] or 0,
                        "latency_p50": p50,
                        "platform": row["platform"] or "unknown",
                    }
                )
            conn.close()
        except Exception as exc:
            import logging as _logging

            _logging.getLogger(__name__).warning("metrics/dashboard sessions query failed: %s", exc)

        self._send_json({"sessions": sessions})


# ---------------------------------------------------------------------------
# Token helpers (lightweight, no heavy deps)
# ---------------------------------------------------------------------------


def _compute_stable_prefix_hash(body: bytes | None) -> str:
    """
    Compute a short SHA-256 hash of the stable system prefix.

    Used to populate X-Tokenpak-Cache-Prefix-Hash response header for
    determinism verification in integration tests and debug tooling.

    Returns a 16-char hex string, or "" if unavailable.
    """
    if not body:
        return ""
    try:
        import hashlib

        data = json.loads(body)
        system = data.get("system")
        if not system:
            return ""
        if isinstance(system, str):
            stable_text = system.strip()
        elif isinstance(system, list):
            from .prompt_builder import classify_system_blocks

            stable_blocks, _ = classify_system_blocks(system)
            stable_text = "\n".join(
                b.get("text", "")
                for b in stable_blocks
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            return ""
        if not stable_text:
            return ""
        return hashlib.sha256(stable_text.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def _estimate_tokens_from_body(body: bytes) -> int:
    try:
        data = json.loads(body)
        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = data.get("input", [])
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += len(part["text"]) // 4
        return total
    except Exception:
        return len(body) // 4


def _decode_request_entity(body: bytes, content_encoding: str) -> tuple[bytes, bool]:
    """Decode a supported HTTP request entity for safe JSON processing.

    Native Codex currently sends Responses API bodies as zstd.  Once decoded,
    the proxy forwards ordinary JSON and removes Content-Encoding rather than
    pretending the modified entity is still compressed.  The size cap guards
    against compressed-body expansion attacks.
    """
    encoding = content_encoding.strip().lower()
    if not body or encoding in {"", "identity"}:
        return body, False

    try:
        configured_limit = int(
            os.environ.get(
                "TOKENPAK_MAX_DECOMPRESSED_REQUEST_BYTES",
                str(64 * 1024 * 1024),
            )
        )
    except ValueError:
        configured_limit = 64 * 1024 * 1024
    max_output_bytes = max(1, configured_limit)
    try:
        if encoding == "gzip":
            decoded = gzip.decompress(body)
        elif encoding == "zstd":
            import zstandard

            decoded = zstandard.ZstdDecompressor().decompress(
                body,
                max_output_size=max_output_bytes,
            )
        else:
            raise ValueError("unsupported request content encoding")
    except Exception as exc:
        raise ValueError("request body could not be decoded") from exc

    if len(decoded) > max_output_bytes:
        raise ValueError("decoded request body exceeds configured size limit")
    return decoded, True


def _extract_response_tokens(body: bytes) -> int:
    try:
        data = json.loads(body)
        usage = data.get("usage", {})
        value = (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("total_tokens", 0)
        )
        return value if isinstance(value, int) else 0
    except Exception:
        return 0


def _extract_response_stop_reason(body: bytes) -> str:
    """Read ``stop_reason`` from a non-streaming response JSON copy.

    Read-only observation for telemetry (the original response bytes are
    forwarded unmodified). Returns '' when absent or unparseable - never
    fabricated. Distinguishes refusals returned as HTTP 200 (e.g.
    ``stop_reason: "refusal"``) from successful completions on receipt rows.
    """
    try:
        value = json.loads(body).get("stop_reason")
        return value if isinstance(value, str) else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# ProxyServer — public API
# ---------------------------------------------------------------------------


def auto_detect_upstream(request_headers: Mapping[str, str]) -> str:
    """
    Detect target upstream from request headers.

    Supports zero-config mode: when no explicit provider URL is configured,
    this function uses request headers to identify the intended LLM provider
    and route to the correct upstream.

    Header priority:
    1. Authorization: Bearer sk-ant-* → Anthropic
    2. Authorization: Bearer sk-* (non-Anthropic) → OpenAI
    3. x-goog-api-key → Google
    4. anthropic-* headers → Anthropic
    5. Default → Anthropic (most common reverse-proxy use case)

    Args:
        request_headers: Dictionary of HTTP request headers (case-insensitive lookup)

    Returns:
        Upstream provider base URL

    Examples:
        >>> auto_detect_upstream({"authorization": "Bearer sk-ant-abc123"})
        'https://api.anthropic.com'

        >>> auto_detect_upstream({"authorization": "Bearer sk-openai-xyz"})
        'https://api.openai.com'

        >>> auto_detect_upstream({"x-goog-api-key": "AIza..."})
        'https://generativelanguage.googleapis.com'
    """
    # Case-insensitive header lookup
    lower_headers = {k.lower(): v for k, v in request_headers.items()}

    # Check Authorization header
    auth = lower_headers.get("authorization", "").lower()

    # Anthropic token pattern: sk-ant-*
    if auth.startswith("bearer sk-ant-"):
        return "https://api.anthropic.com"

    # OpenAI token pattern: sk-* (but not sk-ant-*)
    if auth.startswith("bearer sk-"):
        return "https://api.openai.com"

    # Google API key
    if "x-goog-api-key" in lower_headers:
        return "https://generativelanguage.googleapis.com"

    # Anthropic-specific headers (x-api-key, anthropic-version, etc)
    if "x-api-key" in lower_headers or "anthropic-version" in lower_headers:
        return "https://api.anthropic.com"

    # Default to Anthropic (most common reverse-proxy use case)
    return "https://api.anthropic.com"


class ProxyServer:
    """
    TokenPak HTTP proxy server.

    Parameters
    ----------
    host : str
        Bind host (default "0.0.0.0").
    port : int
        Bind port (default from TOKENPAK_PORT env var or 8766).
    compilation_mode : str
        "strict" | "hybrid" | "aggressive"
    request_hook : callable, optional
        Called for each intercepted request before forwarding.
        Signature: (body: bytes, model: str, trace: PipelineTrace | None)
                    -> (body, sent_tokens, raw_tokens, protected_tokens)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int | None = None,
        compilation_mode: str | None = None,
        request_hook: RequestHook | None = None,
        shutdown_timeout: float | None = None,
    ) -> None:
        self.host = host
        self.port = port or int(os.environ.get("TOKENPAK_PORT", "8766"))
        self.compilation_mode = compilation_mode or os.environ.get("TOKENPAK_MODE", "hybrid")
        self.shutdown_timeout: float = (
            shutdown_timeout
            if shutdown_timeout is not None
            else float(os.environ.get("TOKENPAK_SHUTDOWN_TIMEOUT", "30"))
        )
        # Graceful shutdown coordinator
        self.shutdown = GracefulShutdown()
        # Explicit opt-in guard. Configuration is parsed during construction,
        # but its thread starts only after the listener binds successfully.
        self._memory_guard = _create_memory_guard()
        self._memory_guard_configuration = _memory_guard_configuration_status()
        self._stop_lock = threading.Lock()
        self._signal_stop_thread: threading.Thread | None = None
        self._lifecycle_state = "created"

        # Connection pool — shared across all handler threads
        self._connection_pool = ConnectionPool(PoolConfig.from_env())

        # Auto-wire the capsule builder hook.  When TOKENPAK_CAPSULE_BUILDER=0
        # (the default) the hook is a no-op, so this is safe for all deployments.
        # If a caller supplies their own request_hook it is chained *after* the
        # capsule stage so they still see the (potentially compressed) body.
        try:
            from .capsule_integration import get_capsule_request_hook

            self.request_hook: RequestHook | None = get_capsule_request_hook(base_hook=request_hook)
        except Exception:  # pragma: no cover — import failure falls back gracefully
            self.request_hook = request_hook

        # Wire apply_stable_cache_control into the pipeline.
        # Runs AFTER any capsule/compression processing, BEFORE forwarding to LLM.
        # Ensures every Anthropic request with a system prompt gets a stable cache
        # prefix marker — enabling prompt cache reuse across requests.
        try:
            from .prompt_builder import apply_stable_cache_control

            _prior_hook = self.request_hook

            def _stable_cache_hook(
                body: bytes,
                model: str,
                trace: PipelineTrace | None = None,
            ) -> tuple[bytes, int, int, int]:
                if _prior_hook is not None:
                    body, sent, raw, protected = _prior_hook(body, model, trace)
                else:
                    _tok = len(body) // 4
                    body, sent, raw, protected = body, _tok, _tok, 0
                body = apply_stable_cache_control(body)
                return body, sent, raw, protected

            self.request_hook = _stable_cache_hook
        except Exception:  # pragma: no cover — import failure gracefully degrades
            pass

        self.router = ProviderRouter()
        self.trace_storage = TraceStorage(max_traces=50)
        self.session_filter = SessionFilter()
        self.session: _SessionState = _new_session()
        self._session_lock = threading.Lock()
        self._last_request: dict[str, object] | None = None
        self._last_lock = threading.Lock()
        self._server: _ThreadedHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._admission_limit = max(1, int(os.environ.get("TOKENPAK_MANAGED_ADMISSION", "16")))
        self._admission = threading.BoundedSemaphore(self._admission_limit)
        self._admission_rejected = 0
        # Managed background-agent parallel-execution gate. The admission
        # lease above bounds how many managed connections the listener holds
        # at once; this gate bounds how many of those run in parallel
        # (default 2), queueing the rest FIFO. The queue depth is the lease
        # headroom, so total held managed connections never exceed the lease.
        from tokenpak.proxy.admission import AgentConcurrencyGate, resolve_agent_concurrency

        self._agent_gate: AgentConcurrencyGate | None
        _gate_cap, _gate_source = resolve_agent_concurrency()
        if _gate_cap is None:
            self._agent_gate = None  # explicit operator opt-out (env off)
        else:
            self._agent_gate = AgentConcurrencyGate(
                _gate_cap,
                max_queue=max(0, self._admission_limit - _gate_cap),
                degraded_probe=self._agent_gate_degraded,
                source=_gate_source,
            )
        # Rolling window of per-request compression ratios (last 100)
        self._compression_ratios: deque[float] = deque(maxlen=100)
        self._compression_lock = threading.Lock()
        # Per-session cached-prefix state for prefix-aware cache-miss
        # attribution (telemetry only). session_id -> (fingerprint, id_hashes).
        # Bounded LRU; never holds raw prompt content (hashes/fingerprints only).
        self._cache_prefix_state: OrderedDict[str, tuple[str, frozenset[str]]] = OrderedDict()
        self._cache_prefix_lock = threading.Lock()
        # Compression telemetry — writes events to ~/.tokenpak/compression_events.jsonl
        self.compression_stats = CompressionStats()

        # SQLite request ledger — resolved via _paths.monitor_db(mode="write").
        # Powers `tokenpak status`, `savings`, dashboards. Async write queue keeps
        # per-request cost <0.1ms. Fail-open: any DB error never breaks the proxy.
        try:
            from tokenpak.proxy.config import MONITOR_DB

            self.monitor: _DbMonitor | None = _DbMonitor(MONITOR_DB)
        except Exception:
            self.monitor = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, blocking: bool = True) -> None:
        """Start the proxy server."""
        previous_signal_handlers: dict[int, Any] = {}

        def _restore_start_signals() -> None:
            for signum, previous in previous_signal_handlers.items():
                try:
                    signal.signal(signum, previous)
                except (OSError, RuntimeError, ValueError):
                    pass

        # Serialize bind/guard/thread setup with stop(). The lock is released
        # before a blocking serve loop so a signal-owned stop thread can run.
        with self._stop_lock:
            if self._lifecycle_state != "created":
                raise RuntimeError(
                    f"proxy server is single-use and cannot start from "
                    f"{self._lifecycle_state!r} state"
                )
            self._lifecycle_state = "starting"

            try:
                if blocking and threading.current_thread() is threading.main_thread():
                    for signum in (signal.SIGTERM, signal.SIGINT):
                        previous_signal_handlers[signum] = signal.getsignal(signum)
                        signal.signal(signum, self._handle_signal)
                _all_ok, _warnings = run_startup_checks(self.port)
                if _warnings:
                    report = format_startup_report(_warnings, _all_ok)
                    print(report)
                    for warning in _warnings:
                        get_degradation_tracker().record(
                            DegradationEventType.STARTUP_WARNING,
                            warning,
                            recovered=_all_ok,
                        )
                server = _ThreadedHTTPServer((self.host, self.port), _ProxyHandler)
            except Exception:
                self._lifecycle_state = "start_failed"
                _restore_start_signals()
                raise
            server.proxy_server = self  # inject back-reference
            self._server = server

            try:
                if self._memory_guard is not None:
                    self._memory_guard.start()
            except Exception:
                # An explicitly enabled guard is enforcement, not telemetry.
                # Roll back even a partially-started custom implementation and
                # never leave its listener live.
                self._lifecycle_state = "start_failed"
                _restore_start_signals()
                try:
                    if self._memory_guard is not None:
                        self._memory_guard.stop()
                except Exception as cleanup_exc:
                    self._lifecycle_state = "start_cleanup_failed"
                    print(
                        f"TokenPak: MemoryGuard startup cleanup error: {cleanup_exc}",
                        flush=True,
                    )
                try:
                    server.server_close()
                except Exception as cleanup_exc:
                    self._lifecycle_state = "start_cleanup_failed"
                    print(
                        f"TokenPak: listener startup cleanup error: {cleanup_exc}",
                        flush=True,
                    )
                else:
                    self._server = None
                raise

            if not blocking:
                server_thread = threading.Thread(
                    target=server.serve_forever,
                    name="tokenpak-proxy-server",
                    daemon=True,
                )
                self._server_thread = server_thread
                try:
                    server_thread.start()
                except Exception:
                    self._server_thread = None
                    self._lifecycle_state = "start_failed"
                    _restore_start_signals()
                    try:
                        if self._memory_guard is not None:
                            self._memory_guard.stop()
                    except Exception as cleanup_exc:
                        self._lifecycle_state = "start_cleanup_failed"
                        print(
                            f"TokenPak: proxy startup cleanup error: {cleanup_exc}",
                            flush=True,
                        )
                    try:
                        server.server_close()
                    except Exception as cleanup_exc:
                        self._lifecycle_state = "start_cleanup_failed"
                        print(
                            f"TokenPak: listener startup cleanup error: {cleanup_exc}",
                            flush=True,
                        )
                    else:
                        self._server = None
                    raise
                self._lifecycle_state = "running"
                return

            self._lifecycle_state = "running"

        try:
            for startup_message in (
                f"TokenPak proxy listening on {self.host}:{self.port} [{self.compilation_mode}]",
                "  ✓ Zero-config mode enabled (auto-detecting upstream from request headers)",
            ):
                try:
                    print(startup_message)
                except Exception:
                    # A closed supervising terminal must not strand an owned
                    # listener before serve_forever has entered.
                    pass
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass  # SIGINT handled via _handle_signal → stop()
        finally:
            # Signal-install and serve-loop failures share the same owned cleanup.
            if self._server is not None:
                self.stop()
            _restore_start_signals()

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        """Signal handler for SIGTERM/SIGINT — triggers graceful shutdown."""
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(
            f"\nTokenPak: {sig_name} received — starting graceful shutdown "
            f"(drain timeout: {self.shutdown_timeout:.0f}s)...",
            flush=True,
        )
        # Run stop() in a background thread so the signal handler returns quickly
        if self._signal_stop_thread is not None and self._signal_stop_thread.is_alive():
            return

        def _signal_stop() -> None:
            try:
                self.stop()
            finally:
                self._signal_stop_thread = None

        stop_thread = threading.Thread(
            target=_signal_stop,
            name="tokenpak-proxy-signal-stop",
            daemon=True,
        )
        self._signal_stop_thread = stop_thread
        stop_thread.start()

    def stop(self) -> None:
        """Serialize repeated/concurrent stop calls around the owned lifecycle."""
        with self._stop_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        """
        Gracefully shut down the proxy server.

        Sequence:
          1. Stop accepting new requests (return 503 for any new proxied calls)
          2. Stop and join the MemoryGuard
          3. Drain in-flight requests (up to ``shutdown_timeout`` seconds)
          4. Flush telemetry buffer to disk
          5. Close the HTTP connection pool
          6. Stop, close, and join the HTTP server
        """
        guard_error: Exception | None = None
        prior_state = self._lifecycle_state
        self._lifecycle_state = "stopping"

        # A startup rollback may retain a listener solely because close failed.
        # It was never served, so retry close directly rather than calling
        # HTTPServer.shutdown(), which requires a live serve_forever loop.
        if prior_state == "start_cleanup_failed" and self._server is not None:
            if self._memory_guard is not None:
                try:
                    self._memory_guard.stop()
                except Exception as exc:
                    guard_error = exc
            try:
                self._server.server_close()
            except Exception as exc:
                if guard_error is None:
                    guard_error = exc
            else:
                self._server = None
            try:
                self._connection_pool.close()
            except Exception:
                pass
            self._lifecycle_state = "stopped" if guard_error is None else "start_cleanup_failed"
            if guard_error is not None:
                raise guard_error
            return

        # Never started (or already stopped): nothing is in flight, so just
        # stop any partially-started guard, release pool resources, and return.
        # For a RUNNING server the pool
        # must NOT be closed here — closing it before the drain below kills
        # every in-flight request's upstream connection, turning a graceful
        # SIGTERM into a mid-stream connection reset for every active
        # request. The pool is closed at step 4, after the drain completes.
        if self._server is None:
            if self._memory_guard is not None:
                try:
                    self._memory_guard.stop()
                except Exception as exc:
                    guard_error = exc
            if self._connection_pool is not None:
                try:
                    self._connection_pool.close()
                except Exception:
                    pass
            if guard_error is not None:
                self._lifecycle_state = "stop_failed"
                raise guard_error
            self._lifecycle_state = "stopped"
            return

        # ── Step 1: Stop accepting new proxy requests ─────────────────────
        self.shutdown.begin()
        print("TokenPak: shutdown step 1/6 — rejecting new requests (503)", flush=True)

        # ── Step 2: Stop MemoryGuard before request/cache teardown ───────────
        if self._memory_guard is not None:
            print("TokenPak: shutdown step 2/6 — stopping MemoryGuard...", flush=True)
            try:
                self._memory_guard.stop()
                print("TokenPak: shutdown step 2/6 — MemoryGuard stopped ✓", flush=True)
            except Exception as exc:
                guard_error = exc
                print(
                    f"TokenPak: shutdown step 2/6 — MemoryGuard stop error: {exc}",
                    flush=True,
                )

        # ── Step 3: Drain in-flight requests ──────────────────────────────
        in_flight = self.shutdown.in_flight_count()
        if in_flight > 0:
            print(
                f"TokenPak: shutdown step 3/6 — draining {in_flight} in-flight request(s) "
                f"(timeout: {self.shutdown_timeout:.0f}s)...",
                flush=True,
            )
        else:
            print("TokenPak: shutdown step 3/6 — no in-flight requests, proceeding", flush=True)

        drained = self.shutdown.wait_for_drain(timeout=self.shutdown_timeout)
        if not drained:
            remaining = self.shutdown.in_flight_count()
            print(
                f"TokenPak: shutdown drain timed out after {self.shutdown_timeout:.0f}s "
                f"({remaining} request(s) still active — forcing close)",
                flush=True,
            )
        else:
            print("TokenPak: shutdown step 3/6 — all requests drained ✓", flush=True)

        # ── Step 4: Flush telemetry buffer to disk ─────────────────────────
        print("TokenPak: shutdown step 4/6 — flushing telemetry...", flush=True)
        try:
            self._flush_telemetry()
            print("TokenPak: shutdown step 4/6 — telemetry flushed ✓", flush=True)
        except Exception as exc:
            print(
                f"TokenPak: shutdown step 4/6 — telemetry flush error (non-fatal): {exc}",
                flush=True,
            )

        # ── Step 5: Close HTTP connection pool ────────────────────────────
        print("TokenPak: shutdown step 5/6 — closing connection pool...", flush=True)
        try:
            self._connection_pool.close()
            print("TokenPak: shutdown step 5/6 — connection pool closed ✓", flush=True)
        except Exception as exc:
            print(f"TokenPak: shutdown step 5/6 — pool close error (non-fatal): {exc}", flush=True)

        # ── Step 6: Stop HTTP server and release its listener ──────────────
        print("TokenPak: shutdown step 6/6 — stopping HTTP server...", flush=True)
        srv = self._server
        server_thread = self._server_thread
        server_error: Exception | None = None
        try:
            srv.shutdown()
        except Exception as exc:
            server_error = exc
        try:
            srv.server_close()
        except Exception as exc:
            if server_error is None:
                server_error = exc
        try:
            if server_thread is not None and server_thread is not threading.current_thread():
                server_thread.join(timeout=5.0)
                if server_thread.is_alive():
                    raise RuntimeError("proxy server thread did not stop within 5 seconds")
            if server_thread is None or not server_thread.is_alive():
                self._server_thread = None
        except Exception as exc:
            if server_error is None:
                server_error = exc

        if server_error is None:
            self._server = None
            print("TokenPak: shutdown step 6/6 — HTTP server stopped ✓", flush=True)
        else:
            print(f"TokenPak: shutdown step 6/6 — server stop error: {server_error}", flush=True)
            if guard_error is None:
                guard_error = server_error

        print("TokenPak: graceful shutdown complete.", flush=True)
        if guard_error is not None:
            self._lifecycle_state = "stop_failed"
            raise guard_error
        self._lifecycle_state = "stopped"

    def _flush_telemetry(self) -> None:
        """
        Flush any buffered telemetry to disk before process exit.

        Writes a shutdown summary entry to the compression events JSONL file
        so stats from the current session are preserved across restarts.
        """
        # Snapshot under the session lock — requests may still be mutating
        # these counters while the drain window is open.
        with self._session_lock:
            session = self.session.copy()
        shutdown_record = {
            "event": "shutdown",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_requests": session.get("requests", 0),
            "session_tokens_saved": session.get("saved_tokens", 0),
            "session_cost_saved": round(session.get("cost_saved", 0.0), 6),
            "session_cost_total": round(session.get("cost", 0.0), 6),
            "session_errors": session.get("errors", 0),
            "uptime_seconds": round(time.time() - session.get("start_time", time.time())),
        }
        # Drain the monitor's async write queue so queued request rows are
        # persisted before exit. The monitor DB writer is a daemon thread that
        # is killed abruptly at process exit; without this drain, up to a
        # queue's worth of already-recorded request rows are lost on a clean
        # shutdown (recorded spend < real spend, under-firing rolling caps).
        # Ordered before the compression-stats flush so a failure in that sink
        # cannot skip the monitor drain — recorded request rows are the
        # critical data.
        if self.monitor is not None:
            self.monitor.flush(timeout=5.0)

        # Delegate to the compression_stats recorder (writes to ~/.tokenpak/compression_events.jsonl)
        self.compression_stats.flush_shutdown_record(shutdown_record)

    def is_running(self) -> bool:
        return self._lifecycle_state == "running" and self._server is not None

    def _agent_gate_degraded(self) -> bool:
        """Cheap, no-live-call probe for the background-agent concurrency gate.

        True when the same signals ``health()`` already uses to report
        ``is_degraded`` (degradation tracker, memory guard) indicate trouble,
        or when any provider's circuit breaker is open. Any probe failure is
        treated as "not degraded" — a broken probe must never wedge admission
        by pinning the gate to serial mode forever.
        """
        try:
            if get_degradation_tracker().is_degraded():
                return True
        except Exception:
            pass
        try:
            guard_snapshot = self._memory_guard_snapshot()
            if guard_snapshot.get("enabled") and self._lifecycle_state == "running":
                state = guard_snapshot.get("state")
                if not (state == "running" and guard_snapshot.get("thread_alive")):
                    return True
        except Exception:
            pass
        try:
            for status in get_circuit_breaker_registry().all_statuses().values():
                if status.get("state") == "open":
                    return True
        except Exception:
            pass
        return False

    def _memory_guard_snapshot(self) -> dict[str, object]:
        """Return lifecycle/config truth without creating or starting a guard."""
        if self._memory_guard is None:
            return {
                "enabled": False,
                "state": "disabled",
                "thread_alive": False,
                "callback_policy": "disabled",
                "configuration": dict(self._memory_guard_configuration),
                "callbacks": {
                    "compact": False,
                    "token": False,
                    "semantic": False,
                },
            }
        return self._memory_guard.stats

    # ------------------------------------------------------------------
    # Status endpoints (also used by handler GET routes)
    # ------------------------------------------------------------------

    def health(self, deep: bool = False) -> dict[str, object]:
        with self._session_lock:
            uptime = round(time.time() - self.session["start_time"])
            requests_total = self.session["requests"]
            requests_errors = self.session["errors"]
        with self._compression_lock:
            ratios = list(self._compression_ratios)
        compression_ratio_avg = round(sum(ratios) / len(ratios), 4) if ratios else 0.0
        pool_metrics = self._connection_pool.metrics()
        deg = get_degradation_tracker()
        guard_snapshot = self._memory_guard_snapshot()
        guard_state = guard_snapshot.get("state")
        guard_degraded = bool(
            guard_snapshot.get("enabled")
            and self._lifecycle_state == "running"
            and not (guard_state == "running" and guard_snapshot.get("thread_alive"))
        )
        is_degraded = deg.is_degraded() or guard_degraded
        is_shutting_down = self.shutdown.is_shutting_down
        # Circuit breaker summary
        cb_registry = get_circuit_breaker_registry()
        cb_statuses = cb_registry.all_statuses()
        cb_any_open = any(s.get("state") in ("open", "half_open") for s in cb_statuses.values())
        result: dict[str, object] = {
            "status": "shutting_down"
            if is_shutting_down
            else ("degraded" if is_degraded else "ok"),
            "uptime_seconds": uptime,
            "version": _tokenpak_version,
            "requests_total": requests_total,
            "requests_errors": requests_errors,
            "compression_ratio_avg": compression_ratio_avg,
            "is_degraded": is_degraded,
            "is_shutting_down": is_shutting_down,
            "in_flight_requests": self.shutdown.in_flight_count(),
            "memory_guard": guard_snapshot,
            "admission": {
                "limit": self._admission_limit,
                "available": self._admission._value,
                "rejected": self._admission_rejected,
            },
            "agent_concurrency": (
                self._agent_gate.snapshot() if self._agent_gate is not None else {"enabled": False}
            ),
            "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "connection_pool": {
                "http2_enabled": self._connection_pool.http2_enabled,
                "active_providers": self._connection_pool.active_providers,
                **pool_metrics,
            },
            "circuit_breakers": {
                "enabled": cb_registry.enabled,
                "any_open": cb_any_open,
                "providers": cb_statuses,
            },
        }
        if deep:
            import shutil

            # providers: list active providers with their circuit-breaker status
            providers = [
                {"name": name, "status": info.get("state", "unknown")}
                for name, info in cb_statuses.items()
            ]

            # Deep probes are independent and additive.  Missing optional
            # dependencies or probe failures stay JSON-serialisable, never
            # become a fabricated zero, and never suppress the other probes.
            memory: dict[str, object] = {"rss_mb": None, "available": False}
            try:
                import psutil
            except ImportError:
                memory["reason"] = "optional_dependency_unavailable"
            except Exception:
                memory["reason"] = "probe_failed"
            else:
                try:
                    proc = psutil.Process()
                    memory["rss_mb"] = round(proc.memory_info().rss / (1024 * 1024), 1)
                    memory["available"] = True
                except Exception:
                    memory["reason"] = "probe_failed"

            disk_info: dict[str, object] = {"available_gb": None, "available": False}
            try:
                disk = shutil.disk_usage("/")
                disk_info["available_gb"] = round(disk.free / (1024**3), 2)
                disk_info["available"] = True
            except Exception:
                disk_info["reason"] = "probe_failed"

            result["providers"] = providers
            result["memory"] = memory
            result["disk"] = disk_info
        return result

    def status(self) -> dict[str, object]:
        """Return a concise operational status snapshot for GET /status."""
        with self._session_lock:
            start_time = self.session["start_time"]
            requests_total = self.session["requests"]
        uptime = round(time.time() - start_time)
        started_at = datetime.fromtimestamp(start_time, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # last_request_at: ISO timestamp of most recent proxied request, or None.
        with self._last_lock:
            last_req = self._last_request
        last_request_at = last_req["timestamp"] if last_req is not None else None

        # Provider health — cached from circuit breaker state; no live probe per request.
        # Key env vars determine whether a provider is configured at all.
        _provider_keys = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "google": "GOOGLE_API_KEY",
        }
        cb_registry = get_circuit_breaker_registry()
        cb_statuses = cb_registry.all_statuses()
        providers: dict[str, str] = {}
        active_alerts = 0
        for name, env_var in _provider_keys.items():
            has_key = bool(os.environ.get(env_var, "").strip())
            if not has_key:
                providers[name] = "no-key"
            elif name in cb_statuses:
                state = cb_statuses[name].get("state", "unknown")
                if state in ("open", "half_open"):
                    providers[name] = "unreachable"
                    active_alerts += 1
                else:
                    providers[name] = "reachable"
            else:
                providers[name] = "reachable"

        return {
            "version": _tokenpak_version,
            "uptime_seconds": uptime,
            "started_at": started_at,
            "providers": providers,
            "active_alerts": active_alerts,
            "requests_total": requests_total,
            "last_request_at": last_request_at,
        }

    def stats(self) -> dict[str, object]:
        # Copy under the session lock: handler threads mutate these counters
        # concurrently, and returning the live dict let JSON serialization
        # race with (and observe torn) mid-request updates.
        with self._session_lock:
            s = self.session.copy()
        return {
            "session": s,
            "compilation_mode": self.compilation_mode,
            "memory_guard": self._memory_guard_snapshot(),
            "cache_read_by_origin": {
                "client": s.get("cache_read_client", 0),
                "proxy": s.get("cache_read_proxy", 0),
                "unknown": s.get("cache_read_unknown", 0),
            },
        }

    def session_stats(self) -> dict[str, object]:
        with self._session_lock:
            s = self.session.copy()
        uptime = round((time.time() - s["start_time"]) / 3600, 2)
        return {
            "session_requests": s["requests"],
            "session_total_saved": round(s["cost_saved"], 4),
            "tokens_saved": s["saved_tokens"],
            "tokens_sent": s["sent_input_tokens"],
            "tokens_raw": s["input_tokens"],
            "output_tokens": s["output_tokens"],
            "total_cost": round(s["cost"], 4),
            "uptime_hours": uptime,
            "errors": s["errors"],
            "avg_savings_pct": (
                round(s["saved_tokens"] / s["input_tokens"] * 100, 1)
                if s["input_tokens"] > 0
                else 0.0
            ),
        }

    def last_request_stats(self) -> dict[str, object]:
        with self._last_lock:
            if self._last_request is None:
                return {"error": "no_requests", "message": "No requests captured yet."}
            return dict(self._last_request)

    def reset_session(self) -> None:
        with self._session_lock:
            t = self.session["start_time"]
            self.session = _new_session()
            self.session["start_time"] = t


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def start_proxy(
    host: str = "127.0.0.1",
    port: int | None = None,
    compilation_mode: str | None = None,
    request_hook: RequestHook | None = None,
    blocking: bool = True,
    shutdown_timeout: float | None = None,
) -> ProxyServer:
    """Create and start a ProxyServer. Returns the server instance."""
    server = ProxyServer(
        host=host,
        port=port,
        compilation_mode=compilation_mode,
        request_hook=request_hook,
        shutdown_timeout=shutdown_timeout,
    )
    server.start(blocking=blocking)
    return server


# Backward-compat alias
ForwardProxyHandler = _ProxyHandler


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _write_proxy_pid_file() -> Path:
    """Resolve the proxy PID path under TOKENPAK_HOME and write the current PID.

    Extracted so scoped-home isolation is unit-testable without launching the
    server. Honors TOKENPAK_HOME (falls back to ~/.tokenpak when unset), so a
    scoped-home proxy writes its own pid instead of clobbering the default home.
    """
    pid_path = _paths.under("proxy.pid")
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()))
    return pid_path


def main() -> None:
    """
    Entry point for ``python -m tokenpak.proxy.server``.

    Parses CLI arguments, applies environment overrides, writes a PID file,
    installs a SIGHUP handler for config hot-reload, prints a startup banner,
    then blocks on the proxy server until SIGTERM/SIGINT.
    """
    import argparse
    import logging

    parser = argparse.ArgumentParser(
        prog="python -m tokenpak.proxy.server",
        description="TokenPak forward proxy — intercepts and optimises LLM API traffic.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port (env: TOKENPAK_PORT, default: 8766)",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to config YAML (env: TOKENPAK_CONFIG, default: <TOKENPAK_HOME>/config.yaml, i.e. ~/.tokenpak/config.yaml)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Python logging level (env: TOKENPAK_LOG_LEVEL, default: warning)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=["safe", "balanced", "aggressive", "agentic", "transparent"],
        help="Named workflow profile (env: TOKENPAK_PROFILE, default: balanced)",
    )
    args = parser.parse_args()

    # Apply CLI args to environment *before* constructing ProxyServer.
    # ProxyServer reads TOKENPAK_PORT / TOKENPAK_MODE / TOKENPAK_BIND_ADDRESS
    # from os.environ at __init__ time, so setting them here ensures the right
    # values are used even after config.py's module-level evaluation has run.
    if args.port is not None:
        os.environ["TOKENPAK_PORT"] = str(args.port)
    if args.profile is not None:
        os.environ["TOKENPAK_PROFILE"] = args.profile
    if args.config is not None:
        os.environ["TOKENPAK_CONFIG"] = args.config
    elif "TOKENPAK_CONFIG" not in os.environ:
        # Default config path honors TOKENPAK_HOME so a scoped-home proxy resolves
        # its own config; unchanged (~/.tokenpak/config.yaml) when TOKENPAK_HOME is
        # unset. NOTE: config.py's import-time loader may resolve the config file
        # independently of this — full config scoping is a config.py residual.
        os.environ["TOKENPAK_CONFIG"] = str(_paths.under("config.yaml"))

    _log_level = (args.log_level or os.environ.get("TOKENPAK_LOG_LEVEL", "warning")).upper()
    logging.basicConfig(level=_log_level, format="%(levelname)s %(name)s: %(message)s")

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    host = os.environ.get("TOKENPAK_BIND_ADDRESS", "127.0.0.1")
    mode = os.environ.get("TOKENPAK_MODE", "hybrid")
    profile = os.environ.get("TOKENPAK_PROFILE", "balanced")

    _mode_desc = {
        "strict": "100% lossless — no compression",
        "hybrid": "protected/code strict, narrative compressed",
        "aggressive": "everything except protected gets compressed",
    }

    # Write PID file on startup; remove on clean shutdown. Path resolves under
    # TOKENPAK_HOME (falls back to ~/.tokenpak when unset), so a scoped-home
    # proxy writes its own pid instead of clobbering the default home.
    _pid_path = _write_proxy_pid_file()

    from tokenpak.proxy.config import PROVIDER_DISPLAY as _provider_display

    print(
        f"""
╔══════════════════════════════════════════════════════════════════╗
║  TokenPak Proxy  v{_tokenpak_version}
╠══════════════════════════════════════════════════════════════════╣
║  Listening:  http://{host}:{port}
║  Profile:    {profile}
║  Mode:       {mode} — {_mode_desc.get(mode, "?")}
║  Providers:  {_provider_display}
║  PID:        {os.getpid()}
║  PID file:   {_pid_path}
╚══════════════════════════════════════════════════════════════════╝
""",
        flush=True,
    )

    ps = ProxyServer(host=host, port=port)

    # Start background model discovery if enabled (TOKENPAK_MODEL_DISCOVERY=1).
    # Discovery polls provider /v1/models endpoints and persists to
    # ~/.tokenpak/data/discovered_models.json for offline visibility.
    # Pricing/inference for unseen models always works via family rules,
    # so this is purely supplementary.
    try:
        from tokenpak.models._discovery import auto_start_if_enabled

        auto_start_if_enabled()
    except Exception:
        pass  # discovery is optional; never block proxy startup

    def _handle_sighup(signum: int, frame: FrameType | None) -> None:
        """Hot-reload dynamic config from environment variables (no restart needed)."""
        print("\n[tokenpak] SIGHUP received — reloading config from env", flush=True)
        ps.compilation_mode = os.environ.get("TOKENPAK_MODE", ps.compilation_mode)
        print(f"[tokenpak] config reloaded: mode={ps.compilation_mode}", flush=True)

    # SIGHUP-based hot-reload is POSIX-only; skip on platforms that lack it
    # (e.g. Windows has no signal.SIGHUP attribute).
    try:
        signal.signal(signal.SIGHUP, _handle_sighup)
    except (OSError, ValueError, AttributeError):
        pass

    try:
        ps.start(blocking=True)
    finally:
        _pid_path.unlink(missing_ok=True)
        print("[tokenpak] PID file removed — clean exit.", flush=True)


if __name__ == "__main__":
    main()
