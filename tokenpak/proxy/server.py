"""
TokenPak Proxy Server

Core HTTP proxy server / request-handling layer. Wraps Python's built-in
HTTPServer into a multi-threaded ProxyServer with compression pipeline hooks,
session stats, and management endpoints.

Env vars (all optional):
    TOKENPAK_PORT          (default 8766)
    TOKENPAK_MODE          (default hybrid) — strict|hybrid|aggressive
    TOKENPAK_COMPACT       (default 1) — master on/off switch
    TOKENPAK_COMPACT_THRESHOLD_TOKENS (default 4500)
    TOKENPAK_DB            (default .tokenpak/monitor.db)
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
from typing import Any, Callable, Dict, Generator, List, Optional
from urllib.parse import urlparse

from tokenpak import __version__ as _tokenpak_version
from tokenpak.agent.adapters.registry import detect_platform
from tokenpak.agent.config import get_stats_footer_enabled
from tokenpak.agent.dashboard.export_api import ExportAPI
from tokenpak.agent.dashboard.session_filter import (
    FilterParams,
    SessionFilter,
)
from tokenpak.agent.telemetry.collector import RequestStats
from tokenpak.agent.telemetry.footer import render_footer_oneline
from tokenpak.cache.telemetry import CacheMetrics
from tokenpak.cache.telemetry import get_collector as _get_cache_collector
from tokenpak.telemetry.request_logger import log_request
from tokenpak.telemetry.request_logger import new_request_id as _new_request_id

from tokenpak.proxy.circuit_breaker import get_circuit_breaker_registry, provider_from_url
from tokenpak.proxy.connection_pool import ConnectionPool, PoolConfig
from tokenpak.proxy.degradation import DegradationEventType, get_degradation_tracker
from tokenpak.proxy.passthrough import PassthroughConfig, forward_headers, validate_auth
from tokenpak.proxy.router import INTERCEPT_HOSTS, ProviderRouter, estimate_cost
from tokenpak.proxy.startup import format_startup_report, run_startup_checks
from tokenpak.proxy.stats import CompressionStats
from tokenpak.proxy.streaming import extract_sse_tokens

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
        # Origin-split cache accounting (attribution contract 2026-04-17).
        # Only 'proxy' origin counts toward tokenpak savings; 'client' and
        # 'unknown' are observability-only. Two counters per origin so
        # status can show hit rate AND total cached tokens per origin.
        "cache_hits_by_origin": {"proxy": 0, "client": 0, "unknown": 0},
        "cache_reads_by_origin": {"proxy": 0, "client": 0, "unknown": 0},
        "cache_requests_by_origin": {"proxy": 0, "client": 0, "unknown": 0},
    }


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
            from urllib.parse import parse_qs
            from urllib.parse import urlparse as _urlparse

            parsed_path = _urlparse(path)
            qs = parse_qs(parsed_path.query)
            deep = qs.get("deep", ["false"])[0].lower() in ("true", "1", "yes")
            self._send_json(ps.health(deep=deep))
            return

        # Reject new proxied requests while shutting down
        if ps.shutdown.is_shutting_down and path.startswith("http"):
            self._send_503_shutdown()
            return
        if path == "/metrics":
            from tokenpak.telemetry.metrics import ProxyMetricsCollector

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
            import asyncio

            from tokenpak.dashboard import serve_dashboard_file

            # Extract dashboard path
            dashboard_path = path[10:]  # Remove '/dashboard' prefix
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
            self._send_json(
                {
                    "enabled": registry.enabled,
                    "circuit_breakers": registry.all_statuses(),
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
            from tokenpak.orchestration.goals import GoalManager

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

        # Run compression pipeline hook if registered
        if should_log and is_messages and body:
            try:
                route = ps.router.route(target_url, dict(self.headers), body)
                model = route.model
            except Exception:
                pass
            input_tokens = _estimate_tokens_from_body(body)

            try:
                data = json.loads(body)
                is_streaming = data.get("stream", False)
            except Exception:
                pass

            # Route classification + policy resolution (1.3.0-α).
            # Single call, single source of truth. Every downstream
            # policy-driven decision reads `_policy.<flag>` instead of
            # re-inspecting headers or body bytes itself.
            try:
                from tokenpak.services.request import Request as _SvcRequest
                from tokenpak.services.request_pipeline.classify_stage import (
                    ClassifyStage as _ClassifyStage,
                )
                from tokenpak.services.request_pipeline.stages import (
                    PipelineContext as _PipelineContext,
                )

                _svc_req = _SvcRequest(
                    body=body or b"",
                    headers=dict(self.headers),
                    metadata={"target_url": target_url},
                )
                _ctx = _PipelineContext(request=_svc_req)
                _ClassifyStage().apply_request(_ctx)
                _route_class = _ctx.route_class
                _policy = _ctx.policy
            except Exception:
                # Classifier never raises, but defensively fall back to
                # the generic (conservative) policy if something goes
                # wrong during import.
                from tokenpak.core.routing.policy import DEFAULT_POLICY as _policy
                from tokenpak.core.routing.route_class import RouteClass as _RC
                _route_class = _RC.GENERIC

            # Attribution: derive origin from Policy.cache_ownership.
            # "client"  → upstream caller owns cache_control (byte_preserve
            #             routes like claude-code-*). Hits credit the
            #             platform, never tokenpak.
            # "proxy"   → tokenpak owns cache_control. If the request_hook
            #             below actually modifies the body, hits credit
            #             tokenpak.
            # "none"    → no cache_control involvement; hits (if any) are
            #             unattributed.
            if _policy.cache_ownership == "client":
                _cache_origin = "client"
            elif _policy.cache_ownership == "proxy":
                _cache_origin = "unknown"  # promoted below if hook mutates
            else:
                _cache_origin = "unknown"
            _body_before_hook = body

            if ps.request_hook:
                try:
                    body, sent_input_tokens, input_tokens, protected_tokens = ps.request_hook(
                        body, model, trace
                    )
                    # Promote to "proxy" origin only when Policy says
                    # tokenpak may own the cache AND the hook actually
                    # added blocks. Never over-claim on a client-owned
                    # route.
                    if (
                        _policy.cache_ownership == "proxy"
                        and body != _body_before_hook
                        and body
                        and (b'"cache_control"' in body)
                    ):
                        _cache_origin = "proxy"
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
        if should_log and is_messages:
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

        # Validate credentials for intercepted provider requests
        # Client-supplied key takes precedence over any environment-level key.
        if should_log and is_messages:
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
        fwd_headers = forward_headers(dict(self.headers), passthrough_cfg)
        fwd_headers["Host"] = parsed.netloc
        if body is not None:
            fwd_headers["Content-Length"] = str(len(body))

        try:
            pool = self.server.proxy_server._connection_pool  # type: ignore[attr-defined]
            _cb_success = False  # track whether request succeeded for circuit breaker

            output_tokens = 0
            if is_streaming:
                # ── Streaming (SSE) path ──────────────────────────────────
                # Use pool.stream() so the connection is kept alive after SSE ends
                sse_buffer = b""
                with pool.stream(method, target_url, content=body, headers=fwd_headers) as resp:
                    self.send_response(resp.status_code)
                    # SC-02: accumulate headers we actually send so the
                    # conformance observer can validate the outbound set.
                    # Parallel to send_header calls — no behavior change.
                    _captured_headers: dict[str, str] = {}
                    has_content_type = False
                    has_cache_control = False
                    for h_key, h_val in resp.headers.items():
                        h_lower = h_key.lower()
                        # `content-encoding` is stripped because httpx's
                        # iter_bytes() yields decoded (decompressed) bytes;
                        # keeping upstream's gzip header would make the
                        # client attempt to decompress plaintext bytes.
                        if h_lower in (
                            "connection",
                            "keep-alive",
                            "transfer-encoding",
                            "content-length",
                            "content-encoding",
                        ):
                            continue
                        if h_lower == "content-type":
                            has_content_type = True
                        if h_lower == "cache-control":
                            has_cache_control = True
                        self.send_header(h_key, h_val)
                        _captured_headers[h_key] = h_val
                    # SSE-required headers: enforce even if upstream omits them
                    if not has_content_type:
                        self.send_header("Content-Type", "text/event-stream")
                        _captured_headers["Content-Type"] = "text/event-stream"
                    if not has_cache_control:
                        self.send_header("Cache-Control", "no-cache")
                        _captured_headers["Cache-Control"] = "no-cache"
                    # Always disable nginx buffering for streaming
                    self.send_header("X-Accel-Buffering", "no")
                    _captured_headers["X-Accel-Buffering"] = "no"
                    # Propagate request ID to client for correlation
                    self.send_header("X-Request-ID", _req_id)
                    _captured_headers["X-Request-ID"] = _req_id
                    # SC-02: notify observer before closing headers.
                    try:
                        from tokenpak.services.diagnostics import (
                            conformance as _conformance,
                        )
                        _conformance.notify_response_headers(
                            _captured_headers, "response"
                        )
                    except Exception:
                        pass
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

                self.send_response(resp.status_code)
                # SC-02: accumulate headers we actually send so the
                # conformance observer can validate the outbound set.
                # Parallel to send_header calls — no behavior change.
                _captured_headers: dict[str, str] = {}
                # httpx auto-decompresses the response body when accessed via
                # .content. Upstream's `Content-Encoding: gzip` header is now
                # a lie for the bytes we forward — clients that trust it will
                # hit a ZlibError trying to "decompress" the already-plaintext
                # body. Strip content-encoding along with the hop-by-hop
                # headers (content-length is already stripped because we might
                # resize the body on future edits).
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
                    self.send_header(h_key, h_val)
                    _captured_headers[h_key] = h_val
                # Debug header: stable prefix hash for cache determinism verification.
                # Emitted for all messages requests (not just intercepted hosts)
                # so integration tests and local stubs can verify determinism.
                if is_messages:
                    _ph = _compute_stable_prefix_hash(body)
                    if _ph:
                        self.send_header("X-Tokenpak-Cache-Prefix-Hash", _ph)
                        _captured_headers["X-Tokenpak-Cache-Prefix-Hash"] = _ph
                # Propagate request ID to client for correlation
                self.send_header("X-Request-ID", _req_id)
                _captured_headers["X-Request-ID"] = _req_id
                # SC-02: notify observer before closing headers.
                try:
                    from tokenpak.services.diagnostics import (
                        conformance as _conformance,
                    )
                    _conformance.notify_response_headers(
                        _captured_headers, "response"
                    )
                except Exception:
                    pass
                self.end_headers()

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
            _cb_success = True  # reached here without exception → request succeeded

            # ── Request logging ───────────────────────────────────────────
            # Always count forwarded requests at the start of the logging
            # block, BEFORE any token parsing that might throw on error
            # responses. This keeps `tokenpak status` counters honest
            # regardless of whether we could extract usage info.
            _always_counted = False
            try:
                _resp_status = resp.status_code if "resp" in dir() else 0  # type: ignore
                if should_log and is_messages:
                    with ps._session_lock:
                        ps.session["requests"] += 1
                        if _resp_status >= 400:
                            ps.session["errors"] += 1
                        # Origin-attribution request count fires here too
                        # so 4xx/5xx responses (no usable usage data)
                        # still show up in the correct origin bucket.
                        _o = _cache_origin if _cache_origin in ("proxy", "client", "unknown") else "unknown"
                        ps.session["cache_requests_by_origin"][_o] += 1
                    _always_counted = True
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
                )
            except Exception:
                pass  # logging must never break the proxy

            if should_log and is_messages and input_tokens > 0:
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

                with ps._session_lock:
                    if not _always_counted:
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
                    # Per-origin hit + token accounting (only when we have
                    # a usable response body). Request-count happens
                    # unconditionally below, outside this guard, so error
                    # responses (4xx/5xx with no usage) are still
                    # attributed to the right origin bucket.
                    _o = _cache_origin if _cache_origin in ("proxy", "client", "unknown") else "unknown"
                    if cache_read_tokens > 0:
                        ps.session["cache_hits_by_origin"][_o] += 1
                        ps.session["cache_reads_by_origin"][_o] += cache_read_tokens

                # Persist to monitor.db so `tokenpak status`, `tokenpak cost`,
                # `tokenpak savings`, and the dashboards see this request.
                # Async write queue (<0.1ms enqueue). Fail-open: DB errors
                # never break the request.
                if getattr(ps, "monitor", None) is not None:
                    try:
                        # _cache_origin was set earlier when the body was
                        # classified (before/after request_hook). Do NOT
                        # re-derive from cache_read_tokens — that says
                        # "who got the hit", not "who placed the markers".
                        # Per the attribution contract, only 'proxy'
                        # origin counts toward tokenpak savings.
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
                            # SC-03: plumb the wire request id through so
                            # the telemetry observer row correlates with
                            # the response headers observer event via the
                            # same X-Request-Id seen on the wire.
                            request_id=_req_id,
                        )
                    except Exception:
                        pass

                # Record cache telemetry
                try:
                    _stable_tokens = max(0, input_tokens - (input_tokens - sent_input_tokens))
                    _miss_reason: Optional[str] = None
                    if cache_read_tokens == 0:
                        # Heuristic miss-reason diagnosis (best-effort)
                        try:
                            _body_text = (
                                body.decode("utf-8", errors="ignore")
                                if isinstance(body, (bytes, bytearray))
                                else ""
                            )
                        except Exception:
                            _body_text = ""
                        import re as _re

                        if _re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", _body_text):
                            _miss_reason = "timestamp"
                        elif "request_id" in _body_text.lower() or "uuid" in _body_text.lower():
                            _miss_reason = "uuid"
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
                            output_tokens=output_tokens,
                        )
                    )
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
                    input_tokens=input_tokens,
                    output_tokens=0,
                    ratio=0.0,
                    latency_ms=latency_ms,
                    status="error",
                )
            exc_type = type(exc).__name__
            exc_msg = str(exc)
            # Log the failed request
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
                err = json.dumps(
                    {
                        "error": {
                            "type": "proxy_error",
                            "message": user_detail,
                            "detail": exc_msg,
                            "hint": "Run `tokenpak doctor` for diagnostics or `tokenpak status` for recent errors.",
                        }
                    }
                ).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
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
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("total_tokens", 0)
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

            self.request_hook: Optional[Callable] = get_capsule_request_hook(base_hook=request_hook)
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

        # SQLite request log — writes to ~/.tokenpak/monitor.db (async queue,
        # <0.1ms enqueue). Feeds `tokenpak status`, `cost`, `savings`, and
        # dashboards. Instantiation is fail-open so the proxy keeps
        # serving even if the DB is locked or corrupt.
        try:
            from tokenpak.proxy.monitor import Monitor as _DbMonitor

            _db_path = os.environ.get(
                "TOKENPAK_DB",
                os.path.expanduser("~/.tokenpak/monitor.db"),
            )
            self.monitor = _DbMonitor(_db_path)
        except Exception as _mon_exc:
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "monitor.db writer disabled: %s", _mon_exc
            )
            self.monitor = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, blocking: bool = True) -> None:
        """Start the proxy server."""
        # SC-02: publish tip-proxy capability set at boot. Canonical
        # source is tokenpak.core.contracts.capabilities; no duplication.
        # Observer is ship-safe no-op when none installed.
        try:
            from tokenpak.services.diagnostics import conformance as _conformance
            from tokenpak.core.contracts.capabilities import (
                SELF_CAPABILITIES_PROXY,
            )
            _conformance.notify_capability_published(
                "tip-proxy", SELF_CAPABILITIES_PROXY
            )
        except Exception:
            pass
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
        print(
            f"\nTokenPak: {sig_name} received — starting graceful shutdown "
            f"(drain timeout: {self.shutdown_timeout:.0f}s)...",
            flush=True,
        )
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
            print(
                f"TokenPak: shutdown step 3/5 — telemetry flush error (non-fatal): {exc}",
                flush=True,
            )

        # ── Step 4: Close HTTP connection pool ────────────────────────────
        print("TokenPak: shutdown step 4/5 — closing connection pool...", flush=True)
        try:
            self._connection_pool.close()
            print("TokenPak: shutdown step 4/5 — connection pool closed ✓", flush=True)
        except Exception as exc:
            print(f"TokenPak: shutdown step 4/5 — pool close error (non-fatal): {exc}", flush=True)

        # ── Step 5: Stop HTTP server ───────────────────────────────────────
        print("TokenPak: shutdown step 5/5 — stopping HTTP server...", flush=True)
        srv = self._server
        self._server = None
        try:
            srv.shutdown()
            print("TokenPak: shutdown step 5/5 — HTTP server stopped ✓", flush=True)
        except Exception as exc:
            print(f"TokenPak: shutdown step 5/5 — server stop error (non-fatal): {exc}", flush=True)

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
        cb_any_open = any(s.get("state") in ("open", "half_open") for s in cb_statuses.values())
        result = {
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
                disk_available_gb = round(disk.free / (1024**3), 2)
            except Exception:
                disk_available_gb = None
            result["providers"] = providers
            result["memory"] = {"rss_mb": mem_mb}
            result["disk"] = {"available_gb": disk_available_gb}
        return result

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
                if s["input_tokens"] > 0
                else 0.0
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
