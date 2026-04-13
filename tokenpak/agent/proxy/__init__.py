"""
TokenPak Proxy Module

Modular HTTP proxy with compression, context injection, and provider routing.
"""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.proxy is deprecated, use tokenpak.proxy instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    get_circuit_breaker_registry,
    provider_from_url,
)
from .passthrough import PassthroughConfig, forward_headers
from .router import ProviderRouter, estimate_cost
from .server import ProxyServer, start_proxy
from .streaming import StreamHandler, extract_sse_tokens

__all__ = ['ProxyServer', 'start_proxy', 'ProviderRouter', 'estimate_cost', 'extract_sse_tokens', 'StreamHandler', 'forward_headers', 'PassthroughConfig', 'CircuitBreaker', 'CircuitBreakerConfig', 'CircuitBreakerRegistry', 'CircuitState', 'get_circuit_breaker_registry', 'provider_from_url', 'capsule_builder', 'capsule_integration', 'circuit_breaker', 'connection_pool', 'degradation', 'example_selector', 'failover', 'failover_engine', 'intent_policy', 'oauth', 'passthrough', 'prompt_builder', 'providers', 'proxy', 'router', 'server', 'server_async', 'startup', 'stats', 'stats_api', 'streaming', 'tool_schema_registry']
