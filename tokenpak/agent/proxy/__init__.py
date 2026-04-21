"""Deprecated re-export shim for ``tokenpak.agent.proxy``.

The canonical home is ``tokenpak.proxy``. This package
re-exports everything from the canonical home and will be
removed in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy is a deprecated re-export; "
    "import from tokenpak.proxy instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy import *  # noqa: F401,F403,E402

__all__ = ["CircuitBreaker", "CircuitBreakerConfig", "CircuitBreakerRegistry", "CircuitState", "CredentialPassthrough", "PassthroughConfig", "ProviderRouter", "ProxyServer", "StreamHandler", "estimate_cost", "extract_sse_tokens", "forward_headers", "get_circuit_breaker_registry", "provider_from_url", "start_proxy"]
