"""
TokenPak Proxy Server (LEGACY)

⚠️  DEPRECATED: This module is superseded by the canonical proxy.py, which has full
    compression pipeline, cache poison removal, vault injection, tool schema
    registry, circuit breakers, and Prometheus metrics.

    Use `tokenpak start` (which now launches proxy.py) or run proxy.py
    directly. This module is kept for backward compatibility.

Core HTTP proxy server / request-handling layer. Wraps Python's built-in
HTTPServer into a multi-threaded ProxyServer with compression pipeline hooks,
session stats, and management endpoints.

Env vars (all optional):
    TOKENPAK_PORT          (default 8766)
    TOKENPAK_MODE          (default hybrid) — strict|hybrid|aggressive
    TOKENPAK_COMPACT       (default 1) — master on/off switch
    TOKENPAK_COMPACT_THRESHOLD_TOKENS (default 4500)
    TOKENPAK_DB            (default .ocp/monitor.db)
    NOTIFY_SOCKET          systemd sd_notify socket path (set by systemd, not TokenPak)
"""
from __future__ import annotations

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.proxy.server is deprecated — use proxy.py instead. "
    "Run `tokenpak start` to launch the current proxy.",
    DeprecationWarning,
    stacklevel=2,
)

import gzip
import http.client
import json
import os
import re
import signal
import socket
import ssl
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional
from urllib.parse import urlparse

import httpx

from .connection_pool import ConnectionPool, PoolConfig, get_global_pool

from .router import ProviderRouter, estimate_cost, INTERCEPT_HOSTS
from .streaming import extract_sse_tokens
from .passthrough import (
    forward_headers,
    validate_auth,
    PassthroughConfig,
    CredentialPassthrough,
    _classify_route,
    CLAUDE_CODE_HEADER_ALLOWLIST,
    LEGACY_HEADER_ALLOWLIST,
)
from .stats import CompressionStats
from .degradation import get_degradation_tracker, DegradationEventType
from .circuit_breaker import get_circuit_breaker_registry, provider_from_url
from .startup import run_startup_checks, format_startup_report
from tokenpak import __version__ as _tokenpak_version
from tokenpak.monitoring.request_logger import log_request, new_request_id as _new_request_id
from tokenpak.agent.adapters.registry import detect_platform
from tokenpak.agent.config import get_stats_footer_enabled
from tokenpak.agent.dashboard.export_api import ExportAPI
from tokenpak.agent.dashboard.session_filter import (
    SessionFilter,
    FilterParams,
    get_distinct_models,
)
from tokenpak.agent.telemetry.collector import RequestStats
from tokenpak.agent.telemetry.footer import render_footer_oneline
from tokenpak.cache.telemetry import CacheMetrics, get_collector as _get_cache_collector


# ---------------------------------------------------------------------------
# Systemd integration — read sd_notify socket path from environment
# Transferred from monolith (TPK-CONSOLIDATION-A2a, lines 7577/7601)
# ---------------------------------------------------------------------------
from tokenpak.proxy.adapters.base import FormatAdapter  # noqa: F401
from tokenpak.proxy.streaming import _extract_sse_tokens  # noqa: F401
from tokenpak.proxy.cache_poison import (  # noqa: F401
    _strip_cache_poisons,
    _classify_cache_miss_reason,
)
from tokenpak.proxy.request_pipeline import (  # noqa: F401
    _get_router,
    _get_validation_gate,
    _has_validation_gate,
    _RouterResult,
    _classify_intent,
    _extract_user_text,
    _run_router,
    _router_health,
    _health_cache,
    _HEALTH_CACHE_TTL,
    _get_route_engine,
    _get_cached_route_rules,
    _get_precond_gates,
    _get_budget_controller,
    PROTECTED_MARKERS,
    is_protected_content,
    classify_message_risk,
    can_compress,
)
from tokenpak.proxy.tracing import (  # noqa: F401
    _CompressionTimeout,
    StageTrace,
    PipelineTrace,
    TraceStorage,
    TRACE_STORAGE,
)
from tokenpak.proxy.config import (  # noqa: F401
    ACTIVE_PROFILE,
    PROXY_AUTH_KEY,
    DASHBOARD_AUTH_ENABLED,
    COMPILATION_MODE,
    ENABLE_COMPACTION,
    COMPACT_MAX_CHARS,
    COMPACT_THRESHOLD_TOKENS,
    COMPACT_MAX_TOKENS,
    COMPACT_CACHE_SIZE,
    ENABLE_CAPSULE_BUILDER,
    CAPSULE_MIN_CHARS,
    CAPSULE_HOT_WINDOW,
    ROUTER_ENABLED,
    SKELETON_ENABLED,
    SHADOW_ENABLED,
    BUDGET_TOTAL_TOKENS,
    CHAT_FOOTER_ENABLED,
    HTTP100_KEEPALIVE_ENABLED,
    SEMANTIC_CACHE_ENABLED,
    _get_sem_cache,
    PREFIX_REGISTRY_ENABLED,
    COMPRESSION_DICT_ENABLED,
    TRACE_ENABLED,
    ERROR_NORMALIZER_ENABLED,
    BUDGET_CONTROLLER_ENABLED,
    REQUEST_LOGGER_ENABLED,
    SALIENCE_ROUTER_ENABLED,
    CACHE_REGISTRY_ENABLED,
    RETRIEVAL_WATCHDOG_ENABLED,
    FAILURE_MEMORY_ENABLED,
    FIDELITY_TIERS_ENABLED,
    SESSION_CAPSULES_ENABLED,
    PRECONDITION_GATES_ENABLED,
    QUERY_REWRITER_ENABLED,
    STABILITY_SCORER_ENABLED,
    WS_PORT,
    WS_MAX_CONNECTIONS,
    _plugin_registry,
    _cache_registry,
    UPSTREAM_TIMEOUT,
    STRICT_VALIDATION,
    _POOL_MANAGER,
    VALIDATION_GATE_ENABLED,
    VALIDATION_GATE_BUDGET_CAP,
    VALIDATION_GATE_SOFT,
    INJECT_BUDGET,
    INJECT_TOP_K,
    INJECT_MIN_SCORE,
    INJECT_SKIP_MODELS,
    INJECT_MIN_PROMPT,
    MAX_COMPRESSION_TIME_MS,
    TERM_RESOLVER_ENABLED,
    TERM_RESOLVER_TOP_K,
    TERM_RESOLVER_MAX_BYTES,
    _COMPACT_CACHE,
    _COMPACT_CACHE_ORDER,
    ADAPTER_REGISTRY,
    UPSTREAM_ROUTES,
)
from tokenpak.proxy.fallback import (  # noqa: F401
    _ANTHROPIC_KEY_POOL,
    _reload_config_from_env,
    _cool_down_key,
    _get_next_key,
    _strip_empty_text_blocks,
    _cap_cache_control_blocks,
    _resolve_upstream,
    INTERCEPT_HOSTS,
    OLLAMA_UPSTREAM,
    OLLAMA_CONNECT_TIMEOUT,
    _provider_for_url,
    _circuit_check,
    _circuit_record_failure,
    _circuit_record_success,
    _sanitize_headers,
    _make_structured_error,
    _enrich_upstream_error,
    _rate_limit_check,
    _KEY_COOLDOWN_429,
    _KEY_COOLDOWN_401,
    # circuit breaker state used directly by ForwardProxyHandler
    _ollama_circuit,
    _ollama_circuit_lock,
    _provider_circuits,
    _RATE_LIMIT_RPM,
    _MAX_REQUEST_BYTES,
)

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
    }


# ---------------------------------------------------------------------------
# Request latency tracking (rolling window, used by /v1/messages/forecast)
# ---------------------------------------------------------------------------
_forecast_latencies: deque = deque(maxlen=100)
_forecast_latency_lock = threading.Lock()


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
    # CONNECT tunnelling (HTTPS MITM passthrough)
    # ------------------------------------------------------------------

    def do_CONNECT(self):
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
        ps = self.server.proxy_server
        path = self.path

        # Always allow /health during shutdown (needed for health-check polling)
        if path == "/health" or path.startswith("/health?"):
            from urllib.parse import parse_qs, urlparse as _urlparse
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
            from tokenpak.monitoring.metrics import ProxyMetricsCollector
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
        if path.startswith("/dashboard"):
            # Serve dashboard UI files
            from tokenpak.dashboard import serve_dashboard_file
            import asyncio
            
            # Extract dashboard path
            dashboard_path = path[10:]  # Remove '/dashboard' prefix
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
            self._send_json({
                "enabled": registry.enabled,
                "circuit_breakers": registry.all_statuses(),
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
            from tokenpak.goals import GoalManager
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
        ps = self.server.proxy_server
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
        else:
            self.send_error(404)

    def do_PUT(self):
        ps = self.server.proxy_server
        if ps.shutdown.is_shutting_down and self.path.startswith("http"):
            self._send_503_shutdown()
            return
        if self.path.startswith("http"):
            self._proxy_to(self.path, "PUT")
        else:
            self.send_error(404)

    def do_DELETE(self):
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
            # Start workflow tracking (no-op when feature flag is OFF)
            try:
                from tokenpak.agentic.proxy_workflow import start_proxy_workflow

        # Platform adapter detection (feature-flagged via TOKENPAK_PLATFORM_ADAPTERS, default ON)
        _adapters_enabled = os.environ.get("TOKENPAK_PLATFORM_ADAPTERS", "1") != "0"
        if _adapters_enabled and should_log and is_messages:
            import logging as _logging
            _adapter = detect_platform(dict(self.headers), dict(os.environ))
            _logging.debug(
                "tokenpak.proxy: detected platform=%s for request to %s",
                _adapter.platform_name,
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

            if ps.request_hook:
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
                    if VAULT_INDEX.available:
                        skip_injection = False
                        if INJECT_SKIP_MODELS.strip():
                            if any(
                                skip.strip() and skip.strip().lower() in model.lower()
                                for skip in INJECT_SKIP_MODELS.split(",")
                            ):
                                skip_injection = True
                        if input_tokens < INJECT_MIN_PROMPT:
                            skip_injection = True
                        if skip_injection:
                            SESSION["injection_skips"] += 1
                            vault_stage.details["skipped"] = True
                            vault_stage.details["reason"] = (
                                "model_skip"
                                if INJECT_SKIP_MODELS.strip()
                                and any(
                                    s.lower() in model.lower()
                                    for s in INJECT_SKIP_MODELS.split(",")
                                )
                                else "prompt_too_short"
                            )
                            # Even when skipping vault injection, apply cache_control to stable prefix
                            if PROMPT_BUILDER_AVAILABLE:
                                body = _apply_stable_cache_control(body)
                        else:
                            body, injected_tokens, injected_sources = inject_vault_context(
                                body, adapter=active_adapter
                            )
                            if injected_tokens > 0:
                                # Recount tokens after injection
                                _, input_tokens = extract_request_tokens(
                                    body, adapter=active_adapter
                                )
                                vault_stage.tokens_delta = injected_tokens
                                vault_stage.details["blocks_matched"] = len(injected_sources)
                                vault_stage.details["block_names"] = injected_sources[:5]  # Top 5
                                vault_stage.details["tokens_injected"] = injected_tokens
                                # Enrich with sub-step timing from inject_vault_context
                                vault_stage.details["sub_timing_ms"] = SESSION.get(
                                    "vault_last_timing_ms", {}
                                )
                    vault_stage.output_tokens = input_tokens
                    vault_stage.duration_ms = (time.time() - t_inject) * 1000
                    if trace:
                        trace.stages.append(vault_stage)

                    # Phase 1.2: Retrieval Watchdog — monitor vault injection quality
                    if RETRIEVAL_WATCHDOG_ENABLED and injected_tokens > 0:
                        try:
                            from tokenpak._internal.regression.retrieval_watchdog import (
                                QueryRetrievalRecord,
                                RetrievalQualityWatchdog,
                            )

                            _rw = RetrievalQualityWatchdog()
                            _rw_chunk_count = len(injected_sources) if injected_sources else 0
                            _rw_record = QueryRetrievalRecord(
                                query_id=model or "unknown",
                                query_text=_extract_user_text(
                                    body
                                    if isinstance(body, bytes)
                                    else body.encode("utf-8")
                                    if isinstance(body, str)
                                    else b""
                                )[:200],
                                chunk_count=_rw_chunk_count,
                                unique_chunk_count=_rw_chunk_count,
                                relevance_scores=[1.0] * _rw_chunk_count,
                                source_ids=injected_sources if injected_sources else [],
                                chunk_ids_ordered=[f"chunk_{i}" for i in range(_rw_chunk_count)],
                            )
                            _rw_alert = _rw.observe(_rw_record)
                            if _rw_alert:
                                SESSION["retrieval_watchdog_alert"] = str(_rw_alert)
                        except Exception as _rw_err:
                            SESSION["retrieval_watchdog_error"] = str(_rw_err)
                            pass  # fail-open

                    # Phase 1.5: CANON dedup (AFTER injection, BEFORE compaction)
                    if CANON_AVAILABLE and injected_tokens > 0:
                        t_canon = time.time()
                        canon_stage = StageTrace(
                            name="canon_dedup",
                            enabled=True,
                            input_tokens=input_tokens,
                        )
                        try:
                            session_id = self.headers.get("X-OpenClaw-Session", model)
                            body, canon_refs, canon_saved = apply_canon_refs(body, session_id)
                            if canon_refs > 0:
                                SESSION["canon_hits"] += canon_refs
                                SESSION["canon_tokens_saved"] += canon_saved
                                canon_stage.tokens_delta = -canon_saved
                                canon_stage.details["blocks_referenced"] = canon_refs
                                canon_stage.details["tokens_saved"] = canon_saved
                                _, input_tokens = extract_request_tokens(
                                    body, adapter=active_adapter
                                )
                        except Exception as _canon_err:
                            canon_stage.details["error"] = str(_canon_err)
                        canon_stage.output_tokens = input_tokens
                        canon_stage.duration_ms = (time.time() - t_canon) * 1000
                        if trace:
                            trace.stages.append(canon_stage)

                    # Phase 1.8: Salience Router — content-type-aware extraction before compaction
                    if SALIENCE_ROUTER_ENABLED and body:
                        try:
                            from tokenpak.compression.salience.router import (
                                detect_content_type,
                            )
                            from tokenpak.compression.salience.router import (
                                extract as salience_extract,
                            )

                            _req_data = json.loads(body)
                            _salience_applied = 0
                            for _msg in _req_data.get("messages", []):
                                _content = _msg.get("content", "")
                                if isinstance(_content, str) and len(_content) > 500:
                                    _ctype = detect_content_type(_content)
                                    if _ctype.value != "unknown":
                                        _result = salience_extract(_content, content_type=_ctype)
                                        if _result.compressed and len(_result.compressed) < len(
                                            _content
                                        ):
                                            _msg["content"] = _result.compressed
                                            _salience_applied += 1
                            if _salience_applied > 0:
                                body = json.dumps(_req_data, separators=(",", ":"))
                                SESSION["salience_router_applied"] = _salience_applied
                        except Exception as _sr_err:
                            SESSION["salience_router_error"] = str(_sr_err)
                            pass  # fail-open

                    # Phase 1.7: Query Rewriter — optimize messages for compression/clarity
                    if QUERY_REWRITER_ENABLED and body:
                        try:
                            from tokenpak.compression.query_rewriter import QueryRewriter

                            _qr = QueryRewriter()
                            _req_data = json.loads(body)
                            _rewritten = _qr.rewrite_messages(_req_data.get("messages", []))
                            if _rewritten and _rewritten != _req_data.get("messages", []):
                                _req_data["messages"] = _rewritten
                                body = json.dumps(_req_data, separators=(",", ":"))
                                SESSION["query_rewriter_applied"] = len(_rewritten)
                        except Exception as _qr_err:
                            SESSION["query_rewriter_error"] = str(_qr_err)
                            pass  # fail-open

                    # Phase 1.9: Fidelity Tiers — select compression level based on budget/complexity
                    if FIDELITY_TIERS_ENABLED and body:
                        try:
                            from tokenpak.compression.fidelity_tiers import (
                                TierSelector,
                            )

                            _ts = TierSelector()
                            _complexity = min(
                                1.0, (input_tokens or 0) / 10000.0
                            )  # simple heuristic
                            _budget_remaining = max(0.0, 1.0 - _complexity)
                            _selected_tier = _ts.select(_complexity, _budget_remaining)
                            SESSION["fidelity_tier"] = (
                                _selected_tier.name
                                if hasattr(_selected_tier, "name")
                                else str(_selected_tier)
                            )
                        except Exception as _ft_err:
                            SESSION["fidelity_tier_error"] = str(_ft_err)
                            pass  # fail-open

                    # Plugin system — run custom compressors first
                    if _plugin_registry is not None and body:
                        _plugin_context = {
                            "mode": COMPILATION_MODE,
                            "input_tokens": input_tokens,
                            "request_id": SESSION.get("request_id", ""),
                        }
                        for _plugin in _plugin_registry.get_plugins():
                            try:
                                _req_data = json.loads(body)
                                for _msg in _req_data.get("messages", []):
                                    _content = _msg.get("content", "")
                                    if isinstance(_content, str):
                                        _plugin_result = _plugin.compress(_content, _plugin_context)
                                        _msg["content"] = _plugin_result["text"]
                                body = json.dumps(_req_data, separators=(",", ":"))
                            except Exception as _plugin_run_err:
                                import logging as _logging

                                _logging.getLogger(__name__).warning(
                                    "Plugin '%s' raised an error: %s — skipping",
                                    getattr(_plugin, "name", repr(_plugin)),
                                    _plugin_run_err,
                                )

                    # Compression budget check — if vault injection took too long, skip compaction
                    if _compression_budget_exceeded():
                        print(
                            f"  ⏱️  Compression budget exceeded ({MAX_COMPRESSION_TIME_MS}ms) after vault injection — "
                            f"skipping compaction, forwarding as-is"
                        )
                        SESSION["compression_timeouts"] += 1
                        raise _CompressionTimeout()

                    # Phase 2: Compaction (AFTER injection)
                    t_compact = time.time()
                    compaction_stage = StageTrace(
                        name="compaction",
                        enabled=ENABLE_COMPACTION,
                        input_tokens=input_tokens,
                    )
                    if ENABLE_COMPACTION:
                        body, sent_input_tokens, original_tokens, protected_tokens = (
                            compact_request_body(
                                body,
                                adapter=active_adapter,
                            )
                        )
                        if original_tokens > 0:
                            input_tokens = original_tokens
                        compaction_stage.output_tokens = sent_input_tokens
                        compaction_stage.tokens_delta = (
                            -(original_tokens - sent_input_tokens) if original_tokens else 0
                        )
                        compaction_stage.details["mode"] = COMPILATION_MODE
                        compaction_stage.details["protected_tokens"] = protected_tokens
                        compaction_stage.details["tokens_removed"] = (
                            max(0, original_tokens - sent_input_tokens) if original_tokens else 0
                        )
                    else:
                        sent_input_tokens = input_tokens
                        compaction_stage.output_tokens = sent_input_tokens
                    compaction_stage.duration_ms = (time.time() - t_compact) * 1000
                    if trace:
                        trace.stages.append(compaction_stage)
                    # Phase 2.1: Compression Dictionary — apply learned compression terms post-standard-compaction
                    if COMPRESSION_DICT_ENABLED and body:
                        try:
                            from tokenpak.compression.dictionary import CompressionDictionary

                            _dict = CompressionDictionary()
                            _req_data = json.loads(body)
                            if "messages" in _req_data:
                                _dict_result = _dict.apply(_req_data["messages"])
                                _req_data["messages"] = _dict_result.messages
                                body = json.dumps(_req_data, separators=(",", ":"))
                                SESSION["compression_dict_applied"] = True
                        except Exception as _cd_err:
                            SESSION["compression_dict_error"] = str(_cd_err)
                            pass  # fail-open

                    # Workflow: vault_inject done → compress done → begin forward
                    if _wf_id:
                        try:
                            from tokenpak.agentic.proxy_workflow import advance_step

                            advance_step(_wf_id, "vault_inject", "compress")
                            advance_step(_wf_id, "compress", "forward")
                        except Exception:
                            pass
                else:
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
                    from tokenpak.validation.request_validator import (
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

        try:
            pool = self.server.proxy_server._connection_pool  # type: ignore[attr-defined]
            _cb_success = False  # track whether request succeeded for circuit breaker

                _fb = _j2.loads(body) if isinstance(body, (bytes, str)) else body
                _all_cc = 0
                for _sk in ["system", "tools", "messages"]:
                    items = _fb.get(_sk, [])
                    if isinstance(items, list):
                        for _it in items:
                            if isinstance(_it, dict):
                                if "cache_control" in _it:
                                    _all_cc += 1
                                for _cv in (
                                    _it.get("content", [])
                                    if isinstance(_it.get("content"), list)
                                    else []
                                ):
                                    if isinstance(_cv, dict) and "cache_control" in _cv:
                                        _all_cc += 1
                print(
                    f"  🎯 FINAL body has {_all_cc} cache_control blocks (system+tools+messages)",
                    flush=True,
                )
                if _all_cc > 4:
                    with open("/tmp/debug_body.json", "w") as _df:
                        _j2.dump(_fb, _df, indent=2)
                    print("  ❌ DUMPED to /tmp/debug_body.json", flush=True)
            except Exception as _de:
                print(f"  debug error: {_de}", flush=True)

            # --- Early SSE keepalive ---
            # Send HTTP 200 + SSE headers BEFORE the upstream call when streaming.
            # This prevents OpenClaw from timing out during compression + upstream TTFB.
            # SSE comments (lines starting with ":") are ignored by spec-compliant parsers.
            _early_sse_sent = False
            # Keepalive disabled — causes framing issues with OpenClaw SDK

            _t0_conn = time.monotonic()
            resp = _POOL_MANAGER.request(
                method,
                target_url,
                headers=fwd_headers,
                body=body,
                timeout=urllib3.Timeout(connect=10.0, read=UPSTREAM_TIMEOUT),
                preload_content=False,
            )
            _conn_ms = int((time.monotonic() - _t0_conn) * 1000)
            print(f"  🔌 upstream connect+send: {_conn_ms}ms (pool reuse enabled)", flush=True)
            status = resp.status

            # Key pool failover: retry with next key on 401/429 (only when we injected)
            if (
                status in (401, 429)
                and _current_key_idx >= 0
                and not _client_has_auth
                and len(_ANTHROPIC_KEY_POOL) > 1
            ):
                _cooldown_dur = _KEY_COOLDOWN_401 if status == 401 else _KEY_COOLDOWN_429
                _cool_down_key(_current_key_idx, _cooldown_dur, f"HTTP {status}")
                _retry_key, _retry_idx = _get_next_key(exclude_idx=_current_key_idx)
                if _retry_key:
                    print(
                        f"[key-pool] Key #{_current_key_idx} returned {status}, "
                        f"retrying with key #{_retry_idx}",
                        flush=True,
                    )
                    fwd_headers["x-api-key"] = _retry_key
                    _current_key_idx = _retry_idx
                    try:
                        resp.drain_conn()
                    except Exception:
                        pass
                    _t0_conn = time.monotonic()
                    resp = _POOL_MANAGER.request(
                        method,
                        target_url,
                        headers=fwd_headers,
                        body=body,
                        timeout=urllib3.Timeout(connect=10.0, read=UPSTREAM_TIMEOUT),
                        preload_content=False,
                    )
                    status = resp.status
                    print(
                        f"[key-pool] Retry key #{_retry_idx} → HTTP {status} "
                        f"({int((time.monotonic() - _t0_conn) * 1000)}ms)",
                        flush=True,
                    )

            # Fix #5: Record success/failure for circuit breaker
            if status >= 500:
                _circuit_record_failure(_cb_provider)
            else:
                _circuit_record_success(_cb_provider)
            content_type = resp.getheader("Content-Type", "")
            is_sse = "text/event-stream" in content_type

            # If upstream errored but we already sent 200+SSE headers, emit SSE error event
            if _early_sse_sent and status >= 400:
                try:
                    _err_body = resp.read()
                    _err_event = json.dumps({
                        "type": "error",
                        "error": {"type": "upstream_error", "message": f"HTTP {status}: {_err_body[:500].decode('utf-8', errors='replace')}"}
                    })
                    self.wfile.write(f"event: error\ndata: {_err_event}\n\n".encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return

            # Fix #4: Normalize upstream error responses to unified JSON shape
            # Anthropic returns {"type":"error","error":{...},"request_id":"..."}
            # We normalize all 4xx/5xx to {"error":{"type":...,"message":...}}
            _resp_content_type = resp.getheader("Content-Type", "")
            if status >= 400 and "application/json" in _resp_content_type and not is_sse:
                try:
                    _err_raw = resp.read()
                    _err_data = json.loads(_err_raw)
                    # Anthropic shape: {"type":"error","error":{"type":...,"message":...}}
                    if (
                        "type" in _err_data
                        and _err_data.get("type") == "error"
                        and "error" in _err_data
                    ):
                        _inner = _err_data["error"]
                        _normalized = {
                            "error": {
                                "type": _inner.get("type", "upstream_error"),
                                "message": _inner.get("message", ""),
                                "request_id": _err_data.get("request_id", ""),
                            }
                        }
                    # OpenAI shape: {"error":{"message":...,"type":...,"code":...}}
                    elif "error" in _err_data and isinstance(_err_data["error"], dict):
                        _normalized = _err_data  # already correct shape
                    else:
                        _normalized = {
                            "error": {"type": "upstream_error", "message": str(_err_data)}
                        }
                    # Tier 2A: Error Normalizer — further standardize error message text
                    if ERROR_NORMALIZER_ENABLED:
                        try:
                            from tokenpak.agentic.error_normalizer import ErrorNormalizer

                            _en = ErrorNormalizer()
                            _err_msg = _normalized.get("error", {}).get("message", "")
                            if _err_msg:
                                _normalized["error"]["message"] = _en.normalize(_err_msg)
                                SESSION["error_normalizer_applied"] = True
                        except Exception:
                            pass  # fail-open
                    # Tier 2C: Failure Memory — record error signature for future avoidance
                    if FAILURE_MEMORY_ENABLED:
                        try:
                            from tokenpak._internal.agentic.failure_memory import (
                                FailureMemoryDB,
                                FailureSignature,
                            )

                            _fm = FailureMemoryDB()
                            _fm_msg = _normalized.get("error", {}).get("message", "")
                            _fm_type = _normalized.get("error", {}).get("type", "unknown")
                            if _fm_msg and not _fm.match(_fm_msg):
                                _fm.add(
                                    FailureSignature(
                                        error_type=_fm_type, pattern=_fm_msg[:200], model=model
                                    )
                                )
                                SESSION["failure_memory_recorded"] = True
                        except Exception:
                            pass  # fail-open
                    # Actionable error enrichment — add hint/retry_after for key error paths
                    _retry_after_hdr = (
                        resp.getheader("Retry-After", None) if hasattr(resp, "getheader") else None
                    )
                    _normalized = _enrich_upstream_error(_normalized, status, _retry_after_hdr)
                    _err_body = json.dumps(_normalized, indent=2).encode()
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", len(_err_body))
                    # Forward Retry-After header for 429 responses
                    if status == 429 and _retry_after_hdr:
                        self.send_header("Retry-After", _retry_after_hdr)
                    self.end_headers()
                    self.wfile.write(_err_body)
                    return
                except Exception:
                    resp = type(
                        "FakeResp",
                        (),
                        {
                            "read": lambda self: _err_raw,
                            "getheaders": lambda self: [],
                            "getheader": lambda self, k, d="": d,
                        },
                    )()

            # HTTP 100 Continue keepalive — send BEFORE response headers if enabled + SSE
            # This signals liveness during compression/upstream delay to prevent client timeouts
            if HTTP100_KEEPALIVE_ENABLED and is_sse and status == 200 and not _early_sse_sent:
                try:
                    self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Client disconnected — fail gracefully
            
            # Skip header sending if we already sent early SSE headers
            if not _early_sse_sent:
                self.send_response(status)
                # urllib3 HTTPResponse uses .headers (HTTPHeaderDict) instead of .getheaders()
                _resp_headers = resp.headers.items() if hasattr(resp, "headers") else resp.getheaders()
                for h_key, h_val in _resp_headers:
                    h_lower = h_key.lower()
                    if h_lower in ("connection", "keep-alive", "transfer-encoding"):
                        continue
                    if h_lower == "content-length":
                        continue
                    self.send_header(h_key, h_val)
                self.end_headers()

            if is_sse:
                output_tokens = 0
                sse_buffer = b""
                with pool.stream(method, target_url, content=body, headers=fwd_headers) as resp:
                    self.send_response(resp.status_code)
                    has_content_type = False
                    has_cache_control = False
                    for h_key, h_val in resp.headers.items():
                        h_lower = h_key.lower()
                        if h_lower in ("connection", "keep-alive", "transfer-encoding", "content-length"):
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

                if should_log and is_messages and sse_buffer:
                    sse_usage = extract_sse_tokens(sse_buffer)
                    output_tokens = sse_usage.get("output_tokens", 0)
                    cache_read_tokens = sse_usage.get("cache_read_input_tokens", 0)
                    cache_creation_tokens = sse_usage.get("cache_creation_input_tokens", 0)
            else:
                # ── Non-streaming path ────────────────────────────────────
                resp = pool.request(method, target_url, content=body, headers=fwd_headers)

                # Phase 2.2: Session Capsules — compress and store session context
                if SESSION_CAPSULES_ENABLED and body:
                    try:
                        from tokenpak._internal.memory.session_capsules import (
                            build_session_capsule,
                            serialize_capsule,
                        )

                        _session_id = self.headers.get("X-OpenClaw-Session", model)
                        _capsule_text = body.decode("utf-8") if isinstance(body, bytes) else body
                        _capsule = build_session_capsule(_capsule_text, source_path=_session_id)
                        _capsule_str = serialize_capsule(_capsule)
                        SESSION["session_capsule_built"] = True
                        SESSION["session_capsule_size"] = len(_capsule_str)
                    except Exception as _sc_err:
                        SESSION["session_capsule_error"] = str(_sc_err)
                        pass  # fail-open

                resp_body = resp.content
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

            # Post-request: Stability Scorer — track response consistency over time
            if STABILITY_SCORER_ENABLED:
                try:
                    from tokenpak._internal.regression.stability_scorer import (
                        RunRecord,
                        StabilityScorer,
                    )

                    _ss = StabilityScorer()
                    _workflow_id = self.headers.get("X-OpenClaw-Session", model)
                    _resp_text = ""
                    try:
                        _resp_text = (
                            (
                                resp_body[:500].decode("utf-8")
                                if isinstance(resp_body, bytes)
                                else str(resp_body)[:500]
                            )
                            if "resp_body" in locals()
                            else ""
                        )
                    except Exception:
                        pass
                    _record = RunRecord(
                        timestamp=str(int(time.time())),
                        passed=status == 200,
                        retried=False,
                        token_count=(input_tokens or 0) + (output_tokens or 0),
                        output_text=_resp_text,
                        validation_passed=status == 200,
                    )
                    _ss.record_run(_workflow_id, _record)
                    _score = _ss.score_workflow(_workflow_id)
                    SESSION["stability_score"] = (
                        _score.score if hasattr(_score, "score") else str(_score)
                    )
                except Exception as _ss_err:
                    SESSION["stability_scorer_error"] = str(_ss_err)
                    pass  # fail-open

            # Post-request: Log completed request via Request Logger
            if REQUEST_LOGGER_ENABLED and _request_log_id:
                try:
                    from tokenpak.monitoring.request_logger import RequestLogger

                    _req_logger = RequestLogger.get_instance()
                    _record = _req_logger.build_record(
                        request_id=_request_log_id,
                        method="POST",
                        endpoint=target_url,
                        request_body_size=len(body) if body else 0,
                        response_status=status,
                        compression_ratio=round(sent_input_tokens / input_tokens, 3)
                        if input_tokens
                        else None,
                        latency_ms=latency_ms,
                        model=model,
                        provider=_cb_provider if "_cb_provider" in dir() else "",
                    )
                    _req_logger.log(_record)
                    SESSION["request_logger_logged"] = True
                except Exception as _rl_post_err:
                    SESSION["request_logger_post_error"] = str(_rl_post_err)
                    pass  # fail-open

            if should_log and is_messages and input_tokens > 0:
                cost = estimate_cost(model, sent_input_tokens, output_tokens,
                                     cache_read_tokens, cache_creation_tokens)
                cost_without = estimate_cost(model, input_tokens, output_tokens,
                                             cache_read_tokens, cache_creation_tokens)
                saved = max(0, input_tokens - sent_input_tokens)
                cost_saved = max(0.0, cost_without - cost)

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
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
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

                # Workflow tracking: mark forward done → log_metrics → complete
                if _wf_id:
                    try:
                        from tokenpak.agentic.proxy_workflow import (
                            advance_step,
                            complete_workflow,
                        )

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
            import traceback as _tb

            _tb.print_exc(file=__import__("sys").stderr)
            print(f"  ❌ Proxy error: {type(e).__name__}: {e} | {latency_ms}ms")
            # Workflow tracking: mark the in-progress step as failed (not whole workflow)
            if _wf_id:
                try:
                    from tokenpak.agentic.proxy_workflow import fail_step as _wf_fail

                    _wf_fail(_wf_id, "forward", error=f"{type(e).__name__}: {e}")
                except Exception:
                    pass
            try:
                log_request(
                    request_id=_req_id,
                    client_ip=self.client_address[0] if self.client_address else "",
                    method=method,
                    endpoint=parsed.path,
                    request_body_size=content_length,
                    response_status=502,
                    latency_ms=latency_ms,
                    model=model,
                    extra={"error": exc_type, "error_message": exc_msg[:200]},
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

        Response shape:
          {
            estimated_cost_usd: float,
            ttfb_estimate_ms: int,
            cache_hit_likelihood: float,
            breakdown: {
              input_tokens: int,
              output_estimate: int,
              cache_hits_estimate: int,
              cache_creates_estimate: int,
            }
          }
        """
        from tokenpak.proxy.token_cache import count_tokens as _count_tokens

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

        model = payload.get("model", "claude-sonnet-4-5") or "claude-sonnet-4-5"
        if not isinstance(model, str):
            model = "claude-sonnet-4-5"

        # ── 1. Count input tokens (mirrors _handle_count_tokens logic) ──────────
        total_input = 0

        system = payload.get("system", "")
        if isinstance(system, str):
            total_input += _count_tokens(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        total_input += _count_tokens(text)
                elif isinstance(block, str):
                    total_input += _count_tokens(block)

        for msg in payload["messages"]:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                total_input += _count_tokens(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "")
                        if isinstance(text, str):
                            total_input += _count_tokens(text)
                    elif isinstance(block, str):
                        total_input += _count_tokens(block)

        for tool in payload.get("tools", []):
            if not isinstance(tool, dict):
                continue
            total_input += _count_tokens(tool.get("name", ""))
            total_input += _count_tokens(tool.get("description", ""))
            schema = tool.get("input_schema", {})
            if isinstance(schema, dict):
                total_input += _count_tokens(json.dumps(schema, separators=(",", ":")))

        # ── 2. Estimate cache creates from cache_control hints ─────────────────
        cache_creates_estimate = 0
        cache_hits_estimate = 0
        for msg in payload["messages"]:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("cache_control"):
                        block_text = block.get("text", "")
                        if isinstance(block_text, str):
                            cache_creates_estimate += _count_tokens(block_text)
        if isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("cache_control"):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        cache_creates_estimate += _count_tokens(text)

        # ── 3. Output estimate — default 500 unless max_tokens is small ────────
        max_tokens = payload.get("max_tokens")
        if isinstance(max_tokens, int) and 0 < max_tokens < 500:
            output_estimate = max_tokens
        else:
            output_estimate = 500

        # ── 4. Apply pricing config ────────────────────────────────────────────
        estimated_cost_usd = estimate_cost(
            model,
            total_input,
            output_estimate,
            cache_read_tokens=cache_hits_estimate,
            cache_creation_tokens=cache_creates_estimate,
        )

        # ── 5. TTFB estimate from rolling latency average ─────────────────────
        with _forecast_latency_lock:
            lats = list(_forecast_latencies)
        if lats:
            ttfb_estimate_ms = int(sum(lats) / len(lats))
        else:
            ttfb_estimate_ms = 800  # default when no prior requests

        # ── 6. Cache hit likelihood — per-session if available, else 0.5 ───────
        cache_hit_likelihood = 0.5
        session_id = self.headers.get("x-claude-code-session-id", "").strip()
        if session_id:
            try:
                import sqlite3 as _sqlite3
                from tokenpak.proxy.config import MONITOR_DB as _DB
                _conn = _sqlite3.connect(_DB, timeout=2)
                try:
                    _cur = _conn.execute(
                        "SELECT cache_read_tokens FROM requests "
                        "WHERE session_id = ? ORDER BY timestamp DESC LIMIT 20",
                        (session_id,),
                    )
                    rows = _cur.fetchall()
                    if len(rows) >= 3:
                        hits = sum(1 for r in rows if (r[0] or 0) > 0)
                        cache_hit_likelihood = round(hits / len(rows), 4)
                finally:
                    _conn.close()
            except Exception:
                pass  # never break the forecast endpoint
        else:
            try:
                ps = self.server.proxy_server
                _s = ps.session
                _cr = _s.get("cache_read_tokens", 0)
                _total_reqs = _s.get("requests", 0)
                if _total_reqs >= 5:
                    cache_hit_likelihood = round(min(1.0, _cr / max(1, _total_reqs * 500)), 4)
            except Exception:
                pass

        resp_body = json.dumps({
            "estimated_cost_usd": round(estimated_cost_usd, 8),
            "ttfb_estimate_ms": ttfb_estimate_ms,
            "cache_hit_likelihood": cache_hit_likelihood,
            "breakdown": {
                "input_tokens": total_input,
                "output_estimate": output_estimate,
                "cache_hits_estimate": cache_hits_estimate,
                "cache_creates_estimate": cache_creates_estimate,
            },
        }, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(resp_body)

    def _send_json(self, data: dict) -> None:
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


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
        self.compression_stats = CompressionStats(start_time=self.session["start_time"])

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
        uptime = round(time.time() - start_time)
        last_restart = datetime.fromtimestamp(start_time, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Provider health from circuit breakers; unknown when no circuit exists yet.
        _known_providers = ("anthropic", "openai", "google")
        cb_registry = get_circuit_breaker_registry()
        cb_statuses = cb_registry.all_statuses()
        providers: dict = {}
        active_alerts = 0
        for name in _known_providers:
            if name in cb_statuses:
                state = cb_statuses[name].get("state", "unknown")
                if state in ("open", "half_open"):
                    providers[name] = "error"
                    active_alerts += 1
                else:
                    providers[name] = "ok"
            else:
                providers[name] = "unknown"
        return {
            "uptime_seconds": uptime,
            "version": _tokenpak_version,
            "last_restart": last_restart,
            "active_alerts": active_alerts,
            "providers": providers,
        }

    def stats(self) -> dict:
        return {
            "session": self.session,
            "compilation_mode": self.compilation_mode,
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
    host: str = "0.0.0.0",
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
