"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.server_async``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.server_async is a deprecated re-export; "
    "import from tokenpak.proxy.server_async instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.server_async import *  # noqa: F401,F403,E402

__all__ = ["Any", "BaseHTTPMiddleware", "ConcurrencyLimiterMiddleware", "Dict", "HTTPX_POOL_SIZE", "HTTPX_TIMEOUT", "INTERCEPT_HOSTS", "JSONResponse", "MAX_CONCURRENCY", "Optional", "PROXY_PORT", "Request", "Response", "Route", "Starlette", "StreamingResponse", "asynccontextmanager", "create_async_app", "datetime", "handle_circuit_breakers", "handle_degradation", "handle_export_csv", "handle_health", "handle_not_found", "handle_proxy", "handle_sessions", "handle_stats", "handle_stats_last", "handle_stats_session", "handle_trace_by_id", "handle_trace_last", "handle_traces", "handle_v1_proxy", "lifespan", "run_async_proxy", "start_async_proxy_in_thread"]
