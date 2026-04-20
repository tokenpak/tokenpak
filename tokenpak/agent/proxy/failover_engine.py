"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.failover_engine``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.failover_engine is a deprecated re-export; "
    "import from tokenpak.proxy.failover_engine instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.failover_engine import *  # noqa: F401,F403,E402

__all__ = ["Any", "CIRCUIT_COOL_DOWN_SECONDS", "CIRCUIT_FAILURE_THRESHOLD", "CircuitBreaker", "CircuitState", "ClassifiedError", "Dict", "ErrorType", "FailoverConfig", "FailoverDecision", "FailoverEngine", "FailoverEvent", "FailoverEventLog", "FailoverManager", "Iterator", "List", "MAX_RETRY_SAME_PROVIDER", "Optional", "ProviderAttempt", "RATE_LIMIT_WAIT_SECONDS", "Tuple", "classify_error", "dataclass", "datetime", "decide", "field", "get_event_log", "load_failover_config", "logger", "normalize_response", "normalize_stream", "render_failover_footer", "timezone"]
