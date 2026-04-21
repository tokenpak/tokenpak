"""
TokenPak Async Proxy Server (Starlette + uvicorn + httpx)

Replaces the synchronous BaseHTTPRequestHandler with a fully async ASGI stack:
- Starlette ASGI app for all HTTP management endpoints and reverse-proxy paths
- httpx.AsyncClient with connection pooling for upstream forwarding
- ConcurrencyLimiter middleware for backpressure (503 when at capacity)
- CONNECT tunnelling via a lightweight asyncio TCP bridge
- Sub-10ms overhead target on the proxy path (excluding upstream latency)

Architecture:
  client → asyncio TCP server (CONNECT) ─┐
             ↓ HTTP                        ├→ upstream LLM provider
           uvicorn → Starlette ASGI app ──┘

All management state (session counters, trace storage, etc.) is shared with
the ProxyServer class via a ``_AsyncProxyState`` singleton so the /health,
/stats, and other endpoints remain accurate.

Env vars (all optional):
    TOKENPAK_PORT              (default 8766)
    TOKENPAK_CONCURRENCY       (default 200) — max concurrent in-flight requests
    TOKENPAK_HTTPX_POOL_SIZE   (default 100) — httpx connection pool size
    TOKENPAK_HTTPX_TIMEOUT     (default 300) — upstream timeout seconds
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROXY_PORT = int(os.environ.get("TOKENPAK_PORT", "8766"))
MAX_CONCURRENCY = int(os.environ.get("TOKENPAK_CONCURRENCY", "200"))
HTTPX_POOL_SIZE = int(os.environ.get("TOKENPAK_HTTPX_POOL_SIZE", "100"))
HTTPX_TIMEOUT = float(os.environ.get("TOKENPAK_HTTPX_TIMEOUT", "300"))
INTERCEPT_HOSTS = {"api.anthropic.com", "api.openai.com"}

# ---------------------------------------------------------------------------
# Module-level shared state (set by ProxyServer before uvicorn starts)
# ---------------------------------------------------------------------------

_proxy_server_ref: Any = None  # ProxyServer instance


def _ps():
    """Return the ProxyServer instance (set before serving)."""
    if _proxy_server_ref is None:
        raise RuntimeError("AsyncProxyApp: ProxyServer not initialised")
    return _proxy_server_ref


# ---------------------------------------------------------------------------
# Shared async httpx client (created in lifespan)
# ---------------------------------------------------------------------------

_async_client: Optional[httpx.AsyncClient] = None


def _client() -> httpx.AsyncClient:
    if _async_client is None:
        raise RuntimeError("AsyncProxyApp: httpx client not ready")
    return _async_client


# ---------------------------------------------------------------------------
# Concurrency limiter middleware — backpressure / 503 when at capacity
# ---------------------------------------------------------------------------


class ConcurrencyLimiterMiddleware(BaseHTTPMiddleware):
    """Return HTTP 503 when MAX_CONCURRENCY in-flight requests are active."""

    def __init__(self, app, max_concurrency: int = MAX_CONCURRENCY):
        super().__init__(app)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max = max_concurrency

    async def dispatch(self, request: Request, call_next):
        # Management endpoints bypass the limit so /health always responds
        if request.url.path in ("/health", "/stats", "/stats/last", "/stats/session"):
            return await call_next(request)

        if not self._semaphore.locked() or self._semaphore._value > 0:  # noqa: SLF001
            async with self._semaphore:
                return await call_next(request)
        else:
            return JSONResponse(
                {
                    "error": {
                        "type": "overloaded",
                        "message": (
                            f"TokenPak proxy is at capacity ({self._max} concurrent requests). "
                            "Please retry in a moment."
                        ),
                    }
                },
                status_code=503,
                headers={"Retry-After": "1"},
            )


# ---------------------------------------------------------------------------
# Helper: should we intercept and log this request?
# ---------------------------------------------------------------------------


def _should_intercept(url: str) -> bool:
    return any(h in url for h in INTERCEPT_HOSTS)


def _is_messages_endpoint(url: str) -> bool:
    return "/messages" in url or "/chat/completions" in url


# ---------------------------------------------------------------------------
# Helper: estimate tokens from body
# ---------------------------------------------------------------------------


def _estimate_tokens(body: bytes) -> int:
    try:
        data = json.loads(body)
        total = 0
        for msg in data.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += len(part["text"]) // 4
        sys_content = data.get("system", "")
        if isinstance(sys_content, str):
            total += len(sys_content) // 4
        elif isinstance(sys_content, list):
            for part in sys_content:
                if isinstance(part, dict) and "text" in part:
                    total += len(part["text"]) // 4
        return total
    except Exception:
        return len(body) // 4


def _extract_response_tokens(body: bytes) -> int:
    try:
        usage = json.loads(body).get("usage", {})
        return (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("total_tokens", 0)
        )
    except Exception:
        return 0


def _parse_sse_tokens(sse_bytes: bytes) -> Dict[str, int]:
    result = {"output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    try:
        for line in sse_bytes.decode("utf-8", errors="replace").split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except Exception:
                continue
            if event.get("type") == "message_start":
                usage = event.get("message", {}).get("usage", {})
                result["cache_read_input_tokens"] = usage.get("cache_read_input_tokens", 0)
                result["cache_creation_input_tokens"] = usage.get("cache_creation_input_tokens", 0)
            if event.get("type") == "message_delta":
                usage = event.get("usage", {})
                if "output_tokens" in usage:
                    result["output_tokens"] = usage["output_tokens"]
            # OpenAI-style
            if "usage" in event and "completion_tokens" in event.get("usage", {}):
                result["output_tokens"] = event["usage"]["completion_tokens"]
    except Exception:
        pass
    return result


def _build_forward_headers(request: Request, target_url: str) -> Dict[str, str]:
    """Build headers to forward to the upstream provider."""
    from urllib.parse import urlparse

    parsed = urlparse(target_url)
    skip = {
        "host",
        "proxy-connection",
        "proxy-authorization",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "accept-encoding",
    }
    headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    headers["host"] = parsed.netloc
    return headers


# ---------------------------------------------------------------------------
# Pipeline runner (runs sync pipeline in thread pool to avoid blocking)
# ---------------------------------------------------------------------------


def _run_pipeline_sync(ps, body: bytes, model: str, trace) -> tuple:
    """
    Run the synchronous compression/injection pipeline in a thread.
    Returns (new_body, sent_tokens, raw_tokens, protected_tokens).
    """
    if ps.request_hook is None:
        tokens = _estimate_tokens(body)
        return body, tokens, tokens, 0
    try:
        result = ps.request_hook(body, model, trace)
        return result
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "tokenpak async: pipeline failed (%s: %s) — passthrough", type(exc).__name__, exc
        )
        tokens = _estimate_tokens(body)
        return body, tokens, tokens, 0


# ---------------------------------------------------------------------------
# Core forwarding logic (async)
# ---------------------------------------------------------------------------


async def _forward_request(request: Request, target_url: str) -> Response:
    """
    Forward an HTTP request to target_url via httpx.AsyncClient.
    Applies compression pipeline, records telemetry, returns Response.
    """
    ps = _ps()
    t0 = time.monotonic()

    should_log = _should_intercept(target_url)
    is_messages = _is_messages_endpoint(target_url)

    # Read body
    body = await request.body()

    model = "unknown"
    input_tokens = 0
    sent_input_tokens = 0
    protected_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    is_streaming = False

    # Pipeline trace
    trace = None
    if should_log and is_messages:
        # Import here to avoid circular imports
        try:
            from tokenpak.proxy.server import PipelineTrace

            trace = PipelineTrace(
                request_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )
        except Exception:
            pass

    # Route detection
    if should_log and is_messages and body:
        try:
            from tokenpak.proxy.router import ProviderRouter

            _router = ProviderRouter()
            route = _router.route(target_url, dict(request.headers), body)
            model = route.model
        except Exception:
            pass

        input_tokens = _estimate_tokens(body)

        try:
            is_streaming = json.loads(body).get("stream", False)
        except Exception:
            pass

        # Run pipeline in thread pool (sync code, must not block event loop)
        (
            body,
            sent_input_tokens,
            input_tokens,
            protected_tokens,
        ) = await asyncio.get_event_loop().run_in_executor(
            None, _run_pipeline_sync, ps, body, model, trace
        )

    if sent_input_tokens == 0:
        sent_input_tokens = input_tokens

    # Build forward headers
    fwd_headers = _build_forward_headers(request, target_url)
    if body is not None:
        fwd_headers["content-length"] = str(len(body))

    output_tokens = 0
    client = _client()

    try:
        if is_streaming:
            # ── SSE streaming path ────────────────────────────────────────
            sse_buffer = bytearray()
            upstream_req = client.build_request(
                request.method, target_url, content=body, headers=fwd_headers
            )

            async def sse_stream_generator():
                nonlocal output_tokens, cache_read_tokens, cache_creation_tokens
                try:
                    async with client.stream(
                        request.method,
                        target_url,
                        content=body,
                        headers=fwd_headers,
                        timeout=HTTPX_TIMEOUT,
                    ) as resp:
                        async for chunk in resp.aiter_bytes(chunk_size=4096):
                            if chunk:
                                sse_buffer.extend(chunk)
                                yield chunk
                    # Parse SSE usage after stream complete
                    if should_log and is_messages and sse_buffer:
                        usage = _parse_sse_tokens(bytes(sse_buffer))
                        output_tokens = usage["output_tokens"]
                        cache_read_tokens = usage["cache_read_input_tokens"]
                        cache_creation_tokens = usage["cache_creation_input_tokens"]
                except Exception as e:
                    yield b""

            # We need response headers from upstream — do a peek
            async with client.stream(
                request.method, target_url, content=body, headers=fwd_headers, timeout=HTTPX_TIMEOUT
            ) as upstream:
                resp_headers = {
                    k: v
                    for k, v in upstream.headers.items()
                    if k.lower()
                    not in ("content-length", "transfer-encoding", "connection", "keep-alive")
                }

                async def _inner_stream():
                    nonlocal output_tokens, cache_read_tokens, cache_creation_tokens
                    _buf = bytearray()
                    async for chunk in upstream.aiter_bytes(chunk_size=4096):
                        if chunk:
                            _buf.extend(chunk)
                            yield chunk
                    if should_log and is_messages and _buf:
                        usage = _parse_sse_tokens(bytes(_buf))
                        output_tokens = usage["output_tokens"]
                        cache_read_tokens = usage["cache_read_input_tokens"]
                        cache_creation_tokens = usage["cache_creation_input_tokens"]
                    # Record telemetry after stream
                    latency_ms = int((time.monotonic() - t0) * 1000)
                    _record_telemetry(
                        ps,
                        trace,
                        model,
                        input_tokens,
                        sent_input_tokens,
                        output_tokens,
                        protected_tokens,
                        cache_read_tokens,
                        cache_creation_tokens,
                        latency_ms,
                    )

                response = StreamingResponse(
                    _inner_stream(),
                    status_code=upstream.status_code,
                    headers=resp_headers,
                    media_type="text/event-stream",
                )
                response.headers["X-Accel-Buffering"] = "no"
                response.headers["Cache-Control"] = "no-cache"
                response.headers["Access-Control-Allow-Origin"] = "*"
                return response

        else:
            # ── Non-streaming path ────────────────────────────────────────
            resp = await client.request(
                request.method,
                target_url,
                content=body,
                headers=fwd_headers,
                timeout=HTTPX_TIMEOUT,
            )
            resp_body = resp.content

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

            latency_ms = int((time.monotonic() - t0) * 1000)
            _record_telemetry(
                ps,
                trace,
                model,
                input_tokens,
                sent_input_tokens,
                output_tokens,
                protected_tokens,
                cache_read_tokens,
                cache_creation_tokens,
                latency_ms,
            )

            resp_headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower()
                not in ("content-length", "transfer-encoding", "connection", "keep-alive")
            }
            resp_headers["Access-Control-Allow-Origin"] = "*"
            return Response(
                content=resp_body,
                status_code=resp.status_code,
                headers=resp_headers,
            )

    except Exception as exc:
        with ps._session_lock:
            ps.session["errors"] += 1
        exc_type = type(exc).__name__
        return JSONResponse(
            {
                "error": {
                    "type": "proxy_error",
                    "message": str(exc),
                    "detail": f"{exc_type}: {exc}",
                    "hint": "Run `tokenpak doctor` for diagnostics.",
                }
            },
            status_code=502,
        )


def _record_telemetry(
    ps,
    trace,
    model: str,
    input_tokens: int,
    sent_input_tokens: int,
    output_tokens: int,
    protected_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    latency_ms: int,
) -> None:
    """Record telemetry for a completed request. Thread-safe."""
    if input_tokens == 0:
        return
    try:
        from tokenpak.proxy.router import estimate_cost

        cost = estimate_cost(
            model, sent_input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
        )
        cost_without = estimate_cost(
            model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
        )
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

        if input_tokens > 0:
            ratio = round(saved / input_tokens, 4)
            with ps._compression_lock:
                ps._compression_ratios.append(ratio)
            try:
                ps.compression_stats.record_compression(
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    ratio=ratio,
                    latency_ms=latency_ms,
                    status="ok",
                )
            except Exception:
                pass

        if trace is not None:
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
    except Exception:
        pass  # telemetry must never break the proxy


# ---------------------------------------------------------------------------
# Management endpoint handlers
# ---------------------------------------------------------------------------


async def handle_health(request: Request) -> JSONResponse:
    return JSONResponse(_ps().health())


async def handle_stats(request: Request) -> JSONResponse:
    return JSONResponse(_ps().stats())


async def handle_stats_last(request: Request) -> JSONResponse:
    return JSONResponse(_ps().last_request_stats())


async def handle_stats_session(request: Request) -> JSONResponse:
    return JSONResponse(_ps().session_stats())


async def handle_traces(request: Request) -> JSONResponse:
    traces = _ps().trace_storage.get_all()
    return JSONResponse({"traces": [t.to_dict() for t in traces], "count": len(traces)})


async def handle_trace_last(request: Request) -> JSONResponse:
    trace = _ps().trace_storage.get_last()
    if trace:
        return JSONResponse(trace.to_dict())
    return JSONResponse({"error": "no_traces"})


async def handle_trace_by_id(request: Request) -> JSONResponse:
    rid = request.path_params["request_id"]
    trace = _ps().trace_storage.get_by_id(rid)
    if trace:
        return JSONResponse(trace.to_dict())
    return JSONResponse({"error": "not_found", "request_id": rid}, status_code=404)


async def handle_degradation(request: Request) -> JSONResponse:
    from tokenpak.proxy.degradation import get_degradation_tracker

    return JSONResponse(get_degradation_tracker().summary())


async def handle_circuit_breakers(request: Request) -> JSONResponse:
    from tokenpak.proxy.circuit_breaker import get_circuit_breaker_registry

    registry = get_circuit_breaker_registry()
    return JSONResponse(
        {
            "enabled": registry.enabled,
            "circuit_breakers": registry.all_statuses(),
        }
    )


async def handle_sessions(request: Request) -> JSONResponse:
    from tokenpak.agent.dashboard.session_filter import FilterParams

    qs = (
        str(request.query_string, "utf-8")
        if isinstance(request.query_string, bytes)
        else request.query_string
    )  # type: ignore[attr-defined]
    try:
        params = FilterParams.from_query_string(qs)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": "invalid_params", "detail": str(exc)}, status_code=400)
    ps = _ps()
    sf = ps.session_filter
    result = sf.query(params)
    result["models"] = sf.distinct_models()
    return JSONResponse(result)


async def handle_export_csv(request: Request) -> Response:
    from tokenpak.agent.dashboard.export_api import ExportAPI

    ps = _ps()
    raw_body = await request.body()
    traces = [t.to_dict() for t in ps.trace_storage.get_all()]
    stats = ps.session_stats()
    body, status, headers = ExportAPI.handle(raw_body=raw_body, traces=traces, session_stats=stats)
    return Response(content=body, status_code=status, headers=headers)


# ---------------------------------------------------------------------------
# Generic proxy handler (full URL forwarding)
# ---------------------------------------------------------------------------


async def handle_proxy(request: Request) -> Response:
    """Handle requests where the path is a full URL (forward proxy mode)."""
    target_url = str(request.url)
    # For forward proxy, path already contains full URL
    path = request.url.path
    if path.startswith("http://") or path.startswith("https://"):
        target_url = path
        if request.url.query:
            target_url += "?" + request.url.query
    return await _forward_request(request, target_url)


async def handle_v1_proxy(request: Request) -> Response:
    """Handle /v1/* paths — reverse proxy to the appropriate provider."""
    from tokenpak.proxy.router import ProviderRouter

    router = ProviderRouter()
    path = request.url.path
    query = request.url.query
    full_path = path + ("?" + query if query else "")
    try:
        route = router.route(full_path, dict(request.headers))
        target_url = route.full_url
    except Exception:
        # Fallback: route to Anthropic
        target_url = "https://api.anthropic.com" + path
    return await _forward_request(request, target_url)


async def handle_not_found(request: Request, exc=None) -> JSONResponse:
    return JSONResponse({"error": "not_found", "path": request.url.path}, status_code=404)


# ---------------------------------------------------------------------------
# Lifespan — create/destroy the shared httpx.AsyncClient
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    """ASGI lifespan: spin up httpx.AsyncClient on startup, close on shutdown."""
    global _async_client
    limits = httpx.Limits(
        max_connections=HTTPX_POOL_SIZE,
        max_keepalive_connections=HTTPX_POOL_SIZE // 2,
        keepalive_expiry=30.0,
    )
    _async_client = httpx.AsyncClient(
        limits=limits,
        timeout=httpx.Timeout(HTTPX_TIMEOUT, connect=30.0),
        follow_redirects=False,
        verify=True,
    )

    # ---- Background tasks: cooldown auto-clear + OAuth auto-refresh --------
    # Controlled by config keys: auth.auto_clear_cooldowns / auth.oauth_auto_refresh
    _cooldown_clearer = None
    _oauth_refresher = None
    try:
        from tokenpak.agent.config import get_config

        cfg = get_config()
        auth_cfg = cfg.get("auth", {}) if isinstance(cfg.get("auth"), dict) else {}
        cooldown_enabled = auth_cfg.get("auto_clear_cooldowns", True)
        oauth_enabled = auth_cfg.get("oauth_auto_refresh", True)

        if cooldown_enabled:
            from tokenpak.agent.auth.cooldown_manager import BackgroundCooldownClearer

            _cooldown_clearer = BackgroundCooldownClearer(interval=60, enabled=True)
            await _cooldown_clearer.start()

        if oauth_enabled:
            from tokenpak.agent.auth.oauth_manager import BackgroundOAuthRefresher

            _oauth_refresher = BackgroundOAuthRefresher(interval=300, enabled=True)
            await _oauth_refresher.start()

    except Exception as _bg_exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "lifespan: could not start background tasks: %s", _bg_exc
        )

    try:
        yield
    finally:
        # Shut down background tasks cleanly
        if _cooldown_clearer:
            await _cooldown_clearer.stop()
        if _oauth_refresher:
            await _oauth_refresher.stop()
        if _async_client is not None:
            await _async_client.aclose()
        _async_client = None


# ---------------------------------------------------------------------------
# ASGI application factory
# ---------------------------------------------------------------------------


def create_async_app(proxy_server) -> Starlette:
    """
    Create the Starlette ASGI app, wired to the given ProxyServer instance.

    All management state is shared through the proxy_server reference.
    """
    global _proxy_server_ref
    _proxy_server_ref = proxy_server

    routes = [
        # Management endpoints
        Route("/health", handle_health, methods=["GET"]),
        Route("/stats", handle_stats, methods=["GET"]),
        Route("/stats/last", handle_stats_last, methods=["GET"]),
        Route("/stats/session", handle_stats_session, methods=["GET"]),
        Route("/traces", handle_traces, methods=["GET"]),
        Route("/trace/last", handle_trace_last, methods=["GET"]),
        Route("/trace/{request_id}", handle_trace_by_id, methods=["GET"]),
        Route("/degradation", handle_degradation, methods=["GET"]),
        Route("/circuit-breakers", handle_circuit_breakers, methods=["GET"]),
        # Session filter
        Route("/v1/sessions", handle_sessions, methods=["GET"]),
        # Export
        Route("/v1/export/csv", handle_export_csv, methods=["POST"]),
        # Reverse proxy (all /v1/* paths)
        Route(
            "/v1/{path:path}", handle_v1_proxy, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
        ),
    ]

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        exception_handlers={404: handle_not_found},
    )

    # Add backpressure middleware
    app.add_middleware(ConcurrencyLimiterMiddleware, max_concurrency=MAX_CONCURRENCY)

    return app


# ---------------------------------------------------------------------------
# CONNECT tunnel handler (asyncio TCP level)
# ---------------------------------------------------------------------------


async def _handle_connect_tunnel(
    host: str, port: int, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
) -> None:
    """
    Handle an HTTP CONNECT tunnel request by bridging client ↔ remote TCP.
    Called from the custom TCP server when CONNECT is detected.
    """
    try:
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=30
        )
    except Exception as exc:
        client_writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await client_writer.drain()
        client_writer.close()
        return

    # Send 200 Connection Established
    client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_writer.drain()

    # Bridge bidirectionally until one side closes
    async def relay(src_reader: asyncio.StreamReader, dst_writer: asyncio.StreamWriter):
        try:
            while True:
                data = await src_reader.read(65536)
                if not data:
                    break
                dst_writer.write(data)
                await dst_writer.drain()
        except Exception:
            pass
        finally:
            try:
                dst_writer.close()
            except Exception:
                pass

    await asyncio.gather(
        relay(client_reader, remote_writer),
        relay(remote_reader, client_writer),
        return_exceptions=True,
    )


# ---------------------------------------------------------------------------
# Custom TCP server that handles CONNECT + delegates HTTP to uvicorn
# ---------------------------------------------------------------------------


class _AsyncTCPProxy:
    """
    Lightweight asyncio TCP server that sits in front of uvicorn.

    For CONNECT requests: performs the tunnel directly (no ASGI involved).
    For all other HTTP: delegates to uvicorn's port by internal connection.
    This preserves CONNECT support while using Starlette for everything else.
    """

    def __init__(self, host: str, port: int, uvicorn_port: int):
        self.host = host
        self.port = port
        self.uvicorn_port = uvicorn_port
        self._server: Optional[asyncio.AbstractServer] = None

    async def _handle_connection(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ):
        try:
            # Read the first line to detect CONNECT
            first_line = await asyncio.wait_for(client_reader.readline(), timeout=30)
        except Exception:
            client_writer.close()
            return

        parts = first_line.decode("latin-1", errors="replace").strip().split()
        if len(parts) >= 2 and parts[0].upper() == "CONNECT":
            # CONNECT host:port HTTP/1.1
            target = parts[1]
            host, _, port_str = target.partition(":")
            port = int(port_str) if port_str.isdigit() else 443
            # Drain the headers (CONNECT has no body, only headers)
            while True:
                header_line = await client_reader.readline()
                if header_line in (b"\r\n", b"\n", b""):
                    break
            await _handle_connect_tunnel(host, port, client_reader, client_writer)
        else:
            # Non-CONNECT: forward to uvicorn via local TCP
            try:
                uv_reader, uv_writer = await asyncio.open_connection("127.0.0.1", self.uvicorn_port)
                uv_writer.write(first_line)
                await uv_writer.drain()

                async def relay_c_to_uv():
                    try:
                        while True:
                            data = await client_reader.read(65536)
                            if not data:
                                break
                            uv_writer.write(data)
                            await uv_writer.drain()
                    except Exception:
                        pass
                    finally:
                        try:
                            uv_writer.close()
                        except Exception:
                            pass

                async def relay_uv_to_c():
                    try:
                        while True:
                            data = await uv_reader.read(65536)
                            if not data:
                                break
                            client_writer.write(data)
                            await client_writer.drain()
                    except Exception:
                        pass
                    finally:
                        try:
                            client_writer.close()
                        except Exception:
                            pass

                await asyncio.gather(relay_c_to_uv(), relay_uv_to_c(), return_exceptions=True)
            except Exception:
                try:
                    client_writer.close()
                except Exception:
                    pass

    async def start(self):
        self._server = await asyncio.start_server(self._handle_connection, self.host, self.port)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()


# ---------------------------------------------------------------------------
# Async proxy runner (called from ProxyServer.start_async())
# ---------------------------------------------------------------------------


async def run_async_proxy(
    proxy_server,
    host: str = "0.0.0.0",
    port: int = PROXY_PORT,
    shutdown_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Run the async proxy: uvicorn (ASGI) on an internal port + asyncio TCP server
    on the public port (with CONNECT handling).

    Uses uvicorn on an internal port so CONNECT can be handled at TCP level
    without uvicorn's involvement.
    """
    import uvicorn

    # uvicorn listens on an internal port; TCP proxy bridges public port → uvicorn
    uvicorn_port = port + 1000  # e.g. 9766 if public port is 8766
    # Check if internal port is available; if not, use direct mode (no CONNECT support)
    _use_tcp_proxy = True
    import socket as _socket

    try:
        _sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        _sock.bind(("127.0.0.1", uvicorn_port))
        _sock.close()
    except OSError:
        # Internal port not available — run uvicorn directly on public port (no CONNECT)
        _use_tcp_proxy = False
        uvicorn_port = port

    app = create_async_app(proxy_server)

    config = uvicorn.Config(
        app,
        host="127.0.0.1" if _use_tcp_proxy else host,
        port=uvicorn_port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
        http="h11",
        workers=1,  # single worker; concurrency via async
        timeout_keep_alive=75,
        timeout_graceful_shutdown=int(proxy_server.shutdown_timeout),
    )
    server = uvicorn.Server(config)

    # Suppress uvicorn's default signal handlers (ProxyServer manages shutdown)
    server.install_signal_handlers = lambda: None  # type: ignore[attr-defined]

    tasks = [asyncio.create_task(server.serve())]

    # Start TCP proxy on public port if using dual-port mode
    tcp_proxy = None
    if _use_tcp_proxy:
        tcp_proxy = _AsyncTCPProxy(host, port, uvicorn_port)
        await tcp_proxy.start()

    print(
        f"TokenPak async proxy: public={host}:{port} "
        f"internal=127.0.0.1:{uvicorn_port} "
        f"concurrency={MAX_CONCURRENCY} pool={HTTPX_POOL_SIZE}"
    )

    # Wait for shutdown signal
    if shutdown_event:
        await shutdown_event.wait()
        server.should_exit = True
        if tcp_proxy:
            await tcp_proxy.stop()

    await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Thread-based entry point (called from ProxyServer.start())
# ---------------------------------------------------------------------------


def start_async_proxy_in_thread(
    proxy_server,
    host: str = "0.0.0.0",
    port: int = PROXY_PORT,
    shutdown_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """
    Start the async proxy in a daemon thread with its own event loop.
    Returns the thread (already started).

    ``shutdown_event`` is a threading.Event; when set, graceful shutdown begins.
    """
    _loop_ready = threading.Event()
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _async_shutdown = None

    def _run():
        nonlocal _loop, _async_shutdown
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop = loop

        _async_shutdown = asyncio.Event()

        # Signal the outer thread that the loop is ready
        _loop_ready.set()

        # Monitor threading.Event in a background coroutine
        async def _watch_shutdown():
            while shutdown_event and not shutdown_event.is_set():
                await asyncio.sleep(0.5)
            if _async_shutdown:
                _async_shutdown.set()

        async def _main():
            await asyncio.gather(
                run_async_proxy(proxy_server, host, port, _async_shutdown),
                _watch_shutdown(),
                return_exceptions=True,
            )

        loop.run_until_complete(_main())
        loop.close()

    t = threading.Thread(target=_run, daemon=True, name="tokenpak-async-proxy")
    t.start()
    _loop_ready.wait(timeout=5)  # wait for loop to initialise
    return t
