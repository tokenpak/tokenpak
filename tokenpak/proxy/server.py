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
    TOKENPAK_COMPACT_THRESHOLD_TOKENS (default 4500)
    TOKENPAK_DB            (default .tokenpak/monitor.db)
    NOTIFY_SOCKET          systemd sd_notify socket path (set by systemd, not TokenPak)

See tokenpak/proxy/route_policy.py for the per-route behavior matrix.
"""
from __future__ import annotations

import gzip
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional
from urllib.parse import urlparse

import httpx

from tokenpak import __version__ as _tokenpak_version
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
from .streaming import extract_sse_tokens

# ---------------------------------------------------------------------------
# Upstream retry configuration — transparent retry on transient 5xx and
# protocol errors so the Claude CLI never sees the mid-stream disconnects
# Anthropic currently produces at ~15% on large requests.
# ---------------------------------------------------------------------------
MAX_UPSTREAM_RETRIES: int = int(os.environ.get("TOKENPAK_UPSTREAM_RETRIES", "3"))

_RETRYABLE_UPSTREAM_EXCEPTIONS: tuple = (
    httpx.RemoteProtocolError,
    httpx.LocalProtocolError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)


def _is_retryable_upstream_status(status_code: int) -> bool:
    return status_code in (502, 503, 504)


def _upstream_retry_backoff(attempt: int) -> float:
    # 0.2s, 0.6s, 1.8s — bounded
    return min(2.5, 0.2 * (3 ** attempt))


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
_UPSTREAM_ACQUIRE_TIMEOUT: float = float(
    os.environ.get("TOKENPAK_UPSTREAM_ACQUIRE_TIMEOUT", "30")
)

import threading as _threading

_upstream_semaphores: Dict[tuple, _threading.BoundedSemaphore] = {}
_upstream_sem_lock = _threading.Lock()
_upstream_inflight: Dict[tuple, int] = {}


def _get_upstream_semaphore(
    provider: str, session_key: Optional[str] = None
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
            _upstream_inflight[key] = 0
    return sem


def _upstream_inflight_delta(
    provider: str, delta: int, session_key: Optional[str] = None
) -> int:
    """Adjust and return the in-flight counter for (provider, session)."""
    key = (provider or "_unknown", session_key or "_shared")
    with _upstream_sem_lock:
        _upstream_inflight[key] = max(0, _upstream_inflight.get(key, 0) + delta)
        return _upstream_inflight[key]


def get_upstream_inflight_snapshot() -> Dict[str, int]:
    """Return a snapshot of current in-flight counts, for /health exposure.

    Keyed as ``"<provider>::<session>"`` for JSON-friendliness. The
    legacy shared path appears under ``"<provider>::_shared"``.
    """
    with _upstream_sem_lock:
        return {f"{prov}::{sess}": n for (prov, sess), n in _upstream_inflight.items()}


# ---------------------------------------------------------------------------
# Codex OAuth credentials — read from ~/.codex/auth.json (cached, file-mtime-based)
# ---------------------------------------------------------------------------

_CODEX_AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
_CODEX_CREDS_CACHE: dict = {"mtime": 0.0, "access_token": "", "account_id": ""}
_CODEX_CREDS_LOCK = _threading.Lock()


def _load_codex_credentials() -> tuple:
    """Load Codex OAuth token from ~/.codex/auth.json (file-mtime cached)."""
    try:
        st = os.stat(_CODEX_AUTH_PATH)
    except OSError:
        return "", ""
    with _CODEX_CREDS_LOCK:
        if st.st_mtime != _CODEX_CREDS_CACHE["mtime"]:
            try:
                with open(_CODEX_AUTH_PATH, "r") as f:
                    data = json.load(f)
                tokens = data.get("tokens", {}) if isinstance(data, dict) else {}
                _CODEX_CREDS_CACHE["access_token"] = tokens.get("access_token", "") or ""
                _CODEX_CREDS_CACHE["account_id"] = tokens.get("account_id", "") or ""
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
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


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
    stages: List[StageTrace] = field(default_factory=list)
    status: str = "pending"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["stages"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in self.stages]
        return d


class TraceStorage:
    """Thread-safe storage for recent pipeline traces."""

    def __init__(self, max_traces: int = 10):
        self._traces: deque = deque(maxlen=max_traces)
        self._lock = threading.Lock()
        self._by_id: Dict[str, PipelineTrace] = {}

    def store(self, trace: PipelineTrace) -> None:
        with self._lock:
            self._traces.append(trace)
            self._by_id[trace.request_id] = trace
            if len(self._by_id) > len(self._traces) * 2:
                valid_ids = {t.request_id for t in self._traces}
                self._by_id = {k: v for k, v in self._by_id.items() if k in valid_ids}

    def get_last(self) -> Optional[PipelineTrace]:
        with self._lock:
            return self._traces[-1] if self._traces else None

    def get_by_id(self, request_id: str) -> Optional[PipelineTrace]:
        with self._lock:
            return self._by_id.get(request_id)

    def get_all(self) -> List[PipelineTrace]:
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
    def track_request(self) -> Generator[None, None, None]:
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

def _new_session() -> Dict[str, Any]:
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
    }


# ---------------------------------------------------------------------------
# Request latency tracking (rolling window, used by /v1/messages/forecast)
# Shared with forecast_endpoint module so both reads and writes use same buffer.
# ---------------------------------------------------------------------------
from tokenpak.proxy.forecast_endpoint import (  # noqa: E402
    _forecast_latencies,
    _forecast_latency_lock,
)

# ---------------------------------------------------------------------------
# Threaded HTTP server
# ---------------------------------------------------------------------------

class _ThreadedHTTPServer(HTTPServer):
    """HTTP server that dispatches each request to a daemon thread."""

    proxy_server: "ProxyServer"  # injected after construction

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


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
        return self.server.proxy_server  # type: ignore[attr-defined]

    def log_message(self, format, *args):  # silence access log
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
            self._tokenpak_user_id: Optional[str] = None
        if not hasattr(self, "_tokenpak_proxy_auth_header"):
            self._tokenpak_proxy_auth_header: Optional[str] = None
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

    def send_error(self, code, message=None, explain=None):
        # Override the stdlib HTML error page. Every client hitting this
        # proxy is expecting an API-style JSON error, and leaking the
        # default HTML to a chat bot or SDK produces garbage output.
        try:
            short, long = self.responses.get(code, ("Unknown", "Unknown"))
        except Exception:
            short, long = "Error", ""
        body = json.dumps({
            "error": {
                "type": "proxy_error",
                "code": code,
                "message": message or short,
                "detail": explain or long,
            }
        }).encode("utf-8")
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

    def do_CONNECT(self):
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

    def do_GET(self):
        if not self._enforce_proxy_auth():
            return
        ps = self.server.proxy_server
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
            # App endpoint dispatch must never break the LLM passthrough.
            import sys as _sys
            print(f"[tokenpak] /tpk/v1 dispatch error: {_exc}", file=_sys.stderr)

        # Always allow /health during shutdown (needed for health-check polling)
        if path == "/health" or path.startswith("/health?"):
            from urllib.parse import parse_qs
            from urllib.parse import urlparse as _urlparse
            parsed_path = _urlparse(path)
            qs = parse_qs(parsed_path.query)
            deep = qs.get("deep", ["false"])[0].lower() in ("true", "1", "yes")
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
                dashboard_path = '/'

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
            self._send_json({
                "enabled": registry.enabled,
                "circuit_breakers": registry.all_statuses(),
                "upstream_concurrency": {
                    "limit_per_provider": _UPSTREAM_CONCURRENCY,
                    "acquire_timeout_seconds": _UPSTREAM_ACQUIRE_TIMEOUT,
                    "in_flight": get_upstream_inflight_snapshot(),
                },
                "session_client_pool": _sess_pool_info,
            })
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
            qs = ""
            if "?" in path:
                _, qs = path.split("?", 1)
            ps = self.server.proxy_server
            try:
                params = FilterParams.from_query_string(qs)
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
            result = sf.query(params)
            models = sf.distinct_models()
            result["models"] = models
            self._send_json(result)
            return
        if path.startswith("http"):
            self._proxy_to(path, "GET")
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._enforce_proxy_auth():
            return
        ps = self.server.proxy_server

        # App-level /tpk/v1/* POST endpoints — reserved for future compress,
        # optimize, budget event, journal write, etc. See app_endpoints.py.
        try:
            from tokenpak.proxy.app_endpoints import try_handle_post as _tp_try_post
            if _tp_try_post(self):
                return
        except Exception as _exc:
            import sys as _sys
            print(f"[tokenpak] /tpk/v1 POST dispatch error: {_exc}", file=_sys.stderr)

        if ps.shutdown.is_shutting_down and (
            self.path.startswith("http") or self.path.startswith("/v1/")
        ):
            self._send_503_shutdown()
            return
        if self.path.startswith("http"):
            self._proxy_to(self.path, "POST")
        elif self.path == "/v1/export/csv":
            # CSV export endpoint — reads body, returns downloadable CSV
            ps = self.server.proxy_server
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
            # CCG-05: Default passthrough for unrecognised /v1/messages/* subpaths.
            # Forwards body + headers to upstream untouched (guards future Anthropic API additions).
            ps = self.server.proxy_server
            route = ps.router.route(self.path, dict(self.headers))
            self._proxy_to(route.full_url, "POST")
        elif self.path.startswith("/v1/"):
            ps = self.server.proxy_server
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

    def do_PUT(self):
        if not self._enforce_proxy_auth():
            return
        ps = self.server.proxy_server
        if ps.shutdown.is_shutting_down and self.path.startswith("http"):
            self._send_503_shutdown()
            return
        if self.path.startswith("http"):
            self._proxy_to(self.path, "PUT")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if not self._enforce_proxy_auth():
            return
        ps = self.server.proxy_server
        if ps.shutdown.is_shutting_down and self.path.startswith("http"):
            self._send_503_shutdown()
            return
        if self.path.startswith("http"):
            self._proxy_to(self.path, "DELETE")
        else:
            self.send_error(404)

    def _send_503_shutdown(self) -> None:
        """Return 503 Service Unavailable during graceful shutdown drain."""
        body = json.dumps({
            "error": {
                "type": "service_unavailable",
                "message": (
                    "TokenPak proxy is shutting down. "
                    "Please retry your request against a new proxy instance."
                ),
            }
        }).encode()
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
        ps = self.server.proxy_server  # type: ignore[attr-defined]
        with ps.shutdown.track_request():
            self._proxy_to_inner(target_url, method)

    def _proxy_to_inner(self, target_url: str, method: str) -> None:
        t0 = time.time()
        # Request ID: honour X-Request-ID from client, else generate UUID
        _req_id = _new_request_id(dict(self.headers))
        ps = self.server.proxy_server  # type: ignore[attr-defined]
        parsed = urlparse(target_url)

        should_log = any(h in target_url for h in INTERCEPT_HOSTS)
        is_messages = "/messages" in target_url or "/chat/completions" in target_url

        content_length = int(self.headers.get("Content-Length", 0))
        body: Optional[bytes] = self.rfile.read(content_length) if content_length > 0 else None

        model = "unknown"
        input_tokens = 0
        sent_input_tokens = 0
        protected_tokens = 0
        is_streaming = False
        cache_read_tokens = 0
        cache_creation_tokens = 0

        trace: Optional[PipelineTrace] = None
        if should_log and is_messages:
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
        if _adapters_enabled and should_log and is_messages:
            import logging as _logging
            _platform = detect_platform()
            _logging.debug(
                "tokenpak.proxy: detected platform=%s for request to %s",
                _platform,
                target_url[:60],
            )

        # ── DLP outbound secret scan ──────────────────────────────────────────
        # Scans the raw request body for secrets before compression/forwarding.
        # Default: TOKENPAK_DLP_ENABLED=1, TOKENPAK_DLP_MODE=warn (log only).
        # Opt-out: TOKENPAK_DLP_ENABLED=0
        if os.environ.get("TOKENPAK_DLP_ENABLED", "1") != "0" and should_log and is_messages and body:
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
                        _dlp_err = json.dumps({
                            "error": {
                                "type": "dlp_block",
                                "message": (
                                    f"Request blocked by DLP scanner: "
                                    f"{len(_dlp_findings)} secret(s) detected in outbound "
                                    "prompt. Remove secrets before retrying."
                                ),
                                "rule_ids": [f.rule_id for f in _dlp_findings],
                            }
                        }).encode()
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
                    type(_dlp_exc).__name__, _dlp_exc,
                )

        # Run compression pipeline hook if registered
        if should_log and is_messages and body:
            try:
                route = ps.router.route(target_url, dict(self.headers), body)
                model = route.model
            except Exception:
                pass
            input_tokens = _estimate_tokens_from_body(body)

            # TIP-03: Observe-only optimization pipeline.
            # Pipeline composition lives in services/optimization/ per
            # 01-architecture-standard.md §1.3 invariant 1 (services/ owns
            # all pipeline composition). proxy/server.py only invokes it
            # over the byte-preserved request. Gated on
            # TOKENPAK_OPTIMIZATION_PIPELINE (default off); runs before
            # any body-mutating stage and never returns a different body
            # in observe-only mode. Trace is stashed locally for future
            # telemetry persistence (TIP-04+).
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
                        len(_body_before_opt or b""), len(body or b""),
                    )
                    body = _body_before_opt
            except Exception as _opt_exc:
                # Fail-open: optimization scaffolding must never break a request.
                import logging as _opt_log
                _opt_log.getLogger(__name__).debug(
                    "optimization.pipeline: skipped (%s: %s)",
                    type(_opt_exc).__name__, _opt_exc,
                )

            # CCG-11: Cache invalidator detection (log-only).
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
                "streamGenerateContent" in target_url
                or "alt=sse" in target_url
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
                        type(hook_err).__name__, hook_err,
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
        if should_log and is_messages:
            _cb_provider = provider_from_url(target_url)
            _cb_registry = get_circuit_breaker_registry()
            if not _cb_registry.allow_request(_cb_provider):
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "tokenpak: circuit breaker OPEN for %s — fast-failing request",
                    _cb_provider,
                )
                err = json.dumps({
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
                }).encode()
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
        if should_log and is_messages and _cb_provider:
            if get_rate_limit_registry().is_open(_cb_provider):
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "tokenpak: rate-limit circuit open for %s — returning 503",
                    _cb_provider,
                )
                err = json.dumps({
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
                }).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.send_header("Retry-After", "30")
                self.end_headers()
                self.wfile.write(err)
                return

        # Validate credentials for intercepted provider requests
        # Client-supplied key takes precedence over any environment-level key.
        if should_log and is_messages:
            passthrough_cfg = PassthroughConfig(require_auth=True)
            auth_ok, auth_err = validate_auth(dict(self.headers), passthrough_cfg)
            if not auth_ok:
                import json as _json
                err_body = _json.dumps({
                    "error": {
                        "type": "authentication_error",
                        "message": auth_err,
                    }
                }).encode()
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
        # CCG-04: For Anthropic routes apply a per-route allowlist (mirroring
        # the WS-path tuple).  All other providers keep the existing blocklist
        # path (forward_headers) — their forwarding behavior is unchanged.
        if provider_from_url(target_url) == "anthropic":
            _route = _classify_route(self.path, self.headers)
            _allowlist = (
                CLAUDE_CODE_HEADER_ALLOWLIST
                if _route == "claude-code"
                else LEGACY_HEADER_ALLOWLIST
            )
            fwd_headers = {}
            for _hk, _hv in self.headers.items():
                if _hk.lower() in _allowlist:
                    fwd_headers[_hk.lower()] = _hv
        else:
            fwd_headers = forward_headers(dict(self.headers), passthrough_cfg)
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
            _router_injected = _creds_router_inject(
                fwd_headers, target_url, dict(self.headers)
            )
        except Exception:
            _router_injected = False  # fail-open

        # ── Codex OAuth credential injection (legacy default path) ───
        # OpenAI Codex (Responses API) routes need OAuth token from
        # ~/.codex/auth.json — similar to how Claude Code uses subscription auth.
        _upstream_provider = provider_from_url(target_url)
        if not _router_injected and _upstream_provider == "openai" and (
            "/v1/responses" in target_url
            or "codex" in target_url.lower()
            or "codex" in model.lower()
            or fwd_headers.get("openai-beta", "") == "responses=experimental"
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
        if _router_injected and _upstream_provider == "openai" and (
            "/v1/responses" in target_url
            or "codex" in target_url.lower()
            or "codex" in model.lower()
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
        _session_key: Optional[str] = None
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
            err_body = json.dumps({
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
            }).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.send_header("Retry-After", "5")
            self.end_headers()
            self.wfile.write(err_body)
            return

        try:
            pool = self.server.proxy_server._connection_pool  # type: ignore[attr-defined]
            _cb_success = False  # track whether request succeeded for circuit breaker

            output_tokens = 0
            if is_streaming:
                # ── Streaming (SSE) path ──────────────────────────────────
                # Use pool.stream() so the connection is kept alive after SSE ends.
                # Retry transient upstream failures BEFORE any bytes are written
                # to the client — once the SSE stream has started flowing to the
                # CLI it's no longer safe to retry (would cause `Unterminated
                # string` JSON parse errors in the client's SSE reader).
                sse_buffer = b""
                _stream_wrote_to_client = False
                for _ustream_attempt in range(MAX_UPSTREAM_RETRIES):
                    _stream_retry = False
                    try:
                        with pool.stream(method, target_url, content=body, headers=fwd_headers, session_key=_session_key) as resp:
                            if (
                                _is_retryable_upstream_status(resp.status_code)
                                and not _stream_wrote_to_client
                                and _ustream_attempt < MAX_UPSTREAM_RETRIES - 1
                            ):
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
                                    if h_lower in ("connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding"):
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

                                for chunk in resp.iter_bytes(chunk_size=4096):
                                    if not chunk:
                                        continue
                                    try:
                                        self.wfile.write(chunk)
                                        self.wfile.flush()
                                    except (BrokenPipeError, ConnectionResetError):
                                        break
                                    if should_log and is_messages:
                                        sse_buffer += chunk
                    except _RETRYABLE_UPSTREAM_EXCEPTIONS:
                        # Once we've committed to writing to the client, can't retry —
                        # the CLI's SSE parser would see a truncated-then-restarted stream.
                        if _stream_wrote_to_client:
                            raise
                        if _ustream_attempt >= MAX_UPSTREAM_RETRIES - 1:
                            raise
                        _stream_retry = True

                    if _stream_retry:
                        time.sleep(_upstream_retry_backoff(_ustream_attempt))
                        continue
                    break

                if should_log and is_messages and sse_buffer:
                    sse_usage = extract_sse_tokens(sse_buffer)
                    output_tokens = sse_usage.get("output_tokens", 0)
                    cache_read_tokens = sse_usage.get("cache_read_input_tokens", 0)
                    cache_creation_tokens = sse_usage.get("cache_creation_input_tokens", 0)
            else:
                # ── Non-streaming path ────────────────────────────────────
                # Retry on transient upstream failures (RemoteProtocolError,
                # Server disconnected, 502/503/504). Safe because the client
                # has not yet received any bytes at this point.
                resp = None
                for _ustream_attempt in range(MAX_UPSTREAM_RETRIES):
                    try:
                        resp = pool.request(method, target_url, content=body, headers=fwd_headers, session_key=_session_key)
                        if (
                            _is_retryable_upstream_status(resp.status_code)
                            and _ustream_attempt < MAX_UPSTREAM_RETRIES - 1
                        ):
                            try:
                                resp.close()
                            except Exception:
                                pass
                            time.sleep(_upstream_retry_backoff(_ustream_attempt))
                            continue
                        break
                    except _RETRYABLE_UPSTREAM_EXCEPTIONS:
                        if _ustream_attempt >= MAX_UPSTREAM_RETRIES - 1:
                            raise
                        time.sleep(_upstream_retry_backoff(_ustream_attempt))
                        continue
                assert resp is not None

                # Normalize upstream 4xx/5xx to canonical error envelope before
                # sending headers so we can set the correct Content-Type.
                resp_body = resp.content
                try:
                    from tokenpak.proxy.request import ProxyResponse as _PResp
                    _upstream_response = _PResp(
                        status_code=resp.status_code,
                        headers=dict(resp.headers),
                        body=resp_body,
                    )
                except Exception:
                    _upstream_response = None  # type: ignore[assignment]
                _is_upstream_error = resp.status_code >= 400
                if _is_upstream_error:
                    resp_body = normalize_upstream_error(
                        resp.status_code, resp_body, provider_from_url(target_url)
                    )

                _client_headers_sent = True
                self.send_response(resp.status_code)
                for h_key, h_val in resp.headers.items():
                    h_lower = h_key.lower()
                    if h_lower in ("connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding"):
                        continue
                    if _is_upstream_error and h_lower == "content-type":
                        continue  # overridden below
                    self.send_header(h_key, h_val)
                if _is_upstream_error:
                    self.send_header("Content-Type", "application/json")
                # Debug header: stable prefix hash for cache determinism verification.
                # Emitted for all messages requests (not just intercepted hosts)
                # so integration tests and local stubs can verify determinism.
                if is_messages:
                    _ph = _compute_stable_prefix_hash(body)
                    if _ph:
                        self.send_header("X-Tokenpak-Cache-Prefix-Hash", _ph)
                # Propagate request ID to client for correlation
                self.send_header("X-Request-ID", _req_id)
                self.end_headers()

                self.wfile.write(resp_body)
                self.wfile.flush()

                if should_log and is_messages:
                    body_for_metrics = resp_body
                    if "gzip" in resp.headers.get("content-encoding", ""):
                        try:
                            body_for_metrics = gzip.decompress(resp_body)
                        except Exception:
                            pass
                    output_tokens = _extract_response_tokens(body_for_metrics)
                    try:
                        usage = json.loads(body_for_metrics).get("usage", {})
                        cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
                    except Exception:
                        pass
            latency_ms = int((time.time() - t0) * 1000)
            with _forecast_latency_lock:
                _forecast_latencies.append(latency_ms)
            _cb_success = True  # reached here without exception → request succeeded

            # ── Request logging ───────────────────────────────────────────
            try:
                _resp_status = resp.status_code if "resp" in dir() else 0  # type: ignore
                _req_body_sz = content_length
                _resp_body_sz = len(resp_body) if "resp_body" in dir() else 0  # type: ignore
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
                _log_extra: Dict[str, Any] = {}
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

            if should_log and is_messages and input_tokens > 0:
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
                    cost = estimate_cost(model, sent_input_tokens, output_tokens,
                                         cache_read_tokens, cache_creation_tokens)
                    cost_without = estimate_cost(model, input_tokens, output_tokens,
                                                 cache_read_tokens, cache_creation_tokens)
                saved = max(0, input_tokens - sent_input_tokens)
                cost_saved = max(0.0, cost_without - cost)

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
                    ps.session[f"cache_read_{_cache_origin}"] += cache_read_tokens

                # Persist to monitor.db so `tokenpak status`, dashboards, and
                # cross-session reporting see this request. Async write queue
                # keeps this call <0.1ms. Fail-open.
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
                            would_have_saved=int(saved),
                            cache_origin=_cache_origin,
                            user_id=getattr(self, "_tokenpak_user_id", "") or "",
                        )
                    except Exception:
                        pass  # DB errors must never break the request

                # Record cache telemetry
                try:
                    _stable_tokens = max(0, input_tokens - (input_tokens - sent_input_tokens))
                    _miss_reason: Optional[str] = None
                    if cache_read_tokens == 0:
                        # Heuristic miss-reason diagnosis (best-effort)
                        try:
                            _body_text = body.decode("utf-8", errors="ignore") if isinstance(body, (bytes, bytearray)) else ""
                        except Exception:
                            _body_text = ""
                        import re as _re
                        if _re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", _body_text):
                            _miss_reason = "timestamp"
                        elif "request_id" in _body_text.lower() or "uuid" in _body_text.lower():
                            _miss_reason = "uuid"
                    _get_cache_collector().record(CacheMetrics(
                        request_id=trace.request_id if trace else str(uuid.uuid4()),
                        stable_prefix_tokens=sent_input_tokens,
                        stable_cached=(cache_read_tokens > 0),
                        cache_miss_reason=_miss_reason,
                        volatile_tail_tokens=max(0, input_tokens - sent_input_tokens),
                        total_input_tokens=input_tokens,
                        cache_read_tokens=cache_read_tokens,
                        cache_creation_tokens=cache_creation_tokens,
                        output_tokens=output_tokens,
                    ))
                except Exception:
                    pass  # telemetry must never break request handling

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
                        "percent_saved": round(saved / input_tokens * 100, 1) if input_tokens else 0.0,
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
                # failure is recorded in except block

        except Exception as exc:
            # ── Circuit breaker: record failure ───────────────────────────
            if _cb_registry is not None and _cb_provider is not None:
                _cb_registry.record_failure(_cb_provider)

            with ps._session_lock:
                ps.session["errors"] += 1
            latency_ms = int((time.time() - t0) * 1000)
            # Record error event in compression telemetry if this was an intercepted request
            if should_log and is_messages and input_tokens > 0:
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
                _err_extra: Dict[str, Any] = {"error": exc_type, "error_message": exc_msg[:200]}
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
                            _sse_err = (
                                "\n\n"
                                "event: error\n"
                                "data: " + json.dumps({
                                    "type": "error",
                                    "error": {
                                        "type": "overloaded_error",
                                        "message": (
                                            f"Upstream connection dropped mid-stream "
                                            f"({exc_type}). Retry the request."
                                        ),
                                    },
                                }) + "\n\n"
                            ).encode("utf-8")
                            self.wfile.write(_sse_err)
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                    # Non-streaming: headers+partial body already flushed; nothing
                    # safe to append. Just let the connection close.
                else:
                    err = json.dumps({
                        "error": {
                            "type": "proxy_error",
                            "message": user_detail,
                            "detail": exc_msg,
                            "hint": "Run `tokenpak doctor` for diagnostics or `tokenpak status` for recent errors.",
                        }
                    }).encode()
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
        """CCG-05: Handle POST /v1/messages/count_tokens — compute token count locally.

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
                {"error": {"type": "invalid_request_error", "message": f"Request body is not valid JSON: {exc}"}}
            ).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            err = json.dumps(
                {"error": {"type": "invalid_request_error", "message": "Request body must include a 'messages' array"}}
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
        """CCI-11: Handle POST /v1/messages/forecast — local cost forecast, no upstream call.

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
            body = json.dumps(
                {"error": {"type": "invalid_request_error", "message": msg}}
            ).encode()
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

            _log.info("claude-code backend: session=%s model=%s stream=%s workspace=%s",
                       oc_session, body_data.get("model", "?"), is_streaming,
                       oc_workspace or "(default)")

            result = execute_via_claude_code(
                openclaw_session=oc_session,
                messages=body_data.get("messages", []),
                model=body_data.get("model", "claude-sonnet-4-6"),
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

    def _send_claude_code_sse(self, result: dict) -> None:
        """Convert a complete Anthropic-format response dict to SSE stream.

        Emits the three events OpenClaw expects:
          1. message_start — contains the message shell + usage.input_tokens
          2. content_block_delta — contains the assistant text
          3. message_delta — contains stop_reason + usage.output_tokens
        """
        msg_id = result.get("id", "msg_unknown")
        model = result.get("model", "claude-sonnet-4-6")
        usage = result.get("usage", {})
        content = result.get("content", [])
        text = ""
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                break

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def _sse(event: str, data: dict) -> None:
            line = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            self.wfile.write(line.encode())
            self.wfile.flush()

        # 1. message_start
        _sse("message_start", {
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
        })

        # 2. content_block_start
        _sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })

        # 3. content_block_delta — send text in one chunk
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        })

        # 4. content_block_stop
        _sse("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        })

        # 5. message_delta — stop reason + output tokens
        _sse("message_delta", {
            "type": "message_delta",
            "delta": {
                "stop_reason": result.get("stop_reason", "end_turn"),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": usage.get("output_tokens", 0)},
        })

        # 6. message_stop
        _sse("message_stop", {"type": "message_stop"})

        # Signal end of stream and close connection
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        try:
            self.wfile.close()
        except Exception:
            pass

    def _send_json(self, data: dict) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_metrics_dashboard(self) -> None:
        """GET /metrics/dashboard — dashboard JSON with top-20 sessions panel.

        Returns a JSON object with a `sessions` array of the top 20 sessions
        by request count. Each entry contains the columns documented in
        Spec Component 11 / CCG-13.
        """
        import os
        import sqlite3 as _sqlite3

        ps = self.server.proxy_server
        db_path = (
            ps.monitor.db_path
            if getattr(ps, "monitor", None) is not None
            else os.path.expanduser("~/.tokenpak/monitor.db")
        )

        sessions: list = []
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

                sessions.append({
                    "session_id": sid,
                    "input_tokens": row["input_tokens"] or 0,
                    "output_tokens": row["output_tokens"] or 0,
                    "cache_read_input_tokens": row["cache_read_input_tokens"] or 0,
                    "cache_creation_input_tokens": row["cache_creation_input_tokens"] or 0,
                    "cost": round(row["cost"] or 0.0, 6),
                    "request_count": row["request_count"] or 0,
                    "latency_p50": p50,
                    "platform": row["platform"] or "unknown",
                })
            conn.close()
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "metrics/dashboard sessions query failed: %s", exc
            )

        self._send_json({"sessions": sessions})


# ---------------------------------------------------------------------------
# Token helpers (lightweight, no heavy deps)
# ---------------------------------------------------------------------------

def _compute_stable_prefix_hash(body: Optional[bytes]) -> str:
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
        messages = data.get("messages", [])
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


def _extract_response_tokens(body: bytes) -> int:
    try:
        data = json.loads(body)
        usage = data.get("usage", {})
        return (
            usage.get("output_tokens") or
            usage.get("completion_tokens") or
            usage.get("total_tokens", 0)
        )
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# ProxyServer — public API
# ---------------------------------------------------------------------------

def auto_detect_upstream(request_headers: dict) -> str:
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
        port: Optional[int] = None,
        compilation_mode: Optional[str] = None,
        request_hook: Optional[Callable] = None,
        shutdown_timeout: Optional[float] = None,
    ):
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

        # Connection pool — shared across all handler threads
        self._connection_pool = ConnectionPool(PoolConfig.from_env())

        # Auto-wire the capsule builder hook.  When TOKENPAK_CAPSULE_BUILDER=0
        # (the default) the hook is a no-op, so this is safe for all deployments.
        # If a caller supplies their own request_hook it is chained *after* the
        # capsule stage so they still see the (potentially compressed) body.
        try:
            from .capsule_integration import get_capsule_request_hook
            self.request_hook: Optional[Callable] = get_capsule_request_hook(
                base_hook=request_hook
            )
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
                trace=None,
                *,
                _hook=_prior_hook,
                _scc=apply_stable_cache_control,
            ):
                if _hook is not None:
                    body, sent, raw, protected = _hook(body, model, trace)
                else:
                    _tok = len(body) // 4
                    body, sent, raw, protected = body, _tok, _tok, 0
                body = _scc(body)
                return body, sent, raw, protected

            self.request_hook = _stable_cache_hook
        except Exception:  # pragma: no cover — import failure gracefully degrades
            pass

        self.router = ProviderRouter()
        self.trace_storage = TraceStorage(max_traces=50)
        self.session_filter = SessionFilter()
        self.session: Dict[str, Any] = _new_session()
        self._session_lock = threading.Lock()
        self._last_request: Optional[Dict[str, Any]] = None
        self._last_lock = threading.Lock()
        self._server: Optional[_ThreadedHTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        # Rolling window of per-request compression ratios (last 100)
        self._compression_ratios: deque = deque(maxlen=100)
        self._compression_lock = threading.Lock()
        # Compression telemetry — writes events to ~/.tokenpak/compression_events.jsonl
        self.compression_stats = CompressionStats()

        # SQLite request ledger — writes to ~/.tokenpak/monitor.db (symlink target).
        # Powers `tokenpak status`, `savings`, dashboards. Async write queue keeps
        # per-request cost <0.1ms. Fail-open: any DB error never breaks the proxy.
        try:
            _db_path = os.environ.get(
                "TOKENPAK_DB",
                os.path.expanduser("~/.tokenpak/monitor.db"),
            )
            self.monitor: Optional[_DbMonitor] = _DbMonitor(_db_path)
        except Exception:
            self.monitor = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, blocking: bool = True) -> None:
        """Start the proxy server."""
        # --- Startup self-test ---
        _all_ok, _warnings = run_startup_checks(self.port)
        if _warnings:
            report = format_startup_report(_warnings, _all_ok)
            print(report)
            # Track non-fatal startup warnings in the degradation log
            for w in _warnings:
                get_degradation_tracker().record(
                    DegradationEventType.STARTUP_WARNING, w, recovered=_all_ok
                )
        # --------------------------

        server = _ThreadedHTTPServer((self.host, self.port), _ProxyHandler)
        server.proxy_server = self  # inject back-reference
        self._server = server

        if blocking:
            # Install signal handlers only in the main thread (signal module restriction)
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, self._handle_signal)
                signal.signal(signal.SIGINT, self._handle_signal)

            print(f"TokenPak proxy listening on {self.host}:{self.port} [{self.compilation_mode}]")
            print("  ✓ Zero-config mode enabled (auto-detecting upstream from request headers)")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass  # SIGINT handled via _handle_signal → stop()
            finally:
                # Ensure stop is called even if serve_forever exits unexpectedly
                if self._server is not None:
                    self.stop()
        else:
            self._server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            self._server_thread.start()

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Signal handler for SIGTERM/SIGINT — triggers graceful shutdown."""
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\nTokenPak: {sig_name} received — starting graceful shutdown "
              f"(drain timeout: {self.shutdown_timeout:.0f}s)...", flush=True)
        # Run stop() in a background thread so the signal handler returns quickly
        t = threading.Thread(target=self.stop, daemon=True)
        t.start()

    def stop(self) -> None:
        """
        Gracefully shut down the proxy server.

        Sequence:
          1. Stop accepting new requests (return 503 for any new proxied calls)
          2. Drain in-flight requests (up to ``shutdown_timeout`` seconds)
          3. Flush telemetry buffer to disk
          4. Close the HTTP connection pool
          5. Stop the HTTP server
        """
        # Always close the pool, even if server wasn't started
        if self._connection_pool is not None:
            try:
                self._connection_pool.close()
            except Exception:
                pass

        if self._server is None:
            return  # already stopped

        # ── Step 1: Stop accepting new proxy requests ─────────────────────
        self.shutdown.begin()
        print("TokenPak: shutdown step 1/5 — rejecting new requests (503)", flush=True)

        # ── Step 2: Drain in-flight requests ──────────────────────────────
        in_flight = self.shutdown.in_flight_count()
        if in_flight > 0:
            print(
                f"TokenPak: shutdown step 2/5 — draining {in_flight} in-flight request(s) "
                f"(timeout: {self.shutdown_timeout:.0f}s)...",
                flush=True,
            )
        else:
            print("TokenPak: shutdown step 2/5 — no in-flight requests, proceeding", flush=True)

        drained = self.shutdown.wait_for_drain(timeout=self.shutdown_timeout)
        if not drained:
            remaining = self.shutdown.in_flight_count()
            print(
                f"TokenPak: shutdown drain timed out after {self.shutdown_timeout:.0f}s "
                f"({remaining} request(s) still active — forcing close)",
                flush=True,
            )
        else:
            print("TokenPak: shutdown step 2/5 — all requests drained ✓", flush=True)

        # ── Step 3: Flush telemetry buffer to disk ─────────────────────────
        print("TokenPak: shutdown step 3/5 — flushing telemetry...", flush=True)
        try:
            self._flush_telemetry()
            print("TokenPak: shutdown step 3/5 — telemetry flushed ✓", flush=True)
        except Exception as exc:
            print(f"TokenPak: shutdown step 3/5 — telemetry flush error (non-fatal): {exc}",
                  flush=True)

        # ── Step 4: Close HTTP connection pool ────────────────────────────
        print("TokenPak: shutdown step 4/5 — closing connection pool...", flush=True)
        try:
            self._connection_pool.close()
            print("TokenPak: shutdown step 4/5 — connection pool closed ✓", flush=True)
        except Exception as exc:
            print(f"TokenPak: shutdown step 4/5 — pool close error (non-fatal): {exc}",
                  flush=True)

        # ── Step 5: Stop HTTP server ───────────────────────────────────────
        print("TokenPak: shutdown step 5/5 — stopping HTTP server...", flush=True)
        srv = self._server
        self._server = None
        try:
            srv.shutdown()
            print("TokenPak: shutdown step 5/5 — HTTP server stopped ✓", flush=True)
        except Exception as exc:
            print(f"TokenPak: shutdown step 5/5 — server stop error (non-fatal): {exc}",
                  flush=True)

        print("TokenPak: graceful shutdown complete.", flush=True)

    def _flush_telemetry(self) -> None:
        """
        Flush any buffered telemetry to disk before process exit.

        Writes a shutdown summary entry to the compression events JSONL file
        so stats from the current session are preserved across restarts.
        """
        shutdown_record = {
            "event": "shutdown",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "session_requests": self.session.get("requests", 0),
            "session_tokens_saved": self.session.get("saved_tokens", 0),
            "session_cost_saved": round(self.session.get("cost_saved", 0.0), 6),
            "session_cost_total": round(self.session.get("cost", 0.0), 6),
            "session_errors": self.session.get("errors", 0),
            "uptime_seconds": round(time.time() - self.session.get("start_time", time.time())),
        }
        # Delegate to the compression_stats recorder (writes to ~/.tokenpak/compression_events.jsonl)
        self.compression_stats.flush_shutdown_record(shutdown_record)

    def is_running(self) -> bool:
        return self._server is not None

    # ------------------------------------------------------------------
    # Status endpoints (also used by handler GET routes)
    # ------------------------------------------------------------------

    def health(self, deep: bool = False) -> dict:
        with self._session_lock:
            uptime = round(time.time() - self.session["start_time"])
            requests_total = self.session["requests"]
            requests_errors = self.session["errors"]
        with self._compression_lock:
            ratios = list(self._compression_ratios)
        compression_ratio_avg = round(sum(ratios) / len(ratios), 4) if ratios else 0.0
        pool_metrics = self._connection_pool.metrics()
        deg = get_degradation_tracker()
        is_degraded = deg.is_degraded()
        is_shutting_down = self.shutdown.is_shutting_down
        # Circuit breaker summary
        cb_registry = get_circuit_breaker_registry()
        cb_statuses = cb_registry.all_statuses()
        cb_any_open = any(
            s.get("state") in ("open", "half_open")
            for s in cb_statuses.values()
        )
        result = {
            "status": "shutting_down" if is_shutting_down else ("degraded" if is_degraded else "ok"),
            "uptime_seconds": uptime,
            "version": _tokenpak_version,
            "requests_total": requests_total,
            "requests_errors": requests_errors,
            "compression_ratio_avg": compression_ratio_avg,
            "is_degraded": is_degraded,
            "is_shutting_down": is_shutting_down,
            "in_flight_requests": self.shutdown.in_flight_count(),
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

            import psutil  # optional; fall back gracefully
            # providers: list active providers with their circuit-breaker status
            providers = [
                {"name": name, "status": info.get("state", "unknown")}
                for name, info in cb_statuses.items()
            ]
            # memory usage in MB
            try:
                proc = psutil.Process()
                mem_mb = round(proc.memory_info().rss / (1024 * 1024), 1)
            except Exception:
                mem_mb = None
            # disk available in GB
            try:
                disk = shutil.disk_usage("/")
                disk_available_gb = round(disk.free / (1024 ** 3), 2)
            except Exception:
                disk_available_gb = None
            result["providers"] = providers
            result["memory"] = {"rss_mb": mem_mb}
            result["disk"] = {"available_gb": disk_available_gb}
        return result

    def status(self) -> dict:
        """Return a concise operational status snapshot for GET /status."""
        with self._session_lock:
            start_time = self.session["start_time"]
            requests_total = self.session["requests"]
        uptime = round(time.time() - start_time)
        started_at = datetime.fromtimestamp(start_time, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
        providers: dict = {}
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

    def stats(self) -> dict:
        s = self.session
        return {
            "session": s,
            "compilation_mode": self.compilation_mode,
            "cache_read_by_origin": {
                "client": s.get("cache_read_client", 0),
                "proxy": s.get("cache_read_proxy", 0),
                "unknown": s.get("cache_read_unknown", 0),
            },
        }

    def session_stats(self) -> dict:
        s = self.session
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
                if s["input_tokens"] > 0 else 0.0
            ),
        }

    def last_request_stats(self) -> dict:
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
    port: Optional[int] = None,
    compilation_mode: Optional[str] = None,
    request_hook: Optional[Callable] = None,
    blocking: bool = True,
    shutdown_timeout: Optional[float] = None,
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
        "--port", type=int, default=None,
        help="Bind port (env: TOKENPAK_PORT, default: 8766)",
    )
    parser.add_argument(
        "--config", default=None, metavar="PATH",
        help="Path to config YAML (env: TOKENPAK_CONFIG, default: ~/.tokenpak/config.yaml)",
    )
    parser.add_argument(
        "--log-level", default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Python logging level (env: TOKENPAK_LOG_LEVEL, default: warning)",
    )
    parser.add_argument(
        "--profile", default=None,
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

    _log_level = (args.log_level or os.environ.get("TOKENPAK_LOG_LEVEL", "warning")).upper()
    logging.basicConfig(level=_log_level, format="%(levelname)s %(name)s: %(message)s")

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    host = os.environ.get("TOKENPAK_BIND_ADDRESS", "127.0.0.1")
    mode = os.environ.get("TOKENPAK_MODE", "hybrid")
    profile = os.environ.get("TOKENPAK_PROFILE", "balanced")

    _mode_desc = {
        "strict":     "100% lossless — no compression",
        "hybrid":     "protected/code strict, narrative compressed",
        "aggressive": "everything except protected gets compressed",
    }

    # Write PID file on startup; remove on clean shutdown.
    _pid_path = Path.home() / ".tokenpak" / "proxy.pid"
    _pid_path.parent.mkdir(parents=True, exist_ok=True)
    _pid_path.write_text(str(os.getpid()))

    from tokenpak.proxy.config import PROVIDER_DISPLAY as _provider_display
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  TokenPak Proxy  v{_tokenpak_version}
╠══════════════════════════════════════════════════════════════════╣
║  Listening:  http://{host}:{port}
║  Profile:    {profile}
║  Mode:       {mode} — {_mode_desc.get(mode, '?')}
║  Providers:  {_provider_display}
║  PID:        {os.getpid()}
║  PID file:   {_pid_path}
╚══════════════════════════════════════════════════════════════════╝
""", flush=True)

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

    def _handle_sighup(signum: int, frame) -> None:  # type: ignore[type-arg]
        """Hot-reload dynamic config from environment variables (no restart needed)."""
        print("\n[tokenpak] SIGHUP received — reloading config from env", flush=True)
        ps.compilation_mode = os.environ.get("TOKENPAK_MODE", ps.compilation_mode)
        print(f"[tokenpak] config reloaded: mode={ps.compilation_mode}", flush=True)

    signal.signal(signal.SIGHUP, _handle_sighup)

    try:
        ps.start(blocking=True)
    finally:
        _pid_path.unlink(missing_ok=True)
        print("[tokenpak] PID file removed — clean exit.", flush=True)


if __name__ == "__main__":
    main()
