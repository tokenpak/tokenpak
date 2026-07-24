"""
tokenpak.proxy.routes — GET route handlers, ingest handlers, and static-file servers.

Extracted from proxy/server.py as part of TPK-RESTRUCTURE-012.
Provides a mixin class (ProxyRoutesMixin) with:

  GET handlers (called by ForwardProxyHandler.do_GET):
    - do_GET()              — full GET route-dispatch implementation

  Ingest handlers (called by ForwardProxyHandler.do_POST):
    - _ingest()             — dispatch /ingest and /ingest/batch
    - _ingest_single()      — single-entry ingest
    - _ingest_batch()       — batch-entry ingest

  Static / UI handlers:
    - _serve_api_docs()     — Swagger UI
    - _serve_openapi_yaml() — OpenAPI YAML spec
    - _serve_dashboard()    — static dashboard files
"""

from __future__ import annotations

__all__ = ("ProxyRoutesMixin",)


# ---------------------------------------------------------------------------
# stdlib
# ---------------------------------------------------------------------------
import json
import time
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, TypedDict, cast
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from tokenpak.proxy.server import ProxyServer


def _as_int(value: object, default: int = 0) -> int:
    """Normalize one untrusted telemetry value without inventing data."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    """Normalize one untrusted telemetry value without inventing data."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


class _ModelStats(TypedDict, total=False):
    """Per-model monitor fields exposed by the dashboard route."""

    requests: int
    input_tokens: int
    cost: float


class ProxyRoutesMixin:
    """
    Mixin for ForwardProxyHandler providing GET routes, ingest, and static-file serving.

    Mix in before BaseHTTPRequestHandler in the MRO:

        class ForwardProxyHandler(ProxyRoutesMixin, ProxyMiddlewareMixin, BaseHTTPRequestHandler): ...
    """

    if TYPE_CHECKING:
        _ps: ProxyServer
        headers: Message
        path: str
        rfile: BinaryIO
        wfile: BinaryIO

        def _check_auth(self) -> bool: ...

        def _forward_request(self, method: str) -> None: ...

        def _ollama_proxy(self, method: str) -> None: ...

        def _send_json(self, data: object, *, status: int = 200) -> None: ...

        def end_headers(self) -> None: ...

        def send_header(self, keyword: str, value: str | int) -> None: ...

        def send_response(self, code: int, message: str | None = None) -> None: ...

    # ------------------------------------------------------------------
    # GET route dispatch
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        # Security check: verify auth for non-localhost clients
        if not self._check_auth():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"error": "Unauthorized — missing or invalid X-TokenPak-Key header"}
                ).encode()
            )
            return

        if self.path == "/" or self.path == "":
            # Root path returns welcome JSON instead of 404
            try:
                from tokenpak import __version__ as _tpk_version
            except ImportError:
                _tpk_version = "unknown"
            welcome = {
                "name": "TokenPak",
                "version": _tpk_version,
                "status": "running",
                "endpoints": {
                    "health": "/health",
                    "stats": "/stats",
                    "docs": "/docs",
                    "proxy": "/v1/messages (POST), /v1/chat/completions (POST)",
                },
                "docs": "https://github.com/tokenpak/tokenpak",
            }
            self._send_json(welcome)
            return

        # POST-only paths return 405 instead of 404 on wrong method
        _POST_ONLY_PATHS = {"/v1/messages", "/v1/chat/completions", "/ingest"}
        if self.path.split("?")[0] in _POST_ONLY_PATHS:
            self.send_response(405)
            self.send_header("Allow", "POST, OPTIONS")
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            body = json.dumps(
                {
                    "error": {
                        "type": "method_not_allowed",
                        "message": f"Use POST for {self.path.split('?')[0]}",
                    }
                }
            ).encode()
            self.wfile.write(body)
            return

        if self.path == "/health":
            self._route_health()
            return

        if self.path == "/stats":
            self._route_stats()
            return

        if self.path == "/cache-stats":
            from tokenpak.proxy.cache_stats import _build_cache_stats_payload

            monitor = self._ps.monitor
            self._send_json(
                _build_cache_stats_payload(
                    session=dict(self._ps.session),
                    db_path=str(monitor.db_path) if monitor is not None else None,
                )
            )
            return

        if self.path == "/recent":
            monitor = self._ps.monitor
            self._send_json({"recent": monitor.recent(50) if monitor is not None else []})
            return

        if self.path == "/stats/last":
            self._route_stats_last()
            return

        if self.path == "/stats/session":
            self._route_stats_session()
            return

        if self.path == "/savings" or self.path.startswith("/savings?"):
            self._route_savings()
            return

        if self.path == "/vault":
            self._route_vault_debug()
            return

        if self.path == "/trace/last":
            self._route_trace_last()
            return

        if self.path.startswith("/trace/"):
            self._route_trace_by_id()
            return

        if self.path == "/traces":
            self._route_traces_list()
            return

        if self.path == "/metrics":
            self._route_metrics_prometheus()
            return

        if self.path == "/metrics/dashboard":
            self._route_metrics_dashboard()
            return

        if self.path.startswith("http"):
            self._forward_request("GET")
        elif self.path.split("?")[0] == "/dashboard" or self.path.split("?")[0].startswith(
            "/dashboard/"
        ):
            self._serve_dashboard()
        elif self.path == "/docs" or self.path == "/docs/":
            self._serve_api_docs()
        elif self.path == "/openapi.yaml":
            self._serve_openapi_yaml()
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("GET")
        else:
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    # ------------------------------------------------------------------
    # GET route handlers
    # ------------------------------------------------------------------

    def _route_health(self) -> None:
        """Handle the legacy mixin's cached ``GET /health`` contract.

        ``ProxyRoutesMixin`` is a public, snapshotted compatibility surface but
        is not the handler used by :class:`ProxyServer`.  Keep its v1.13 payload
        and one-second cache separate from the canonical, uncached
        ``ProxyServer.health()`` endpoint.
        """
        from tokenpak.proxy.request_pipeline import (
            _HEALTH_CACHE_TTL,
            _health_cache,
        )

        now = time.monotonic()
        cached = _health_cache["data"]
        if cached is not None and (now - _health_cache["ts"]) < _HEALTH_CACHE_TTL:
            self._send_json(cached)
            return

        health_data = self._build_legacy_health_payload()
        _health_cache["data"] = health_data
        _health_cache["ts"] = now
        self._send_json(health_data)

    def _build_legacy_health_payload(self) -> dict[str, object]:
        """Build the v1.13 mixin payload from current compatibility shims."""
        from tokenpak.core.runtime.proxy import SESSION
        from tokenpak.proxy.config import (
            BUDGET_TOTAL_TOKENS,
            COMPILATION_MODE,
            QUERY_EXPANSION_ENABLED,
            ROUTER_ENABLED,
            SHADOW_ENABLED,
            TERM_RESOLVER_ENABLED,
            TERM_RESOLVER_MAX_BYTES,
            TERM_RESOLVER_TOP_K,
            UPSTREAM_TIMEOUT,
            VAULT_INDEX_PATH,
            skeleton_active,
        )
        from tokenpak.proxy.fallback import _provider_circuits
        from tokenpak.proxy.request_pipeline import _router_health
        from tokenpak.proxy.stats import build_health_response
        from tokenpak.proxy.vault_bridge import (
            get_capsule_builder,
            get_term_resolver,
            get_vault_index,
        )

        vault_index = get_vault_index()
        raw_blocks = getattr(vault_index, "blocks", None)
        if isinstance(raw_blocks, (dict, list, tuple, set)):
            block_count = len(raw_blocks)
        else:
            raw_count = getattr(vault_index, "block_count", 0)
            block_count = raw_count if isinstance(raw_count, int) else 0

        return build_health_response(
            session=SESSION,
            compilation_mode=COMPILATION_MODE,
            vault_info={
                "available": bool(getattr(vault_index, "available", False)),
                "blocks": block_count,
                "path": str(getattr(vault_index, "tokenpak_dir", VAULT_INDEX_PATH)),
            },
            router_info=_router_health(),
            router_enabled=ROUTER_ENABLED,
            capsule_available=get_capsule_builder() is not None,
            # The removed canon/tool-registry singletons have no current
            # equivalent.  Report them unavailable instead of inventing state.
            canon_available=False,
            skeleton_enabled=skeleton_active(),
            shadow_enabled=SHADOW_ENABLED,
            budget_total_tokens=BUDGET_TOTAL_TOKENS,
            tool_registry_stats={},
            tool_registry_available=False,
            term_resolver_enabled=TERM_RESOLVER_ENABLED,
            term_resolver_available=get_term_resolver() is not None,
            term_resolver_top_k=TERM_RESOLVER_TOP_K,
            term_resolver_max_bytes=TERM_RESOLVER_MAX_BYTES,
            query_expansion_enabled=QUERY_EXPANSION_ENABLED,
            upstream_timeout=UPSTREAM_TIMEOUT,
            provider_circuits=_provider_circuits,
            # The legacy global latency deque no longer exists.  Empty means
            # unavailable/no observations and preserves the v1.13 wire shape.
            request_latencies=[],
        )

    def _route_stats(self) -> None:
        """Handle GET /stats."""
        self._send_json(self._ps.stats())

    def _route_stats_last(self) -> None:
        """Handle GET /stats/last — per-request stats for the most recent request."""
        self._send_json(self._ps.last_request_stats())

    def _route_stats_session(self) -> None:
        """Handle GET /stats/session — session aggregates."""
        self._send_json(self._ps.session_stats())

    def _route_savings(self) -> None:
        """Handle GET /savings[?since=...]."""
        parsed = urlparse(self.path)
        qparams = parse_qs(parsed.query)
        since = qparams.get("since", [None])[0]
        monitor = self._ps.monitor
        if monitor is None:
            self._send_json({"error": "monitor_unavailable"}, status=503)
            return
        self._send_json(monitor.get_savings_report(since=since))

    def _route_vault_debug(self) -> None:
        """Handle GET /vault — debug endpoint showing vault index state."""
        from collections.abc import Mapping

        from tokenpak.proxy.vault_bridge import get_vault_index

        vault_index = get_vault_index()
        raw_blocks = getattr(vault_index, "blocks", {})
        blocks = raw_blocks if isinstance(raw_blocks, Mapping) else {}

        blocks_info: list[dict[str, object]] = []
        total_tokens = 0
        for bid, raw_block in blocks.items():
            if not isinstance(raw_block, Mapping):
                continue
            raw_tokens = _as_int(raw_block.get("raw_tokens"))
            total_tokens += raw_tokens
            blocks_info.append(
                {
                    "block_id": bid,
                    "source_path": raw_block.get("source_path", ""),
                    "risk_class": raw_block.get("risk_class", "unknown"),
                    "raw_tokens": raw_tokens,
                }
            )
        self._send_json(
            {
                "available": vault_index.available,
                "blocks": len(blocks),
                "total_tokens": total_tokens,
                "path": str(getattr(vault_index, "tokenpak_dir", "")),
                "block_list": blocks_info,
            }
        )

    def _route_trace_last(self) -> None:
        """Handle GET /trace/last."""
        trace = self._ps.trace_storage.get_last()
        if trace:
            self._send_json(trace.to_dict())
        else:
            self._send_json(
                {
                    "error": "no traces",
                    "message": "No requests captured yet. Send a message to see the pipeline in action.",
                }
            )

    def _route_trace_by_id(self) -> None:
        """Handle GET /trace/{request_id}."""
        request_id = self.path.split("/trace/")[1]
        trace = self._ps.trace_storage.get_by_id(request_id)
        if trace:
            self._send_json(trace.to_dict())
        else:
            self._send_json(
                {
                    "error": "not found",
                    "message": f"No trace found for request_id: {request_id}",
                }
            )

    def _route_traces_list(self) -> None:
        """Handle GET /traces."""
        traces = self._ps.trace_storage.get_all()
        self._send_json({"traces": [t.to_dict() for t in traces], "count": len(traces)})

    def _route_metrics_prometheus(self) -> None:
        """Handle GET /metrics — Prometheus text format metrics."""
        session = dict(self._ps.session)
        monitor = self._ps.monitor
        try:
            from tokenpak.proxy.vault_bridge import get_vault_index
            from tokenpak.telemetry.metrics.prometheus import build_metrics_text

            vault_index = get_vault_index()
            raw_blocks = getattr(vault_index, "blocks", {})
            vault_blocks = (
                len(raw_blocks) if vault_index.available and isinstance(raw_blocks, dict) else 0
            )
            body_out = build_metrics_text(session, monitor, vault_blocks=vault_blocks).encode()
        except Exception:
            # Fallback: minimal unlabeled metrics if module unavailable
            uptime = int(time.time() - _as_float(session.get("start_time"), time.time()))
            lines = [
                f"tokenpak_requests_total {session.get('requests', 0)}",
                f"tokenpak_tokens_input_total {session.get('input_tokens', 0)}",
                f"tokenpak_tokens_saved_total {session.get('saved_tokens', 0)}",
                f"tokenpak_errors_total {session.get('errors', 0)}",
                f"tokenpak_uptime_seconds {uptime}",
            ]
            body_out = "\n".join(lines).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", len(body_out))
        self.end_headers()
        self.wfile.write(body_out)

    def _route_metrics_dashboard(self) -> None:
        """Handle GET /metrics/dashboard — comprehensive 8-metric dashboard payload."""
        monitor = self._ps.monitor
        today_stats = monitor.get_stats(hours=24) if monitor is not None else {}
        recent_reqs = monitor.recent(limit=100) if monitor is not None else []
        by_model = monitor.get_by_model() if monitor is not None else {}
        uptime_secs = int(time.time() - self._ps.session["start_time"])
        uptime_hours = max(0.01, uptime_secs / 3600.0)

        # Throughput (req/sec)
        if len(recent_reqs) > 1:
            first_value = recent_reqs[-1].get("timestamp")
            last_value = recent_reqs[0].get("timestamp")
            if isinstance(first_value, str) and isinstance(last_value, str):
                first_ts = datetime.fromisoformat(first_value)
                last_ts = datetime.fromisoformat(last_value)
                time_diff_secs = max(1.0, (last_ts - first_ts).total_seconds())
                throughput = len(recent_reqs) / time_diff_secs
            else:
                throughput = _as_float(today_stats.get("requests")) / uptime_hours / 3600.0
        else:
            throughput = _as_float(today_stats.get("requests")) / uptime_hours / 3600.0

        # Latency percentiles
        latencies = [
            _as_float(latency)
            for row in recent_reqs
            if (latency := row.get("latency_ms")) is not None
        ]
        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
        avg_latency = _as_float(today_stats.get("avg_latency_ms"))

        # Error rate and top failure types
        error_count = sum(1 for row in recent_reqs if _as_int(row.get("status_code"), 200) >= 400)
        error_rate = error_count / len(recent_reqs) if recent_reqs else 0
        failure_types: dict[str, int] = {}
        for r in recent_reqs:
            sc = _as_int(r.get("status_code"), 200)
            if sc >= 400:
                failure_types[str(sc)] = failure_types.get(str(sc), 0) + 1

        # Cache hit ratio
        total_cache_read = _as_int(today_stats.get("cache_read_tokens"))
        total_cache_creation = _as_int(today_stats.get("cache_creation_tokens"))
        cache_hit_ratio = 0.0
        if total_cache_read > 0 or total_cache_creation > 0:
            cache_hit_ratio = (
                total_cache_read / (total_cache_read + total_cache_creation)
                if (total_cache_read + total_cache_creation) > 0
                else 0.0
            )

        # Model distribution
        model_dist: dict[str, _ModelStats] = {}
        for model, data in by_model.items():
            model_dist[model] = {
                "requests": _as_int(data.get("requests")),
                "input_tokens": _as_int(data.get("input_tokens")),
                "cost": _as_float(data.get("cost")),
            }

        self._send_json(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": uptime_secs,
                "uptime_hours": round(uptime_hours, 2),
                "requests": {
                    "total": today_stats.get("requests", 0),
                    "throughput_req_per_sec": round(throughput, 3),
                    "24h_window": True,
                },
                "latency": {
                    "p50_ms": round(p50, 1),
                    "p95_ms": round(p95, 1),
                    "p99_ms": round(p99, 1),
                    "avg_ms": round(avg_latency, 1),
                    "samples": len(latencies),
                },
                "models": model_dist,
                "model_count": len(model_dist),
                "routing": {
                    "smart_routing_hit_rate": 0.0,  # Placeholder
                    "fallback_chain_usage": 0,  # Placeholder
                },
                "cache": {
                    "hit_ratio": round(cache_hit_ratio, 3),
                    "read_tokens": total_cache_read,
                    "creation_tokens": total_cache_creation,
                },
                "errors": {
                    "error_rate": round(error_rate, 4),
                    "error_count": error_count,
                    "top_failures": dict(
                        sorted(failure_types.items(), key=lambda x: x[1], reverse=True)[:5]
                    ),
                },
                "streaming": {"count": 0, "percentage": 0.0},  # Placeholder
                "window_24h": {
                    "input_tokens": today_stats.get("input_tokens", 0),
                    "output_tokens": today_stats.get("output_tokens", 0),
                    "protected_tokens": today_stats.get("protected_tokens", 0),
                    "compressed_tokens": today_stats.get("compressed_tokens", 0),
                    "injected_tokens": today_stats.get("injected_tokens", 0),
                    "total_cost": today_stats.get("total_cost", 0.0),
                },
            }
        )

    # ------------------------------------------------------------------
    # Ingest handlers
    # ------------------------------------------------------------------

    def _ingest(self, path: str) -> None:
        """Handle /ingest and /ingest/batch POST requests."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json({"error": "empty request body"}, status=400)
            return
        if content_length > 1024 * 1024:  # 1 MB limit
            self._send_json({"error": "request body too large (max 1MB)"}, status=413)
            return
        try:
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self._send_json({"error": f"invalid JSON: {e}"}, status=400)
            return
        if path == "/ingest":
            self._ingest_single(payload)
        elif path == "/ingest/batch":
            self._ingest_batch(payload)

    def _ingest_single(self, payload: object) -> None:
        """Handle single entry ingest."""
        from tokenpak.vault.indexer import _ingest_write_entry

        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return
        entry = cast(dict[str, object], payload)
        required = {"model", "tokens", "cost"}
        missing = required - set(entry.keys())
        if missing:
            self._send_json({"error": f"missing required fields: {', '.join(missing)}"}, status=400)
            return
        try:
            model = entry.get("model")
            tokens = entry.get("tokens")
            cost = entry.get("cost")
            if not isinstance(model, str) or not model:
                raise ValueError("model must be a non-empty string")
            if not isinstance(tokens, int) or tokens < 0:
                raise ValueError("tokens must be a non-negative integer")
            if not isinstance(cost, (int, float)) or cost < 0:
                raise ValueError("cost must be a non-negative number")
            timestamp = entry.get("timestamp")
            if timestamp is not None:
                if not isinstance(timestamp, str):
                    raise ValueError("timestamp must be a string")
                try:
                    datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(f"invalid ISO 8601 timestamp: {timestamp}")
            else:
                timestamp = datetime.now(timezone.utc).isoformat()
                entry["timestamp"] = timestamp
            entry_id = _ingest_write_entry(entry)
            self._send_json({"status": "ok", "ids": [entry_id]}, status=200)
            self._ps.session["ingest_entries"] += 1
        except ValueError as e:
            self._send_json({"error": str(e)}, status=422)
        except Exception as e:
            self._send_json({"error": f"internal error: {e}"}, status=500)

    def _ingest_batch(self, payload: object) -> None:
        """Handle batch entry ingest."""
        from tokenpak.vault.indexer import _ingest_write_entry

        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return
        batch = cast(dict[str, object], payload)
        if "events" not in batch:
            self._send_json({"error": "missing 'events' field"}, status=400)
            return
        events = batch["events"]
        if not isinstance(events, list):
            self._send_json({"error": "events must be a list"}, status=400)
            return
        if len(events) == 0:
            self._send_json({"error": "events list cannot be empty"}, status=400)
            return
        if len(events) > 1000:
            self._send_json({"error": "events list too large (max 1000)"}, status=400)
            return

        ids: list[str] = []
        errors: list[str] = []
        for i, raw_event in enumerate(cast(list[object], events)):
            event = raw_event
            if not isinstance(event, dict):
                errors.append(f"event[{i}]: expected object, got {type(event).__name__}")
                continue
            typed_event = cast(dict[str, object], event)
            required = {"model", "tokens", "cost"}
            missing = required - set(typed_event.keys())
            if missing:
                errors.append(f"event[{i}]: missing fields {', '.join(missing)}")
                continue
            try:
                model = typed_event.get("model")
                tokens = typed_event.get("tokens")
                cost = typed_event.get("cost")
                if not isinstance(model, str) or not model:
                    raise ValueError("model must be non-empty string")
                if not isinstance(tokens, int) or tokens < 0:
                    raise ValueError("tokens must be non-negative int")
                if not isinstance(cost, (int, float)) or cost < 0:
                    raise ValueError("cost must be non-negative number")
                timestamp = typed_event.get("timestamp")
                if timestamp is not None:
                    if not isinstance(timestamp, str):
                        raise ValueError("timestamp must be string")
                    try:
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except ValueError:
                        raise ValueError(f"invalid timestamp: {timestamp}")
                else:
                    timestamp = datetime.now(timezone.utc).isoformat()
                    typed_event["timestamp"] = timestamp
                entry_id = _ingest_write_entry(typed_event)
                ids.append(entry_id)
            except ValueError as e:
                errors.append(f"event[{i}]: {e}")

        if ids:
            self._send_json(
                {"status": "ok", "ids": ids, "errors": errors if errors else None}, status=200
            )
            self._ps.session["ingest_entries"] += len(ids)
        else:
            self._send_json({"error": f"all events failed: {'; '.join(errors)}"}, status=422)

    # ------------------------------------------------------------------
    # Static / UI handlers
    # ------------------------------------------------------------------

    def _serve_api_docs(self) -> None:
        """Serve Swagger UI for interactive API documentation."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TokenPak API Docs</title>
  <link rel="stylesheet" type="text/css" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; background: #fafafa; }
    .swagger-ui .topbar { background: #1a1a2e; }
    .swagger-ui .topbar .download-url-wrapper { display: none; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-standalone-preset.js"></script>
  <script>
    window.onload = function() {
      SwaggerUIBundle({
        url: "/openapi.yaml",
        dom_id: "#swagger-ui",
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        layout: "StandaloneLayout",
        deepLinking: true,
        defaultModelsExpandDepth: 1,
        tryItOutEnabled: true,
      });
    };
  </script>
</body>
</html>"""
        body_bytes = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def _serve_openapi_yaml(self) -> None:
        """Serve the OpenAPI YAML spec file."""
        import pathlib

        candidates = [
            pathlib.Path(__file__).parent.parent.parent / "docs" / "openapi.yaml",
            pathlib.Path(__file__).parent.parent / "docs" / "openapi.yaml",
        ]
        for spec_path in candidates:
            if spec_path.exists():
                body_bytes = spec_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/yaml")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                return
        self._send_json(
            {"error": {"type": "not_found", "message": "openapi.yaml not found"}}, status=404
        )

    def _serve_dashboard(self) -> None:
        """Serve static dashboard files (HTML/CSS/JS)."""
        from tokenpak.dashboard import CCI09_DASHBOARD_MODES
        from tokenpak.proxy.config import DASHBOARD_AUTH_ENABLED

        parsed = urlparse(self.path)
        mode = parse_qs(parsed.query).get("mode", [None])[0]
        if mode and mode not in CCI09_DASHBOARD_MODES:
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown dashboard mode: {mode}"}},
                status=404,
            )
            return

        if DASHBOARD_AUTH_ENABLED:
            params = parse_qs(parsed.query)
            provided = params.get("token", [None])[0]
            from tokenpak.telemetry.token_manager import load_or_create_token

            expected = load_or_create_token()
            if not provided or provided != expected:
                self._send_json(
                    {
                        "error": {
                            "type": "unauthorized",
                            "message": "Dashboard token required. Append ?token=<your-token> to the URL.",
                        }
                    },
                    status=401,
                )
                return
            self.path = parsed.path

        dashboard_dir = Path(__file__).parents[1] / "dashboard"

        dashboard_request_path = parsed.path
        if dashboard_request_path == "/dashboard" or dashboard_request_path == "/dashboard/":
            file_path = dashboard_dir / "index.html"
            content_type = "text/html; charset=utf-8"
        else:
            rel_path = dashboard_request_path[len("/dashboard/") :]
            file_path = (dashboard_dir / rel_path).resolve()
            if not str(file_path).startswith(str(dashboard_dir.resolve())):
                self._send_json(
                    {"error": {"type": "forbidden", "message": "Access denied"}}, status=403
                )
                return
            if rel_path.endswith(".html"):
                content_type = "text/html; charset=utf-8"
            elif rel_path.endswith(".css"):
                content_type = "text/css; charset=utf-8"
            elif rel_path.endswith(".js"):
                content_type = "application/javascript; charset=utf-8"
            elif rel_path.endswith(".json"):
                content_type = "application/json"
            else:
                content_type = "application/octet-stream"

        if not file_path.exists():
            missing_path = (
                dashboard_request_path
                if dashboard_request_path in ("/dashboard", "/dashboard/")
                else rel_path
            )
            self._send_json(
                {"error": {"type": "not_found", "message": f"File not found: {missing_path}"}},
                status=404,
            )
            return

        try:
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send_json({"error": {"type": "server_error", "message": str(e)}}, status=500)
