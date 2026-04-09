"""
tokenpak.proxy.server — ForwardProxyHandler HTTP request handler.

Extracted from runtime/proxy.py (lines 1291-3809) as part of TPK-RESTRUCTURE-007.
Decomposed in TPK-RESTRUCTURE-012:
  - proxy/middleware.py  — auth, tunnel (do_CONNECT/_tunnel_connect), _send_json
  - proxy/routes.py      — do_GET routes, _ingest*, _serve_api_docs/_serve_dashboard/_serve_openapi_yaml
"""

# ---------------------------------------------------------------------------
# stdlib imports needed by ForwardProxyHandler
# ---------------------------------------------------------------------------
import gzip
import json
import os
import socket
import time
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import urllib3  # noqa: F401 — used in _proxy_to

# _time_module alias — used in do_GET health cache check
import time as _time_module  # noqa: F401

# ---------------------------------------------------------------------------
# tokenpak.proxy.* imports (already present in runtime/proxy.py)
# ---------------------------------------------------------------------------
from tokenpak.proxy.adapters.base import FormatAdapter  # noqa: F401
from tokenpak.proxy.streaming import _extract_sse_tokens  # noqa: F401
from tokenpak.proxy.cache_poison import (  # noqa: F401
    _strip_cache_poisons,
    _classify_cache_miss_reason,
)
from tokenpak.proxy.stats import build_health_response, build_stats_response  # noqa: F401
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

from tokenpak.proxy.middleware import ProxyMiddlewareMixin  # noqa: E402
from tokenpak.proxy.routes import ProxyRoutesMixin  # noqa: E402


class ForwardProxyHandler(ProxyRoutesMixin, ProxyMiddlewareMixin, BaseHTTPRequestHandler):
    """HTTP request handler for the TokenPak proxy.

    Mixins (MRO order):
      ProxyRoutesMixin    — do_GET routes, ingest, static-file serve
      ProxyMiddlewareMixin — auth, CONNECT tunnel, _send_json
      BaseHTTPRequestHandler — stdlib HTTP handler
    """

    def log_message(self, format, *args):
        pass

    def do_HEAD(self):
        """Handle HEAD requests — same as GET but suppress response body.

        Needed by K8s liveness probes, uptime monitors, and load balancers
        that use HEAD /health instead of GET /health.
        """
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        else:
            self.send_response(405)
            self.send_header("Allow", "GET, POST, OPTIONS")
            self.send_header("Content-Type", "application/json")
            self.end_headers()

    def do_OPTIONS(self):
        """Handle CORS preflight requests.

        Browser frontends send OPTIONS before POST /v1/messages. Without this,
        all browser-based clients are blocked by CORS.
        """
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, x-api-key, anthropic-version",
        )
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        # Security check: verify auth for non-localhost clients
        if not self._check_auth():
            self._send_json({"error": "Unauthorized — missing or invalid X-TokenPak-Key header"}, status=401)
            return
        
        # Fix #7: Per-IP rate limiting
        client_ip = self.client_address[0]
        if not _rate_limit_check(client_ip):
            self._send_json(
                {
                    "error": {
                        "type": "rate_limit_exceeded",
                        "message": f"Too many requests. Limit: {_RATE_LIMIT_RPM} req/min per IP.",
                    }
                },
                status=429,
            )
            return
        if self.path == "/config/reload":
            # Localhost-only hot config reload (same effect as SIGHUP)
            if client_ip not in ("127.0.0.1", "::1"):
                self._send_json(
                    {"error": {"type": "forbidden", "message": "Config reload only allowed from localhost"}},
                    status=403,
                )
                return
            msg = _reload_config_from_env()
            self._send_json({"status": "ok", "message": msg}, status=200)
            return
        elif self.path.startswith("http"):
            self._forward_request("POST")
        elif self.path.startswith("/ollama-proxy/"):
            self._ollama_proxy("POST")
        elif self.path.startswith("/v1/") or self.path.startswith("/v1beta/"):
            self._reverse_proxy("POST")
        elif self.path == "/ingest" or self.path == "/ingest/batch":
            self._ingest(self.path)
        else:
            # Fix #2: JSON 404 instead of HTML
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    def do_PUT(self):
        if self.path.startswith("http"):
            self._forward_request("PUT")
        else:
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    def do_DELETE(self):
        if self.path.startswith("http"):
            self._forward_request("DELETE")
        else:
            self._send_json(
                {"error": {"type": "not_found", "message": f"Unknown path: {self.path}"}},
                status=404,
            )

    def _forward_request(self, method):
        self._proxy_to(self.path, method)

    def _ollama_proxy(self, method):
        """Route /ollama-proxy/... to the real ollama server with compaction pipeline.

        Circuit breaker: if upstream was unreachable within the last 120s,
        return 503 immediately instead of hanging for minutes.
        Connect timeout: 20s (configurable via TOKENPAK_OLLAMA_TIMEOUT).
        """
        from urllib.parse import urlparse

        # Check circuit breaker -- fail fast if upstream recently unreachable
        with _ollama_circuit_lock:
            if _ollama_circuit["open"]:
                elapsed = time.time() - _ollama_circuit["last_failure"]
                if elapsed < _ollama_circuit["cooldown"]:
                    err_msg = f"Ollama upstream {OLLAMA_UPSTREAM} unreachable (circuit open, retry in {int(_ollama_circuit['cooldown'] - elapsed)}s)"
                    print(f"  \u26a1 {err_msg}")
                    try:
                        err = json.dumps(
                            {"error": {"type": "circuit_open", "message": err_msg}}
                        ).encode()
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", len(err))
                        self.end_headers()
                        self.wfile.write(err)
                    except Exception:
                        pass
                    return
                else:
                    _ollama_circuit["open"] = False
                    print("  \U0001f504 Ollama circuit breaker reset -- retrying upstream")

        # Probe upstream connectivity with short timeout before committing
        parsed = urlparse(OLLAMA_UPSTREAM)
        host = parsed.hostname
        port = parsed.port or 11434
        try:
            probe = socket.create_connection((host, port), timeout=OLLAMA_CONNECT_TIMEOUT)
            probe.close()
        except (socket.timeout, OSError, ConnectionRefusedError) as e:
            with _ollama_circuit_lock:
                _ollama_circuit["open"] = True
                _ollama_circuit["last_failure"] = time.time()
            err_msg = (
                f"Ollama upstream {host}:{port} unreachable after {OLLAMA_CONNECT_TIMEOUT}s: {e}"
            )
            print(f"  \u274c {err_msg}")
            SESSION["errors"] += 1
            try:
                err = json.dumps(
                    {"error": {"type": "upstream_unreachable", "message": err_msg}}
                ).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(err))
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass
            return

        # Upstream reachable -- forward normally
        real_path = self.path[len("/ollama-proxy") :]
        target = OLLAMA_UPSTREAM + real_path
        self._proxy_to(target, method, force_intercept=True)

    def _reverse_proxy(self, method):
        # Pre-flight: check for missing API credentials before touching upstream.
        # If the client sent no auth header AND the environment has no key set,
        # surface a clear auth_missing error immediately rather than forwarding a
        # bare request that will just fail with a cryptic 401 from the provider.
        _req_headers_lower = {k.lower(): v for k, v in self.headers.items()}
        _has_client_auth = bool(
            _req_headers_lower.get("x-api-key", "").strip()
            or _req_headers_lower.get("authorization", "").strip()
        )
        if not _has_client_auth:
            _env_key = (
                os.environ.get("ANTHROPIC_API_KEY", "").strip()
                or os.environ.get("OPENAI_API_KEY", "").strip()
                or os.environ.get("GOOGLE_API_KEY", "").strip()
                or os.environ.get("GEMINI_API_KEY", "").strip()
            )
            if not _env_key:
                self._send_json(
                    _make_structured_error(
                        "auth_missing",
                        "No API key provided and no key found in environment.",
                        "Set your API key via the x-api-key header or environment variable. "
                        "Example: export ANTHROPIC_API_KEY=<your-api-key>",
                    ),
                    status=401,
                )
                return

        headers = _header_mapping(self.headers)
        adapter = _detect_adapter(path=self.path, headers=headers, body_bytes=None)
        try:
            base = _resolve_upstream(adapter)
        except ValueError as exc:
            self._send_json(
                {
                    "error": {
                        "type": "upstream_route_missing",
                        "message": str(exc),
                    }
                },
                status=502,
            )
            return
        self._proxy_to(base + self.path, method, adapter=adapter)

    def _proxy_to(
        self, target_url, method, force_intercept=False, adapter: Optional[FormatAdapter] = None
    ):
        t0 = time.time()
        parsed = urlparse(target_url)
        content_length = int(self.headers.get("Content-Length", 0))
        # Body size cap — configurable via TOKENPAK_MAX_REQUEST_SIZE (default 10 MB)
        if content_length > _MAX_REQUEST_BYTES:
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "error": {
                            "type": "request_too_large",
                            "message": f"Request body exceeds limit ({content_length} bytes > {_MAX_REQUEST_BYTES} bytes). "
                            "Set TOKENPAK_MAX_REQUEST_SIZE to raise the limit.",
                        }
                    }
                ).encode()
            )
            return
        body = self.rfile.read(content_length) if content_length > 0 else None
        active_adapter = adapter
        if active_adapter is None and body is not None:
            active_adapter = _detect_adapter(self.path, _header_mapping(self.headers), body)

        if active_adapter is None:
            active_adapter = _detect_adapter(self.path, _header_mapping(self.headers), None)

        # X-TokenPak-Bypass: skip compression pipeline for this request
        _bypass_header_val = self.headers.get("x-tokenpak-bypass", "").strip().lower()
        _bypass_request: bool = _bypass_header_val in ("true", "1", "yes")

        should_log = (
            force_intercept
            or active_adapter.source_format != "passthrough"
            or any(h in target_url for h in INTERCEPT_HOSTS)
        )
        is_messages = True
        pipeline_enabled = active_adapter.source_format != "passthrough" and not _bypass_request

        model = "unknown"
        input_tokens = 0
        sent_input_tokens = 0
        protected_tokens = 0
        injected_tokens = 0
        injected_sources: List[str] = []
        is_streaming = False
        cache_read_tokens = 0
        cache_creation_tokens = 0
        cache_poison_scrubbed = False
        tools_schema_changed = False
        raw_request_body_for_cache_reason = body
        final_request_body_for_cache_reason = body
        router_meta: Optional[dict] = None

        # Pipeline trace
        trace: Optional[PipelineTrace] = None
        _wf_id = None  # proxy workflow tracking (TOKENPAK_WORKFLOW_TRACKING=1)
        if should_log and is_messages:
            trace = PipelineTrace(
                request_id=str(uuid.uuid4())[:8],
                timestamp=datetime.now().strftime("%H:%M:%S"),
            )
            # Start workflow tracking (no-op when feature flag is OFF)
            try:
                from tokenpak.agent.agentic.proxy_workflow import start_proxy_workflow

                _wf_id = start_proxy_workflow(
                    trace.request_id,
                    metadata={"path": self.path, "method": method},
                )
            except Exception:
                pass

        if _bypass_request and body:
            # Bypass mode: skip entire compression pipeline, pass through unmodified
            print(f"  ⏩ X-TokenPak-Bypass: passthrough (bypass header set)")

        if should_log and is_messages and body and not _bypass_request:
            # Fix #5: Strict validation mode — reject malformed requests early
            if STRICT_VALIDATION:
                try:
                    _val_data = json.loads(body)
                    _val_errors = []
                    if "messages" not in _val_data:
                        _val_errors.append("missing required field: messages")
                    if "model" not in _val_data:
                        _val_errors.append("missing required field: model")
                    msgs = _val_data.get("messages", [])
                    if not isinstance(msgs, list) or len(msgs) == 0:
                        _val_errors.append("messages must be a non-empty array")
                    if _val_errors:
                        _first_err = _val_errors[0]
                        # Extract first missing field name for the hint
                        _fld = None
                        if "messages" in _first_err:
                            _fld = "messages"
                        elif "model" in _first_err:
                            _fld = "model"
                        _val_hint = "Fix the request body before retrying. See: https://docs.anthropic.com/en/api/messages"
                        _val_payload: dict = {
                            "error": {
                                "type": "validation_error",
                                "message": "; ".join(_val_errors),
                                "hint": _val_hint,
                            }
                        }
                        if _fld:
                            _val_payload["error"]["field"] = _fld
                        self._send_json(_val_payload, status=400)
                        return
                except json.JSONDecodeError as _je:
                    self._send_json(
                        {
                            "error": {
                                "type": "invalid_json",
                                "message": str(_je),
                                "hint": "The request body must be valid JSON. Check for missing quotes, trailing commas, or unescaped characters.",
                            }
                        },
                        status=400,
                    )
                    return

            _original_body = body  # save for fallback
            _t0_compression = time.monotonic()  # compression pipeline start time

            def _compression_budget_exceeded() -> bool:
                """Return True if we've blown the MAX_COMPRESSION_TIME_MS budget."""
                if MAX_COMPRESSION_TIME_MS <= 0:
                    return False
                return (time.monotonic() - _t0_compression) * 1000 > MAX_COMPRESSION_TIME_MS

            try:
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                # PERF OPT: parse body JSON once here, reuse throughout pipeline
                req_data = None
                try:
                    req_data = json.loads(body)
                    is_streaming = req_data.get("stream", False)
                except Exception:
                    pass

                # Phase -3: Request Logger — generate request ID and start logging
                _request_log_id = None
                if REQUEST_LOGGER_ENABLED:
                    try:
                        from tokenpak.monitoring.request_logger import RequestLogger

                        _req_logger = RequestLogger.get_instance()
                        _request_log_id = _req_logger.new_request_id(
                            dict(self.headers) if self.headers else None
                        )
                        SESSION["request_logger_id"] = _request_log_id
                    except Exception as _rl_err:
                        SESSION["request_logger_error"] = str(_rl_err)
                        pass  # fail-open

                if pipeline_enabled:
                    # Phase -2: Semantic Cache — short-circuit duplicate/similar queries
                    if SEMANTIC_CACHE_ENABLED and body:
                        try:
                            _sem_cache = _get_sem_cache()
                            if _sem_cache is None:
                                raise ImportError("SemanticCache unavailable")
                            _sem_query = body.decode("utf-8") if isinstance(body, bytes) else body
                            _cache_result = _sem_cache.lookup(_sem_query)
                            if (
                                _cache_result is not None
                                and _cache_result.hit
                                and _cache_result.entry
                            ):
                                SESSION["semantic_cache_hit"] = True
                                SESSION["phase_semantic_cache"] = "hit"
                                # Return cached response — skip all processing
                                _cached_resp = _cache_result.entry.response
                                if isinstance(_cached_resp, dict):
                                    self._send_json(_cached_resp)
                                elif isinstance(_cached_resp, bytes):
                                    self.wfile.write(_cached_resp)
                                else:
                                    self._send_json(json.loads(_cached_resp))
                                return
                            SESSION["phase_semantic_cache"] = "miss"
                        except Exception as _sc_err:
                            SESSION["phase_semantic_cache"] = f"error:{_sc_err}"
                            pass  # fail-open: never break a request over semantic cache

                    # Phase -1: Tool Schema Registry — normalize tools to byte-identical JSON
                    # Enables Anthropic cache hits on repeated tool schemas
                    if TOOL_REGISTRY_AVAILABLE and body:
                        try:
                            _tool_reg = _get_tool_registry()
                            if _tool_reg:
                                body, _tools_changed = _tool_reg.normalize_request(body)
                                tools_schema_changed = bool(_tools_changed)
                                _tstats = _tool_reg.stats()
                                SESSION["tool_schema_frozen_tools"] = _tstats.get("frozen_tools", 0)
                                SESSION["tool_schema_bytes_saved"] = _tool_reg.bytes_saved
                        except Exception as _treg_err:
                            pass  # fail-open: never break a request over tool registry

                    # Phase 0: Manual routing rules — rewrite model before any processing
                    # PERF OPT: use singleton RouteEngine + cached rules + reuse req_data
                    try:
                        from tokenpak.routing.rules import (
                            _count_tokens_approx,
                            _extract_prompt_text,
                        )

                        _route_engine = _get_route_engine()
                        if _route_engine is not None:
                            # Reuse already-parsed req_data if available, else fallback
                            _route_payload = (
                                req_data
                                if req_data is not None
                                else (json.loads(body) if body else {})
                            )
                            _route_prompt = _extract_prompt_text(_route_payload)
                            _route_tokens = _count_tokens_approx(_route_prompt)
                            _cached_rules = _get_cached_route_rules()
                            _matched_rule = _route_engine.match(
                                model=model,
                                prompt=_route_prompt,
                                token_count=_route_tokens,
                                rules=_cached_rules,
                            )
                            if _matched_rule:
                                _route_payload = dict(_route_payload)  # copy before mutate
                                _route_payload["model"] = _matched_rule.target
                                body = json.dumps(_route_payload).encode()
                                req_data = _route_payload  # keep req_data in sync
                                model = _matched_rule.target
                                print(
                                    f"  🔀 Route rule [{_matched_rule.id}]: → {_matched_rule.target}"
                                )
                    except Exception as _route_err:
                        print(f"  ⚠️ Routing rule error (skipping): {_route_err}")

                    # Phase 0.1: Precondition Gates — reject requests likely to fail
                    # PERF OPT: use singleton PreconditionGates (avoids per-request import + init)
                    if PRECONDITION_GATES_ENABLED and body:
                        try:
                            _pg = _get_precond_gates()
                            if _pg is not None:
                                _pg_pass, _pg_reason = _pg.check(model)
                                SESSION["precondition_gates_pass"] = _pg_pass
                                if not _pg_pass:
                                    SESSION["precondition_gates_blocked"] = _pg_reason
                                    self._send_json(
                                        {
                                            "error": {
                                                "type": "precondition_failed",
                                                "message": f"Request blocked by precondition gate: {_pg_reason}",
                                            }
                                        },
                                        status=422,
                                    )
                                    return
                        except Exception as _pg_err:
                            SESSION["precondition_gates_error"] = str(_pg_err)
                            pass  # fail-open

                    # Phase 0.2: Budget Controller — enforce token budget limits before processing
                    # PERF OPT: use singleton BudgetController (avoids per-request import + init)
                    if BUDGET_CONTROLLER_ENABLED and body:
                        try:
                            from tokenpak.budget_controller import ClassificationResult, IntentClass

                            _bc = _get_budget_controller()
                            _bc_tokens = input_tokens or 0
                            _bc_class = ClassificationResult(
                                intent=IntentClass.GEN_Q,
                                complexity_score=min(_bc_tokens / 10000.0, 1.0),
                            )
                            _bc_decision = _bc.decide(_bc_class)
                            SESSION["budget_controller_tier"] = str(_bc_class.intent.name)
                            SESSION["budget_controller_action"] = (
                                _bc_decision.action
                                if hasattr(_bc_decision, "action")
                                else str(_bc_decision)
                            )
                            if hasattr(_bc_decision, "reject") and _bc_decision.reject:
                                self._send_json(
                                    {
                                        "error": {
                                            "type": "budget_exceeded",
                                            "message": f"Request exceeds token budget: {_bc_tokens} tokens",
                                        }
                                    },
                                    status=429,
                                )
                                return
                        except Exception as _bc_err:
                            SESSION["budget_controller_error"] = str(_bc_err)
                            pass  # fail-open

                    # Phase 0.3: DeterministicRouter — intent classification + compression pipeline
                    _intent_for_contract: str = "query"
                    if ROUTER_ENABLED:
                        try:
                            _session_id_router = self.headers.get("X-OpenClaw-Session", model)
                            body, _router_meta = _run_router(body, session_id=_session_id_router)
                            router_meta = _router_meta
                            if _router_meta and not _router_meta.get("fallback"):
                                _intent_for_contract = _router_meta.get("intent", "query")
                                print(
                                    f"  🔀 Router: intent={_router_meta.get('intent','?')} recipe={_router_meta.get('recipe_used','?')} ({_router_meta.get('total_ms',0)}ms)"
                                )
                        except Exception as _router_err:
                            print(f"  ⚠️ Router stage error (skipping): {_router_err}")

                    # Phase 0.4: Context contract enforcement — quota + scope + omission
                    try:
                        from tokenpak.proxy.intent_policy import (
                            resolve_policy as _resolve_policy,
                        )

                        _contract_policy = _resolve_policy(_intent_for_contract, {}, 1.0)
                        _, _pre_contract_tokens = extract_request_tokens(
                            body, adapter=active_adapter
                        )
                        if _pre_contract_tokens > _contract_policy.context_quota:
                            # Soft-cap: log quota violation; hard truncation handled by compaction
                            print(
                                f"  📋 Contract: intent={_intent_for_contract} quota={_contract_policy.context_quota} tokens={_pre_contract_tokens} ceiling={_contract_policy.reasoning_ceiling}"
                            )
                    except Exception as _contract_err:
                        pass  # fail-open: contract enforcement is advisory

                    # Phase 0.5: Capsule builder — compress historical context blocks
                    if CAPSULE_BUILDER is not None and ENABLE_CAPSULE_BUILDER:
                        t_capsule = time.time()
                        capsule_stage = StageTrace(
                            name="capsule",
                            enabled=True,
                            input_tokens=input_tokens,
                        )
                        try:
                            body, _cap_stats = CAPSULE_BUILDER.process(body)
                            _cap_blocks = _cap_stats.get("blocks_capsulized", 0)
                            _cap_ratio = _cap_stats.get("ratio", 1.0)
                            _cap_chars_in = _cap_stats.get("chars_in", 0)
                            _cap_chars_out = _cap_stats.get("chars_out", 0)
                            capsule_stage.details["blocks_capsulized"] = _cap_blocks
                            capsule_stage.details["compression_ratio"] = _cap_ratio
                            capsule_stage.details["chars_in"] = _cap_chars_in
                            capsule_stage.details["chars_out"] = _cap_chars_out
                            capsule_stage.details["skip_reason"] = _cap_stats.get("skip_reason")
                            if _cap_blocks > 0:
                                # Recount tokens after capsulisation
                                _, input_tokens = extract_request_tokens(
                                    body, adapter=active_adapter
                                )
                                print(
                                    f"  💊 Capsule: {_cap_blocks} block(s) compressed "
                                    f"({_cap_chars_in}→{_cap_chars_out} chars, ratio={_cap_ratio})"
                                )
                            capsule_stage.output_tokens = input_tokens
                            capsule_stage.tokens_delta = (
                                capsule_stage.output_tokens - capsule_stage.input_tokens
                            )
                        except Exception as _cap_err:
                            print(f"  ⚠️  Capsule builder error (skipping): {_cap_err}")
                            capsule_stage.details["error"] = str(_cap_err)
                            capsule_stage.output_tokens = input_tokens
                        capsule_stage.duration_ms = (time.time() - t_capsule) * 1000
                        if trace:
                            trace.stages.append(capsule_stage)

                    # Phase 0.6: Prefix Registry — track stable system message prefixes
                    if PREFIX_REGISTRY_ENABLED and body:
                        try:
                            from tokenpak.cache.prefix_registry import StablePrefixRegistry

                            _prefix_reg = StablePrefixRegistry()
                            # PERF OPT: reuse req_data parsed earlier instead of re-parsing body
                            _prefix_body = req_data if req_data is not None else json.loads(body)
                            _sys_msgs = [
                                m
                                for m in _prefix_body.get("messages", [])
                                if m.get("role") == "system"
                            ]
                            if _sys_msgs:
                                _prefix_text = _sys_msgs[0].get("content", "")[
                                    :200
                                ]  # first 200 chars
                                _prefix_hash = hash(_prefix_text)
                                _prefix_meta = _prefix_reg.get_or_create(_prefix_hash, _prefix_text)
                                SESSION["prefix_registry_registered"] = True
                                SESSION["prefix_registry_hash"] = _prefix_hash
                        except Exception as _pr_err:
                            SESSION["prefix_registry_error"] = str(_pr_err)
                            pass  # fail-open

                    # Phase 0.9: Cache Poison Removal — strip dynamic UUIDs, timestamps, heartbeat counters
                    # Must run BEFORE stable cache control so the stable prefix stays bit-identical
                    if body:
                        _pre_poison_body = body
                        body = _strip_cache_poisons(body)
                        cache_poison_scrubbed = body != _pre_poison_body

                    # Compression budget check — if capsule took too long, skip remaining pipeline
                    if _compression_budget_exceeded():
                        print(
                            f"  ⏱️  Compression budget exceeded ({MAX_COMPRESSION_TIME_MS}ms) after capsule stage — "
                            f"skipping vault+compaction, forwarding original body"
                        )
                        SESSION["compression_timeouts"] += 1
                        body = _original_body
                        raise _CompressionTimeout()

                    # Phase 1: Vault context injection (BEFORE compaction)
                    t_inject = time.time()
                    # Vault index reload is handled by _vault_index_reload_timer (background timer)
                    # No per-request thread spawn needed
                    vault_stage = StageTrace(
                        name="vault_injection",
                        enabled=VAULT_INDEX.available,
                        input_tokens=input_tokens,
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
                            from tokenpak.agent.regression.retrieval_watchdog import (
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
                            from tokenpak.agent.compression.salience.router import (
                                detect_content_type,
                            )
                            from tokenpak.agent.compression.salience.router import (
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
                            from tokenpak.agent.compression.query_rewriter import QueryRewriter

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
                            from tokenpak.agent.compression.fidelity_tiers import (
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
                            from tokenpak.agent.compression.dictionary import CompressionDictionary

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
                            from tokenpak.agent.agentic.proxy_workflow import advance_step

                            advance_step(_wf_id, "vault_inject", "compress")
                            advance_step(_wf_id, "compress", "forward")
                        except Exception:
                            pass
                else:
                    sent_input_tokens = input_tokens
            except _CompressionTimeout:
                # Budget exceeded — body already set to best available state; just re-sync tokens
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                sent_input_tokens = input_tokens
            except Exception as _pipeline_err:
                print(f"  ⚠️ Pre-pipeline error (falling back to original body): {_pipeline_err}")
                body = _original_body  # restore original body so request still forwards
                model, input_tokens = extract_request_tokens(body, adapter=active_adapter)
                sent_input_tokens = input_tokens

        final_request_body_for_cache_reason = body

        # Final validation gate (pre-forward): budget, deterministic context, fingerprint, dry-run
        if should_log and is_messages and body and active_adapter.source_format != "passthrough":
            gate = _get_validation_gate()
            if gate is not None:
                try:
                    gate_result = gate.validate_request(
                        request_body=body,
                        model=model,
                        input_tokens=sent_input_tokens or input_tokens,
                        router_meta=router_meta or {},
                    )
                    if gate_result.fingerprint:
                        print(f"  🧾 Determinism fingerprint: {gate_result.fingerprint}")
                    if not gate_result.valid:
                        if VALIDATION_GATE_SOFT:
                            # Soft mode: log warning but forward request anyway
                            print(
                                f"  ⚠️ Validation gate SOFT-BLOCK (forwarding): {gate_result.errors}"
                            )
                            SESSION["validation_gate_soft_block"] = gate_result.errors
                        else:
                            self._send_json(
                                {
                                    "error": {
                                        "type": "validation_gate_failed",
                                        "message": "Request blocked by validation gate",
                                        "reasons": gate_result.errors,
                                    },
                                    "warnings": gate_result.warnings,
                                    "fingerprint": gate_result.fingerprint,
                                },
                                status=422,
                            )
                            return
                    if gate_result.dry_run:
                        self._send_json(
                            {
                                "status": "dry_run",
                                "message": "Validation gate accepted request; upstream forward skipped",
                                "plan": gate_result.plan,
                                "fingerprint": gate_result.fingerprint,
                                "warnings": gate_result.warnings,
                            },
                            status=200,
                        )
                        return
                except Exception as _gate_err:
                    print(f"  ⚠️ Validation gate error (fail-open): {_gate_err}")

        fwd_headers = _sanitize_headers(self.headers)
        fwd_headers["Host"] = parsed.netloc
        if sent_input_tokens == 0:
            sent_input_tokens = input_tokens
        if body is not None:
            fwd_headers["Content-Length"] = str(len(body))

        _req_headers_lower = {k.lower(): v for k, v in self.headers.items()}
        _client_has_auth = bool(
            _req_headers_lower.get("x-api-key", "").strip()
            or _req_headers_lower.get("authorization", "").strip()
        )
        _current_key_idx: int = -1  # tracks which key is injected (for failover)
        if not _client_has_auth and _ANTHROPIC_KEY_POOL and "anthropic.com" in target_url:
            _pool_key, _current_key_idx = _get_next_key()
            if _pool_key:
                fwd_headers["x-api-key"] = _pool_key

        # Fix #5: Check per-provider circuit breaker before attempting upstream
        _cb_provider = _provider_for_url(target_url)
        if _circuit_check(_cb_provider):
            self._send_json(
                {
                    "error": {
                        "type": "circuit_open",
                        "message": f"Provider {_cb_provider} circuit is open — too many recent failures. Retry in 60s.",
                    }
                },
                status=503,
            )
            return

        try:
            path = parsed.path
            if parsed.query:
                path += "?" + parsed.query
            # DEBUG: count cache_control blocks before cap
            try:
                _dbg_body = (
                    json.loads(body)
                    if isinstance(body, bytes)
                    else json.loads(body.encode() if isinstance(body, str) else body)
                )
                _cc_locs = []
                for _si, _sb in enumerate(_dbg_body.get("system", [])):
                    if isinstance(_sb, dict) and "cache_control" in _sb:
                        _cc_locs.append(f"system[{_si}]")
                for _mi, _mm in enumerate(_dbg_body.get("messages", [])):
                    _mc = _mm.get("content", [])
                    if isinstance(_mc, list):
                        for _ci, _cb in enumerate(_mc):
                            if isinstance(_cb, dict) and "cache_control" in _cb:
                                _cc_locs.append(f"msg[{_mi}].content[{_ci}]")
                if _cc_locs:
                    print(f"  🔍 cache_control blocks BEFORE cap: {len(_cc_locs)} at {_cc_locs}")
            except Exception as _e:
                print(f"  🔍 debug error: {_e}")
            body = _strip_empty_text_blocks(body)
            body = _cap_cache_control_blocks(body)
            # Fix Content-Length after cache_control cap may have changed body size
            if isinstance(body, str):
                body = body.encode("utf-8")
            if body is not None:
                fwd_headers["Content-Length"] = str(len(body))
            # DEBUG: count cache_control blocks
            try:
                _dbody = json.loads(body) if isinstance(body, (bytes, str)) else body
                _cc = 0
                for _s in _dbody.get("system") or []:
                    if isinstance(_s, dict) and "cache_control" in _s:
                        _cc += 1
                for _m in _dbody.get("messages") or []:
                    for _c in (
                        (_m.get("content") or []) if isinstance(_m.get("content"), list) else []
                    ):
                        if isinstance(_c, dict) and "cache_control" in _c:
                            _cc += 1
                if _cc > 0:
                    print(f"  📦 cache_control blocks in request: {_cc}", flush=True)
                if _cc > 4:
                    print(
                        f"  ⚠️ OVER LIMIT! Stripping {_cc - 4} earliest cache_control blocks",
                        flush=True,
                    )
                    _locs = []
                    for _i, _s in enumerate((_dbody.get("system") or [])):
                        if isinstance(_s, dict) and "cache_control" in _s:
                            _locs.append(("s", _i))
                    for _mi, _m in enumerate((_dbody.get("messages") or [])):
                        for _ci, _c in enumerate(
                            (_m.get("content") or []) if isinstance(_m.get("content"), list) else []
                        ):
                            if isinstance(_c, dict) and "cache_control" in _c:
                                _locs.append(("m", _mi, _ci))
                    for _loc in _locs[: (_cc - 4)]:
                        if _loc[0] == "s":
                            _dbody["system"][_loc[1]].pop("cache_control", None)
                        else:
                            _dbody["messages"][_loc[1]]["content"][_loc[2]].pop(
                                "cache_control", None
                            )
                    body = json.dumps(_dbody).encode()
                    print(
                        f'  ✅ Stripped. Now {sum(1 for s in (_dbody.get("system") or []) if isinstance(s,dict) and "cache_control" in s) + sum(1 for m in (_dbody.get("messages") or []) for c in (m.get("content") or []) if isinstance(c,dict) and "cache_control" in c)} blocks',
                        flush=True,
                    )
            except Exception as _e:
                print(f"  ⚠️ cache_control debug error: {_e}", flush=True)
            body = _strip_empty_text_blocks(body)
            body = _cap_cache_control_blocks(body)
            if isinstance(body, str):
                body = body.encode("utf-8")
            if body is not None:
                fwd_headers["Content-Length"] = str(len(body))
            # TEMP DEBUG: dump final body to file
            try:
                import json as _j2

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
                            from tokenpak.agent.agentic.error_normalizer import ErrorNormalizer

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
                            from tokenpak.agent.agentic.failure_memory import (
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
                chunk_count = 0
                early_break = False
                _pending_chunk = b""
                _footer_injected = False
                import zlib as _zlib

                _ce = resp.getheader("Content-Encoding", "")
                _decomp = _zlib.decompressobj(_zlib.MAX_WBITS | 16) if "gzip" in _ce else None
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        # Flush any pending chunk at end of stream
                        if _pending_chunk:
                            try:
                                self.wfile.write(_pending_chunk)
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                            if should_log and is_messages:
                                sse_buffer += _pending_chunk
                        break
                    chunk_count += 1
                    if _decomp:
                        try:
                            chunk = _decomp.decompress(chunk)
                        except Exception:
                            pass
                    if not chunk:
                        continue

                    # Chat footer injection — buffer chunks to find message_stop
                    if CHAT_FOOTER_ENABLED and not _footer_injected and should_log and is_messages:
                        combined = _pending_chunk + chunk
                        _pending_chunk = b""
                        if (
                            b'"type":"message_stop"' in combined
                            or b'"type": "message_stop"' in combined
                        ):
                            try:
                                # Find injection point — right before message_stop event
                                stop_idx = combined.find(b"event: message_stop")
                                if stop_idx == -1:
                                    # Inline format — find the event: line before type:message_stop
                                    ms_idx = combined.find(b'"type":"message_stop"')
                                    if ms_idx == -1:
                                        ms_idx = combined.find(b'"type": "message_stop"')
                                    if ms_idx > 0:
                                        search_back = combined[:ms_idx].rfind(b"event:")
                                        stop_idx = search_back if search_back >= 0 else -1

                                if stop_idx > 0:
                                    before_stop = combined[:stop_idx]
                                    after_stop = combined[stop_idx:]
                                    self.wfile.write(before_stop)
                                    self.wfile.flush()
                                    sse_buffer += before_stop

                                    # Build footer stats
                                    _temp_usage = _extract_sse_tokens(sse_buffer)
                                    _temp_output = _temp_usage.get("output_tokens", 0)
                                    _temp_cache_r = _temp_usage.get("cache_read_input_tokens", 0)
                                    _saved = max(0, input_tokens - sent_input_tokens)
                                    _pct = (
                                        int(100 * _saved / input_tokens) if input_tokens > 0 else 0
                                    )
                                    _cost = estimate_cost(
                                        model, sent_input_tokens, _temp_output, _temp_cache_r, 0
                                    )
                                    _footer_text = f"\n\n───\n📊 {input_tokens:,}→{sent_input_tokens:,} tok (-{_pct}%) | ${_cost:.3f}"
                                    if _temp_cache_r > 0:
                                        _footer_text += f" | cache: {_temp_cache_r:,}r"
                                    _footer_event = {
                                        "type": "content_block_delta",
                                        "index": 0,
                                        "delta": {"type": "text_delta", "text": _footer_text},
                                    }
                                    _footer_sse = f"event: content_block_delta\ndata: {json.dumps(_footer_event)}\n\n".encode()
                                    self.wfile.write(_footer_sse)
                                    self.wfile.flush()
                                    _footer_injected = True

                                    self.wfile.write(after_stop)
                                    self.wfile.flush()
                                    sse_buffer += after_stop
                                    continue
                                else:
                                    # Couldn't find injection point — write combined as-is
                                    self.wfile.write(combined)
                                    self.wfile.flush()
                                    sse_buffer += combined
                                    _footer_injected = True
                                    continue
                            except Exception:
                                # Fail-open — write the chunk normally
                                self.wfile.write(combined)
                                self.wfile.flush()
                                sse_buffer += combined
                                _footer_injected = True
                                continue
                        else:
                            # Buffer one chunk ahead to catch message_stop split across chunks
                            if _pending_chunk:
                                try:
                                    self.wfile.write(_pending_chunk)
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    early_break = True
                                    break
                                if should_log and is_messages:
                                    sse_buffer += _pending_chunk
                            _pending_chunk = combined
                            continue

                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        early_break = True
                        break
                    if should_log and is_messages:
                        sse_buffer += chunk
                if should_log and is_messages:
                    sse_usage = _extract_sse_tokens(sse_buffer)
                    output_tokens = extract_response_tokens(
                        sse_buffer, adapter=active_adapter, is_sse=True
                    )
                    cache_read_tokens = sse_usage.get("cache_read_input_tokens", 0)
                    cache_creation_tokens = sse_usage.get("cache_creation_input_tokens", 0)
            else:
                resp_body = resp.read()
                output_tokens = 0
                # Chat footer — JSON (non-streaming) injection
                if CHAT_FOOTER_ENABLED and should_log and is_messages and status == 200:
                    try:
                        body_for_parse = resp_body
                        if "gzip" in resp.getheader("Content-Encoding", ""):
                            body_for_parse = gzip.decompress(resp_body)
                        resp_json = json.loads(body_for_parse)
                        usage = resp_json.get("usage", {})
                        _out_tok = usage.get("output_tokens", 0)
                        _cache_r = usage.get("cache_read_input_tokens", 0)
                        _pct = (
                            round((input_tokens - sent_input_tokens) / input_tokens * 100, 1)
                            if input_tokens
                            else 0
                        )
                        _cost = estimate_cost(model, sent_input_tokens, _out_tok, _cache_r, 0)
                        _footer_text = f"\n\n───\n📊 {input_tokens:,}→{sent_input_tokens:,} tok (-{_pct}%) | ${_cost:.3f}"
                        if _cache_r > 0:
                            _footer_text += f" | cache: {_cache_r:,}r"
                        content = resp_json.get("content", [])
                        if content and isinstance(content, list):
                            for i in range(len(content) - 1, -1, -1):
                                if content[i].get("type") == "text":
                                    content[i]["text"] += _footer_text
                                    break
                            resp_json["content"] = content
                            resp_body = json.dumps(resp_json).encode()
                    except Exception:
                        pass  # fail-open

                # Phase 2.2: Session Capsules — compress and store session context
                if SESSION_CAPSULES_ENABLED and body:
                    try:
                        from tokenpak.agent.memory.session_capsules import (
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

                self.wfile.write(resp_body)
                self.wfile.flush()
                if should_log and is_messages:
                    resp_for_metrics = resp_body
                    if "gzip" in resp.getheader("Content-Encoding", ""):
                        try:
                            resp_for_metrics = gzip.decompress(resp_body)
                        except Exception:
                            pass
                    output_tokens = extract_response_tokens(
                        resp_for_metrics, adapter=active_adapter
                    )
                    try:
                        usage = json.loads(resp_for_metrics).get("usage", {})
                        cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
                    except Exception:
                        pass

            # conn.close()  # REMOVED: urllib3 pool manager, no conn object here

            # Post-request: Store successful response in semantic cache
            if SEMANTIC_CACHE_ENABLED and status == 200 and not SESSION.get("semantic_cache_hit"):
                try:
                    _sem_cache = _get_sem_cache()
                    if _sem_cache is None:
                        raise ImportError("SemanticCache unavailable")
                    _store_query = (
                        _original_body.decode("utf-8")
                        if isinstance(_original_body, bytes)
                        else _original_body
                    )
                    _store_resp_raw = (
                        resp_body
                        if "resp_body" in locals()
                        else json.dumps({"status": status}).encode()
                    )
                    _store_resp_dict = (
                        json.loads(_store_resp_raw)
                        if isinstance(_store_resp_raw, (bytes, str))
                        else _store_resp_raw
                    )
                    _sem_cache.store(_store_query, _store_resp_dict)
                    SESSION["semantic_cache_stored"] = True
                except Exception as _sc_store_err:
                    SESSION["semantic_cache_store_error"] = str(_sc_store_err)
                    pass  # fail-open

            latency_ms = int((time.time() - t0) * 1000)

            # Post-request: Stability Scorer — track response consistency over time
            if STABILITY_SCORER_ENABLED:
                try:
                    from tokenpak.agent.regression.stability_scorer import (
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
                cost = estimate_cost(
                    model,
                    sent_input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_creation_tokens,
                )
                saved = max(0, input_tokens - sent_input_tokens)
                # Estimate cost saved (what it would have cost without compression)
                cost_without_compression = estimate_cost(
                    model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
                )
                cost_saved = max(0.0, cost_without_compression - cost)
                sources_str = ",".join(injected_sources) if injected_sources else ""
                _log_compilation_mode = "bypass" if _bypass_request else COMPILATION_MODE
                try:
                    MONITOR.log(
                        model,
                        sent_input_tokens,
                        output_tokens,
                        cost,
                        latency_ms,
                        status,
                        target_url,
                        _log_compilation_mode,
                        protected_tokens,
                        saved,
                        injected_tokens,
                        sources_str,
                        cache_read_tokens,
                        cache_creation_tokens,
                    )
                except Exception as _monitor_err:
                    print(
                        f"  ⚠️ Monitor.log() failed (SQLite error, request unaffected): {_monitor_err}"
                    )
                try:
                    from tokenpak.telemetry.anon_metrics import record_request

                    record_request(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        tokens_saved=saved,
                        latency_ms=latency_ms,
                        model=model,
                    )
                except Exception:
                    pass  # never break the proxy
                # Record request latency for p50/p99 tracking
                _req_elapsed_ms = (time.time() - t0) * 1000
                with _latency_lock:
                    _request_latencies.append(_req_elapsed_ms)

                SESSION["requests"] += 1
                SESSION["input_tokens"] += input_tokens
                SESSION["sent_input_tokens"] += sent_input_tokens
                SESSION["saved_tokens"] += saved
                SESSION["protected_tokens"] += protected_tokens
                SESSION["output_tokens"] += output_tokens
                SESSION["cost"] += cost
                SESSION["cost_saved"] += cost_saved
                SESSION["injected_tokens"] += injected_tokens
                SESSION["cache_read_tokens"] += cache_read_tokens
                SESSION["cache_creation_tokens"] += cache_creation_tokens
                if cache_read_tokens > 0:
                    SESSION["cache_hits"] += 1
                else:
                    SESSION["cache_misses"] += 1
                    miss_reason = _classify_cache_miss_reason(
                        raw_request_body_for_cache_reason,
                        cache_poison_scrubbed=cache_poison_scrubbed,
                        tools_schema_changed=tools_schema_changed,
                        final_body=final_request_body_for_cache_reason,
                    )
                    miss_map = SESSION.setdefault("cache_miss_reasons", {})
                    miss_map[miss_reason] = int(miss_map.get(miss_reason, 0) or 0) + 1
                if injected_tokens > 0:
                    SESSION["injection_hits"] += 1

                # Complete and store pipeline trace
                if trace:
                    trace.model = model
                    trace.input_tokens = input_tokens
                    trace.output_tokens = output_tokens
                    trace.tokens_saved = saved
                    trace.cost_saved = cost_saved
                    trace.total_cost = cost
                    trace.duration_ms = latency_ms
                    trace.status = "complete"
                    TRACE_STORAGE.store(trace)

                # Workflow tracking: mark forward done → log_metrics → complete
                if _wf_id:
                    try:
                        from tokenpak.agent.agentic.proxy_workflow import (
                            advance_step,
                            complete_workflow,
                        )

                        advance_step(_wf_id, "forward", "log_metrics")
                        complete_workflow(_wf_id)
                    except Exception:
                        pass

                # Update last request stats for /stats/last endpoint
                request_id = trace.request_id if trace else str(uuid.uuid4())[:8]
                update_last_request(
                    request_id=request_id,
                    model=model,
                    input_raw=input_tokens,
                    input_sent=sent_input_tokens,
                    tokens_saved=saved,
                    cost_saved=cost_saved,
                    output_tokens=output_tokens,
                )

                stream_tag = " [SSE]" if is_sse else ""
                mode_tag = f" [{COMPILATION_MODE}]"
                inject_tag = f" [+{injected_tokens} vault]" if injected_tokens > 0 else ""
                # Cache status tag: show FRESH/CACHED with token counts for clarity
                if cache_read_tokens > 0:
                    _saved_k = f"{cache_read_tokens:,}"
                    cache_tag = f" (CACHED: {_saved_k} tokens)"
                elif cache_creation_tokens > 0:
                    _written_k = f"{cache_creation_tokens:,}"
                    cache_tag = f" (FRESH: {_written_k} written)"
                else:
                    cache_tag = " (FRESH)"
                print(
                    f"  📊 {model}{stream_tag}{mode_tag}{inject_tag}: {input_tokens:,} in → {sent_input_tokens:,} sent "
                    f"(saved {saved:,}, protected {protected_tokens:,}) / {output_tokens:,} out | "
                    f"~${cost:.4f}{cache_tag} | {latency_ms}ms"
                )

        except Exception as e:
            SESSION["errors"] += 1
            latency_ms = int((time.time() - t0) * 1000)
            import traceback as _tb

            _tb.print_exc(file=__import__("sys").stderr)
            print(f"  ❌ Proxy error: {type(e).__name__}: {e} | {latency_ms}ms")
            # Workflow tracking: mark the in-progress step as failed (not whole workflow)
            if _wf_id:
                try:
                    from tokenpak.agent.agentic.proxy_workflow import fail_step as _wf_fail

                    _wf_fail(_wf_id, "forward", error=f"{type(e).__name__}: {e}")
                except Exception:
                    pass
            try:
                err = json.dumps({"error": {"type": "proxy_error", "message": str(e)}}).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", len(err))
                self.end_headers()
                self.wfile.write(err)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Globals from runtime/proxy.py used by ForwardProxyHandler.
# Imported AFTER class definition to break circular import:
# runtime/proxy.py → proxy/server.py (class defined) → runtime/proxy.py (already in sys.modules)
# ---------------------------------------------------------------------------
from tokenpak.runtime.proxy import (  # noqa: E402,F401
    SESSION,
    MONITOR,
    VAULT_INDEX,
    CAPSULE_BUILDER,
    LAST_REQUEST,
    _LAST_REQUEST_LOCK,
    TERM_RESOLVER,
    CANON_AVAILABLE,
    TOOL_REGISTRY_AVAILABLE,
    _request_latencies,
    _latency_lock,
    estimate_cost,
    compact_request_body,
    inject_vault_context,
    extract_request_tokens,
    extract_response_tokens,
    _build_cache_stats_payload,
    _ingest_write_entry,
    update_last_request,
    apply_canon_refs,
    _get_tool_registry,
    MODEL_COSTS,
    _detect_adapter,
    _header_mapping,
    count_tokens,
)
