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
    TOKENPAK_DB            (default .ocp/monitor.db)
"""

from __future__ import annotations

import gzip
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .router import ProviderRouter, estimate_cost, INTERCEPT_HOSTS
from .streaming import extract_sse_tokens
from .passthrough import forward_headers, PassthroughConfig
from tokenpak.agent.dashboard.export_api import ExportAPI
from tokenpak.agent.dashboard.session_filter import (
    SessionFilter,
    FilterParams,
    get_distinct_models,
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
    }


# ---------------------------------------------------------------------------
# Threaded HTTP server
# ---------------------------------------------------------------------------

class _ThreadedHTTPServer(HTTPServer):
    """HTTP server that dispatches each request to a daemon thread."""

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

        if path == "/health":
            self._send_json(ps.health())
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
        elif self.path.startswith("/v1/"):
            ps = self.server.proxy_server
            route = ps.router.route(self.path, dict(self.headers))
            self._proxy_to(route.full_url, "POST")
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("http"):
            self._proxy_to(self.path, "PUT")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("http"):
            self._proxy_to(self.path, "DELETE")
        else:
            self.send_error(404)

    # ------------------------------------------------------------------
    # Core forwarding
    # ------------------------------------------------------------------

    def _proxy_to(self, target_url: str, method: str) -> None:
        t0 = time.time()
        ps = self.server.proxy_server
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

            if ps.request_hook:
                try:
                    body, sent_input_tokens, input_tokens, protected_tokens = ps.request_hook(
                        body, model, trace
                    )
                except Exception as hook_err:
                    print(f"  ⚠ request_hook error: {hook_err}")
                    sent_input_tokens = input_tokens

        if sent_input_tokens == 0:
            sent_input_tokens = input_tokens

        # Build forwarding headers
        passthrough_cfg = PassthroughConfig()
        fwd_headers = forward_headers(dict(self.headers), passthrough_cfg)
        fwd_headers["Host"] = parsed.netloc
        if body is not None:
            fwd_headers["Content-Length"] = str(len(body))

        try:
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(parsed.netloc, timeout=300, context=ctx)
            else:
                conn = http.client.HTTPConnection(parsed.netloc, timeout=300)

            path = parsed.path
            if parsed.query:
                path += "?" + parsed.query
            conn.request(method, path, body=body, headers=fwd_headers)
            resp = conn.getresponse()

            content_type = resp.getheader("Content-Type", "")
            is_sse = "text/event-stream" in content_type

            self.send_response(resp.status)
            for h_key, h_val in resp.getheaders():
                h_lower = h_key.lower()
                if h_lower in ("connection", "keep-alive", "transfer-encoding", "content-length"):
                    continue
                self.send_header(h_key, h_val)
            self.end_headers()

            output_tokens = 0
            if is_sse:
                sse_buffer = b""
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
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
                resp_body = resp.read()
                self.wfile.write(resp_body)
                self.wfile.flush()
                if should_log and is_messages:
                    body_for_metrics = resp_body
                    if "gzip" in resp.getheader("Content-Encoding", ""):
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

            conn.close()
            latency_ms = int((time.time() - t0) * 1000)

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
                # Track per-request compression ratio for rolling average
                if input_tokens > 0:
                    ratio = round(saved / input_tokens, 4)
                    with ps._compression_lock:
                        ps._compression_ratios.append(ratio)

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

        except Exception as exc:
            with ps._session_lock:
                ps.session["errors"] += 1
            latency_ms = int((time.time() - t0) * 1000)
            print(f"  ✖ Proxy error [{method} {target_url[:60]}]: {type(exc).__name__}: {exc} | {latency_ms}ms")
            try:
                err = json.dumps({"error": {"type": "proxy_error", "message": str(exc)}}).encode()
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
        host: str = "0.0.0.0",
        port: Optional[int] = None,
        compilation_mode: Optional[str] = None,
        request_hook: Optional[Callable] = None,
    ):
        self.host = host
        self.port = port or int(os.environ.get("TOKENPAK_PORT", "8766"))
        self.compilation_mode = compilation_mode or os.environ.get("TOKENPAK_MODE", "hybrid")
        self.request_hook = request_hook

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, blocking: bool = True) -> None:
        """Start the proxy server."""
        server = _ThreadedHTTPServer((self.host, self.port), _ProxyHandler)
        server.proxy_server = self  # inject back-reference
        self._server = server

        if blocking:
            print(f"TokenPak proxy listening on {self.host}:{self.port} [{self.compilation_mode}]")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                self.stop()
        else:
            self._server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            self._server_thread.start()

    def stop(self) -> None:
        """Shut down the proxy server."""
        if self._server:
            self._server.shutdown()
            self._server = None

    def is_running(self) -> bool:
        return self._server is not None

    # ------------------------------------------------------------------
    # Status endpoints (also used by handler GET routes)
    # ------------------------------------------------------------------

    def health(self) -> dict:
        with self._session_lock:
            uptime = round(time.time() - self.session["start_time"])
            requests_total = self.session["requests"]
            requests_errors = self.session["errors"]
        with self._compression_lock:
            ratios = list(self._compression_ratios)
        compression_ratio_avg = round(sum(ratios) / len(ratios), 4) if ratios else 0.0
        return {
            "status": "ok",
            "uptime_seconds": uptime,
            "version": "0.1.0",
            "requests_total": requests_total,
            "requests_errors": requests_errors,
            "compression_ratio_avg": compression_ratio_avg,
            "timestamp": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
) -> ProxyServer:
    """Create and start a ProxyServer. Returns the server instance."""
    server = ProxyServer(
        host=host,
        port=port,
        compilation_mode=compilation_mode,
        request_hook=request_hook,
    )
    server.start(blocking=blocking)
    return server
