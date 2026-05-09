"""
TokenPak proxy utilities.

Unified proxy package merging core proxy and agent proxy modules (FIN-07).
"""

from .cache import CacheEntry, CacheMetrics, LRUCache  # noqa: F401
from .cache_poison import (  # noqa: F401
    _HEARTBEAT_COUNTER,
    _TIMESTAMP_PATTERN,
    _UUID_PATTERN,
    _classify_cache_miss_reason,
    _strip_cache_poisons,
    classify_cache_miss_reason,
    strip_cache_poisons,
)
from .circuit_breaker import (  # noqa: F401
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    get_circuit_breaker_registry,
    provider_from_url,
)
from .config import (  # noqa: F401
    ACTIVE_PROFILE,
    ADAPTER_REGISTRY,
    COMPILATION_MODE,
    ENABLE_COMPACTION,
    PROXY_PORT,
    TRACE_ENABLED,
    UPSTREAM_ROUTES,
    UPSTREAM_TIMEOUT,
    VAULT_INDEX_PATH,
)
from .credential_passthrough import CredentialPassthrough  # noqa: F401
from .headers import (  # noqa: F401
    CLAUDE_CODE_HEADER_ALLOWLIST,
    OPENCLAW_HEADER_ALLOWLIST,
    forward_headers,
    sanitize_headers,
)
from .monitor import Monitor  # noqa: F401
from .pipeline import (  # noqa: F401
    PipelineResult,
    StageResult,
    process_request,
)
from .request import (  # noqa: F401
    ROUTE_CLAUDE_CODE,
    ROUTE_OPENCLAW,
    ROUTE_SDK,
    HTTPProxy,
    ProxyRequest,
    ProxyResponse,
    _byte_inject_system_block,
    _find_system_array_close,
)
from .route_policy import (  # noqa: F401
    ROUTE_POLICIES,
    get_policy,
    is_auth_passthrough,
    is_byte_preserved,
    is_compaction_enabled,
    platform_tag,
)
from .streaming import (  # noqa: F401
    StreamHandler,
    StreamUsage,
    _extract_sse_tokens,
    extract_sse_tokens,
)
from .tracing import (  # noqa: F401
    TRACE_STORAGE,
    PipelineTrace,
    StageTrace,
    TraceStorage,
    _CompressionTimeout,
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
