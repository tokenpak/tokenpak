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

# ---------------------------------------------------------------------------
# stdlib
# ---------------------------------------------------------------------------
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs


class ProxyRoutesMixin:
    """
    Mixin for ForwardProxyHandler providing GET routes, ingest, and static-file serving.

    Mix in before BaseHTTPRequestHandler in the MRO:

        class ForwardProxyHandler(ProxyRoutesMixin, ProxyMiddlewareMixin, BaseHTTPRequestHandler): ...
    """

    # ------------------------------------------------------------------
    # GET route dispatch
    # ------------------------------------------------------------------

    def do_GET(self):
        # Security check: verify auth for non-localhost clients
        if not self._check_auth():
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized — missing or invalid X-TokenPak-Key header"}).encode())
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
                "docs": "https://github.com/kaywhy331/tokenpak",
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
            body = json.dumps({
                "error": {
                    "type": "method_not_allowed",
                    "message": f"Use POST for {self.path.split('?')[0]}",
                }
            }).encode()
            self.wfile.write(body)
            return

        if self.path == "/health":
            self._route_health()
            return

        if self.path == "/stats":
            self._route_stats()
            return

        if self.path == "/cache-stats":
            from tokenpak.runtime.proxy import _build_cache_stats_payload
            self._send_json(_build_cache_stats_payload())
            return

        if self.path == "/recent":
            from tokenpak.runtime.proxy import MONITOR
            self._send_json({"recent": MONITOR.recent(50)})
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

    def _route_health(self):
        """Handle GET /health with 1-second response cache."""
        import time as _time_module
        from tokenpak.proxy.request_pipeline import (
            _router_health, _health_cache, _HEALTH_CACHE_TTL,
        )
        from tokenpak.proxy.stats import build_health_response
        from tokenpak.proxy.config import (
            COMPILATION_MODE, ROUTER_ENABLED, SKELETON_ENABLED, SHADOW_ENABLED,
            BUDGET_TOTAL_TOKENS, TERM_RESOLVER_ENABLED, TERM_RESOLVER_TOP_K,
            TERM_RESOLVER_MAX_BYTES, STRICT_VALIDATION, UPSTREAM_TIMEOUT,
        )
        from tokenpak.proxy.fallback import _provider_circuits
        from tokenpak.runtime.proxy import (
            SESSION, VAULT_INDEX, CAPSULE_BUILDER, CANON_AVAILABLE,
            TOOL_REGISTRY_AVAILABLE, TERM_RESOLVER, _request_latencies, _get_tool_registry,
        )

        now = _time_module.monotonic()
        if (
            _health_cache["data"] is not None
            and (now - _health_cache["ts"]) < _HEALTH_CACHE_TTL
        ):
            self._send_json(_health_cache["data"])
            return

        vault_info = {
            "available": VAULT_INDEX.available,
            "blocks": len(VAULT_INDEX.blocks),
            "path": str(VAULT_INDEX.tokenpak_dir),
        }
        router_info = _router_health()
        health_data = build_health_response(
            session=SESSION,
            compilation_mode=COMPILATION_MODE,
            vault_info=vault_info,
            router_info=router_info,
            router_enabled=ROUTER_ENABLED,
            capsule_available=CAPSULE_BUILDER is not None,
            canon_available=CANON_AVAILABLE,
            skeleton_enabled=SKELETON_ENABLED,
            shadow_enabled=SHADOW_ENABLED,
            budget_total_tokens=BUDGET_TOTAL_TOKENS,
            tool_registry_stats=(
                _get_tool_registry().stats() if _get_tool_registry() else {}
            )
            if TOOL_REGISTRY_AVAILABLE
            else {},
            tool_registry_available=TOOL_REGISTRY_AVAILABLE,
            term_resolver_enabled=TERM_RESOLVER_ENABLED,
            term_resolver_available=TERM_RESOLVER is not None,
            term_resolver_top_k=TERM_RESOLVER_TOP_K,
            term_resolver_max_bytes=TERM_RESOLVER_MAX_BYTES,
            strict_validation=STRICT_VALIDATION,
            upstream_timeout=UPSTREAM_TIMEOUT,
            provider_circuits=_provider_circuits,
            request_latencies=list(_request_latencies),
        )
        _health_cache["data"] = health_data
        _health_cache["ts"] = now
        self._send_json(health_data)

    def _route_stats(self):
        """Handle GET /stats."""
        from tokenpak.proxy.stats import build_stats_response
        from tokenpak.proxy.config import (
            COMPILATION_MODE, ROUTER_ENABLED, SKELETON_ENABLED, SHADOW_ENABLED,
            BUDGET_TOTAL_TOKENS, MAX_COMPRESSION_TIME_MS,
        )
        from tokenpak.runtime.proxy import SESSION, VAULT_INDEX, CAPSULE_BUILDER, CANON_AVAILABLE, MONITOR

        self._send_json(
            build_stats_response(
                session=SESSION,
                compilation_mode=COMPILATION_MODE,
                vault_info={
                    "available": VAULT_INDEX.available,
                    "blocks": len(VAULT_INDEX.blocks),
                    "last_timing_ms": SESSION.get("vault_last_timing_ms", {}),
                },
                router_enabled=ROUTER_ENABLED,
                capsule_available=CAPSULE_BUILDER is not None,
                compression_timeouts=SESSION.get("compression_timeouts", 0),
                max_compression_time_ms=MAX_COMPRESSION_TIME_MS,
                canon_available=CANON_AVAILABLE,
                skeleton_enabled=SKELETON_ENABLED,
                shadow_enabled=SHADOW_ENABLED,
                budget_total_tokens=BUDGET_TOTAL_TOKENS,
                monitor_today=MONITOR.get_stats(),
                monitor_by_model=MONITOR.get_by_model(),
                monitor_recent=MONITOR.recent(10),
            )
        )

    def _route_stats_last(self):
        """Handle GET /stats/last — per-request stats for the most recent request."""
        from tokenpak.runtime.proxy import SESSION, LAST_REQUEST, _LAST_REQUEST_LOCK

        with _LAST_REQUEST_LOCK:
            if LAST_REQUEST["request_id"] is None:
                self._send_json(
                    {
                        "error": "no_requests",
                        "message": "No requests captured yet. Send a message to see stats.",
                    }
                )
            else:
                self._send_json(
                    {
                        "request_id": LAST_REQUEST["request_id"],
                        "timestamp": LAST_REQUEST["timestamp"],
                        "model": LAST_REQUEST["model"],
                        "tokens_saved": LAST_REQUEST["tokens_saved"],
                        "percent_saved": LAST_REQUEST["percent_saved"],
                        "cost_saved": LAST_REQUEST["cost_saved"],
                        "session_total_saved": round(SESSION["cost_saved"], 4),
                        "session_requests": SESSION["requests"],
                        "input_tokens_raw": LAST_REQUEST["input_tokens_raw"],
                        "input_tokens_sent": LAST_REQUEST["input_tokens_sent"],
                        "output_tokens": LAST_REQUEST["output_tokens"],
                    }
                )

    def _route_stats_session(self):
        """Handle GET /stats/session — session aggregates."""
        from tokenpak.runtime.proxy import SESSION

        uptime_hours = round((time.time() - SESSION["start_time"]) / 3600, 2)
        self._send_json(
            {
                "session_requests": SESSION["requests"],
                "session_total_saved": round(SESSION["cost_saved"], 4),
                "tokens_saved": SESSION["saved_tokens"],
                "tokens_sent": SESSION["sent_input_tokens"],
                "tokens_raw": SESSION["input_tokens"],
                "output_tokens": SESSION["output_tokens"],
                "total_cost": round(SESSION["cost"], 4),
                "uptime_hours": uptime_hours,
                "errors": SESSION["errors"],
                "avg_savings_pct": round(
                    SESSION["saved_tokens"] / SESSION["input_tokens"] * 100, 1
                )
                if SESSION["input_tokens"] > 0
                else 0.0,
            }
        )

    def _route_savings(self):
        """Handle GET /savings[?since=...]."""
        from tokenpak.runtime.proxy import MONITOR

        parsed = urlparse(self.path)
        qparams = parse_qs(parsed.query)
        since = qparams.get("since", [None])[0]
        self._send_json(MONITOR.get_savings_report(since=since))

    def _route_vault_debug(self):
        """Handle GET /vault — debug endpoint showing vault index state."""
        from tokenpak.runtime.proxy import VAULT_INDEX

        blocks_info = []
        for bid, block in VAULT_INDEX.blocks.items():
            blocks_info.append(
                {
                    "block_id": bid,
                    "source_path": block["source_path"],
                    "risk_class": block["risk_class"],
                    "raw_tokens": block["raw_tokens"],
                }
            )
        self._send_json(
            {
                "available": VAULT_INDEX.available,
                "blocks": len(VAULT_INDEX.blocks),
                "total_tokens": sum(b["raw_tokens"] for b in VAULT_INDEX.blocks.values()),
                "path": str(VAULT_INDEX.tokenpak_dir),
                "block_list": blocks_info,
            }
        )

    def _route_trace_last(self):
        """Handle GET /trace/last."""
        from tokenpak.proxy.tracing import TRACE_STORAGE

        trace = TRACE_STORAGE.get_last()
        if trace:
            self._send_json(trace.to_dict())
        else:
            self._send_json(
                {
                    "error": "no traces",
                    "message": "No requests captured yet. Send a message to see the pipeline in action.",
                }
            )

    def _route_trace_by_id(self):
        """Handle GET /trace/{request_id}."""
        from tokenpak.proxy.tracing import TRACE_STORAGE

        request_id = self.path.split("/trace/")[1]
        trace = TRACE_STORAGE.get_by_id(request_id)
        if trace:
            self._send_json(trace.to_dict())
        else:
            self._send_json(
                {
                    "error": "not found",
                    "message": f"No trace found for request_id: {request_id}",
                }
            )

    def _route_traces_list(self):
        """Handle GET /traces."""
        from tokenpak.proxy.tracing import TRACE_STORAGE

        traces = TRACE_STORAGE.get_all()
        self._send_json({"traces": [t.to_dict() for t in traces], "count": len(traces)})

    def _route_metrics_prometheus(self):
        """Handle GET /metrics — Prometheus text format metrics."""
        from tokenpak.runtime.proxy import SESSION, VAULT_INDEX, MONITOR

        try:
            from tokenpak.metrics.prometheus import build_metrics_text

            vault_blocks = len(VAULT_INDEX.blocks) if VAULT_INDEX.available else 0
            body_out = build_metrics_text(SESSION, MONITOR, vault_blocks=vault_blocks).encode()
        except Exception:
            # Fallback: minimal unlabeled metrics if module unavailable
            s = SESSION
            uptime = int(time.time() - s.get("start_time", time.time()))
            lines = [
                f"tokenpak_requests_total {s.get('requests', 0)}",
                f"tokenpak_tokens_input_total {s.get('input_tokens', 0)}",
                f"tokenpak_tokens_saved_total {s.get('saved_tokens', 0)}",
                f"tokenpak_errors_total {s.get('errors', 0)}",
                f"tokenpak_uptime_seconds {uptime}",
            ]
            body_out = "\n".join(lines).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", len(body_out))
        self.end_headers()
        self.wfile.write(body_out)

    def _route_metrics_dashboard(self):
        """Handle GET /metrics/dashboard — comprehensive 8-metric dashboard payload."""
        from tokenpak.runtime.proxy import SESSION, MONITOR

        today_stats = MONITOR.get_stats(hours=24)
        recent_reqs = MONITOR.recent(limit=100)
        by_model = MONITOR.get_by_model()
        uptime_secs = int(time.time() - SESSION["start_time"])
        uptime_hours = max(0.01, uptime_secs / 3600.0)

        # Throughput (req/sec)
        if len(recent_reqs) > 1:
            first_ts = datetime.fromisoformat(recent_reqs[-1]["timestamp"])
            last_ts = datetime.fromisoformat(recent_reqs[0]["timestamp"])
            time_diff_secs = max(1, (last_ts - first_ts).total_seconds())
            throughput = len(recent_reqs) / time_diff_secs
        else:
            throughput = today_stats["requests"] / uptime_hours / 3600.0

        # Latency percentiles
        latencies = [r.get("latency_ms", 0) for r in recent_reqs if r.get("latency_ms")]
        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
        avg_latency = today_stats.get("avg_latency_ms", 0)

        # Error rate and top failure types
        error_count = sum(1 for r in recent_reqs if r.get("status_code", 200) >= 400)
        error_rate = error_count / len(recent_reqs) if recent_reqs else 0
        failure_types: dict = {}
        for r in recent_reqs:
            sc = r.get("status_code", 200)
            if sc >= 400:
                failure_types[str(sc)] = failure_types.get(str(sc), 0) + 1

        # Cache hit ratio
        total_cache_read = today_stats.get("cache_read_tokens", 0)
        total_cache_creation = today_stats.get("cache_creation_tokens", 0)
        cache_hit_ratio = 0.0
        if total_cache_read > 0 or total_cache_creation > 0:
            cache_hit_ratio = (
                total_cache_read / (total_cache_read + total_cache_creation)
                if (total_cache_read + total_cache_creation) > 0
                else 0.0
            )

        # Model distribution
        model_dist = {}
        for model, data in by_model.items():
            model_dist[model] = {
                "requests": data.get("requests", 0),
                "input_tokens": data.get("input_tokens", 0),
                "cost": data.get("cost", 0.0),
            }

        self._send_json({
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
                "smart_routing_hit_rate": 0.0,   # Placeholder
                "fallback_chain_usage": 0,        # Placeholder
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
            "streaming": {"count": 0, "percentage": 0.0},   # Placeholder
            "window_24h": {
                "input_tokens": today_stats.get("input_tokens", 0),
                "output_tokens": today_stats.get("output_tokens", 0),
                "protected_tokens": today_stats.get("protected_tokens", 0),
                "compressed_tokens": today_stats.get("compressed_tokens", 0),
                "injected_tokens": today_stats.get("injected_tokens", 0),
                "total_cost": today_stats.get("total_cost", 0.0),
            },
        })

    # ------------------------------------------------------------------
    # Ingest handlers
    # ------------------------------------------------------------------

    def _ingest(self, path):
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

    def _ingest_single(self, payload):
        """Handle single entry ingest."""
        from tokenpak.runtime.proxy import SESSION, _ingest_write_entry

        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return
        required = {"model", "tokens", "cost"}
        missing = required - set(payload.keys())
        if missing:
            self._send_json({"error": f"missing required fields: {', '.join(missing)}"}, status=400)
            return
        try:
            model = payload.get("model")
            tokens = payload.get("tokens")
            cost = payload.get("cost")
            if not isinstance(model, str) or not model:
                raise ValueError("model must be a non-empty string")
            if not isinstance(tokens, int) or tokens < 0:
                raise ValueError("tokens must be a non-negative integer")
            if not isinstance(cost, (int, float)) or cost < 0:
                raise ValueError("cost must be a non-negative number")
            timestamp = payload.get("timestamp")
            if timestamp is not None:
                if not isinstance(timestamp, str):
                    raise ValueError("timestamp must be a string")
                try:
                    datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(f"invalid ISO 8601 timestamp: {timestamp}")
            else:
                timestamp = datetime.now(timezone.utc).isoformat()
                payload["timestamp"] = timestamp
            entry_id = _ingest_write_entry(payload)
            self._send_json({"status": "ok", "ids": [entry_id]}, status=200)
            SESSION["ingest_entries"] = SESSION.get("ingest_entries", 0) + 1
        except ValueError as e:
            self._send_json({"error": str(e)}, status=422)
        except Exception as e:
            self._send_json({"error": f"internal error: {e}"}, status=500)

    def _ingest_batch(self, payload):
        """Handle batch entry ingest."""
        from tokenpak.runtime.proxy import SESSION, _ingest_write_entry

        if not isinstance(payload, dict):
            self._send_json({"error": "expected object, got " + type(payload).__name__}, status=400)
            return
        if "events" not in payload:
            self._send_json({"error": "missing 'events' field"}, status=400)
            return
        events = payload["events"]
        if not isinstance(events, list):
            self._send_json({"error": "events must be a list"}, status=400)
            return
        if len(events) == 0:
            self._send_json({"error": "events list cannot be empty"}, status=400)
            return
        if len(events) > 1000:
            self._send_json({"error": "events list too large (max 1000)"}, status=400)
            return

        ids = []
        errors = []
        for i, event in enumerate(events):
            if not isinstance(event, dict):
                errors.append(f"event[{i}]: expected object, got {type(event).__name__}")
                continue
            required = {"model", "tokens", "cost"}
            missing = required - set(event.keys())
            if missing:
                errors.append(f"event[{i}]: missing fields {', '.join(missing)}")
                continue
            try:
                model = event.get("model")
                tokens = event.get("tokens")
                cost = event.get("cost")
                if not isinstance(model, str) or not model:
                    raise ValueError("model must be non-empty string")
                if not isinstance(tokens, int) or tokens < 0:
                    raise ValueError("tokens must be non-negative int")
                if not isinstance(cost, (int, float)) or cost < 0:
                    raise ValueError("cost must be non-negative number")
                timestamp = event.get("timestamp")
                if timestamp is not None:
                    if not isinstance(timestamp, str):
                        raise ValueError("timestamp must be string")
                    try:
                        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    except ValueError:
                        raise ValueError(f"invalid timestamp: {timestamp}")
                else:
                    timestamp = datetime.now(timezone.utc).isoformat()
                    event["timestamp"] = timestamp
                entry_id = _ingest_write_entry(event)
                ids.append(entry_id)
            except ValueError as e:
                errors.append(f"event[{i}]: {e}")

        if ids:
            self._send_json(
                {"status": "ok", "ids": ids, "errors": errors if errors else None}, status=200
            )
            SESSION["ingest_entries"] = SESSION.get("ingest_entries", 0) + len(ids)
        else:
            self._send_json({"error": f"all events failed: {'; '.join(errors)}"}, status=422)

    # ------------------------------------------------------------------
    # Static / UI handlers
    # ------------------------------------------------------------------

    def _serve_api_docs(self):
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

    def _serve_openapi_yaml(self):
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

    def _serve_dashboard(self):
        """Serve static dashboard files (HTML/CSS/JS)."""
        from tokenpak.proxy.config import DASHBOARD_AUTH_ENABLED

        if DASHBOARD_AUTH_ENABLED:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            provided = params.get("token", [None])[0]
            from tokenpak.token_manager import load_or_create_token
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

        dashboard_dir = Path(__file__).parent / "tokenpak" / "dashboard"

        if self.path == "/dashboard" or self.path == "/dashboard/":
            file_path = dashboard_dir / "index.html"
            content_type = "text/html; charset=utf-8"
        else:
            rel_path = self.path[len("/dashboard/"):]
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
            self._send_json(
                {"error": {"type": "not_found", "message": f"File not found: {rel_path}"}},
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
