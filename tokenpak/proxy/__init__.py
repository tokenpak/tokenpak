"""
TokenPak proxy utilities.

Unified proxy package merging core proxy and agent proxy modules (FIN-07).
"""

from .streaming import extract_sse_tokens, _extract_sse_tokens, StreamUsage, StreamHandler  # noqa: F401
from .cache_poison import (  # noqa: F401
    strip_cache_poisons,
    classify_cache_miss_reason,
    _strip_cache_poisons,
    _classify_cache_miss_reason,
    _UUID_PATTERN,
    _TIMESTAMP_PATTERN,
    _HEARTBEAT_COUNTER,
)
from .cache import CacheEntry, CacheMetrics, LRUCache  # noqa: F401
from .credential_passthrough import CredentialPassthrough  # noqa: F401
from .tracing import (  # noqa: F401
    _CompressionTimeout,
    StageTrace,
    PipelineTrace,
    TraceStorage,
    TRACE_STORAGE,
)
from .config import (  # noqa: F401
    PROXY_PORT,
    COMPILATION_MODE,
    ENABLE_COMPACTION,
    UPSTREAM_ROUTES,
    ADAPTER_REGISTRY,
    UPSTREAM_TIMEOUT,
    VAULT_INDEX_PATH,
    ACTIVE_PROFILE,
    TRACE_ENABLED,
)
from .monitor import Monitor  # noqa: F401
from .circuit_breaker import (  # noqa: F401
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    get_circuit_breaker_registry,
    provider_from_url,
)


class ProxyStats:
    """Stats/metrics container — resets on each new instance (restart)."""

    def __init__(self):
        self.requests_total = 0
        self.tokens_processed = 0
        self.errors_total = 0
        self.cache_hits = 0
        self.cache_misses = 0


class TokenPakProxy:
    """TokenPak proxy entry point (stub for test surface)."""

    def __init__(self, config=None):
        self.config = config or {}
        self.stats = ProxyStats()
        self._shutdown_event = None

from .websocket import (  # noqa: F401
    _ws_handler,
    start_ws_server,
)

__all__ = ['adapters', 'cache', 'cache_invalidator', 'cache_pipeline', 'cache_poison', 'cache_stats', 'capsule_builder', 'capsule_integration', 'circuit_breaker', 'config', 'connection_pool', 'credential_passthrough', 'custom_providers', 'db', 'degradation', 'embedding_cache', 'embedding_router', 'example_selector', 'failover', 'failover_engine', 'fallback', 'memory_guard', 'middleware', 'monitor', 'oauth', 'passthrough', 'payloads', 'prompt_builder', 'providers', 'proxy', 'request_pipeline', 'router', 'routes', 'server', 'server_async', 'startup', 'stats', 'stats_api', 'streaming', 'token_cache', 'tool_schema_registry', 'tracing', 'vault_bridge', 'websocket']
