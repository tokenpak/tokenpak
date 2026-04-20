"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.server``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.server is a deprecated re-export; "
    "import from tokenpak.proxy.server instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.server import *  # noqa: F401,F403,E402

__all__ = ["Any", "BaseHTTPRequestHandler", "CacheMetrics", "Callable", "CompressionStats", "ConnectionPool", "DegradationEventType", "Dict", "ExportAPI", "FilterParams", "Generator", "GracefulShutdown", "HTTPServer", "INTERCEPT_HOSTS", "List", "Optional", "PassthroughConfig", "PipelineTrace", "PoolConfig", "ProviderRouter", "ProxyServer", "RequestStats", "SessionFilter", "StageTrace", "TraceStorage", "asdict", "auto_detect_upstream", "contextmanager", "dataclass", "datetime", "deque", "detect_platform", "estimate_cost", "extract_sse_tokens", "field", "format_startup_report", "forward_headers", "get_circuit_breaker_registry", "get_degradation_tracker", "get_stats_footer_enabled", "log_request", "provider_from_url", "render_footer_oneline", "run_startup_checks", "start_proxy", "timezone", "urlparse", "validate_auth"]
