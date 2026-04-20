"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.circuit_breaker``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.circuit_breaker is a deprecated re-export; "
    "import from tokenpak.proxy.circuit_breaker instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.circuit_breaker import *  # noqa: F401,F403,E402

__all__ = ["Any", "CircuitBreaker", "CircuitBreakerConfig", "CircuitBreakerRegistry", "CircuitState", "Dict", "Enum", "List", "Optional", "dataclass", "deque", "get_circuit_breaker_registry", "provider_from_url"]
