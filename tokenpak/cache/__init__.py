"""
tokenpak.cache — Stable/Volatile cache layer for TokenPak proxy and runtime.

Public API
----------
StableCache              — Long-lived LRU cache (default TTL: 24 h)
VolatileCache            — Short-lived TTL cache (default TTL: 270 s)
CacheRegistry            — Central registry for named cache instances
CacheMetrics             — Per-request cache telemetry snapshot
CacheTelemetryCollector  — Session-level telemetry aggregator
StablePrefixRegistry     — Content-address registry for stable prompt prefixes
fingerprint              — Compute a stable block ID for any payload
get_registry             — Process-level singleton StablePrefixRegistry accessor

Quick start::

    from tokenpak.cache import StableCache, VolatileCache, CacheRegistry

    # Per-session volatile cache
    cache = CacheRegistry.get_default()
    cache.set("session-abc", my_data)

    # Long-lived pack schema cache
    sc = CacheRegistry.get_stable()
    sc.set("pack:v3", schema_bytes)

    # Stable-prefix content-address registry
    from tokenpak.cache import StablePrefixRegistry, get_registry, fingerprint

    reg = get_registry()
    block_id, is_new = reg.get_or_create(system_prompt_dict)
    print(block_id)   # "spfx-3a7f1c..."
"""

from .stable_cache import StableCache
from .volatile_cache import VolatileCache
from .registry import CacheRegistry
from .telemetry import CacheMetrics, CacheTelemetryCollector, get_collector, reset_collector
from .semantic_cache import (
    SemanticCache,
    SemanticCacheConfig,
    SemanticCacheEntry,
    SemanticCacheLookup,
)
from .prefix_registry import (
    StablePrefixRegistry,
    fingerprint,
    canonicalize,
    get_registry,
    reset_registry,
)

__all__ = [
    "StableCache",
    "VolatileCache",
    "CacheRegistry",
    "CacheMetrics",
    "CacheTelemetryCollector",
    "get_collector",
    "reset_collector",
    # Semantic cache
    "SemanticCache",
    "SemanticCacheConfig",
    "SemanticCacheEntry",
    "SemanticCacheLookup",
    # Stable-prefix content-address registry
    "StablePrefixRegistry",
    "fingerprint",
    "canonicalize",
    "get_registry",
    "reset_registry",
]
