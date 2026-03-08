"""
tokenpak.cache — Stable/Volatile cache layer for TokenPak proxy and runtime.

Public API
----------
StableCache    — Long-lived LRU cache (default TTL: 24 h)
VolatileCache  — Short-lived TTL cache (default TTL: 270 s)
CacheRegistry  — Central registry for named cache instances

Quick start::

    from tokenpak.cache import StableCache, VolatileCache, CacheRegistry

    # Per-session volatile cache
    cache = CacheRegistry.get_default()
    cache.set("session-abc", my_data)

    # Long-lived pack schema cache
    sc = CacheRegistry.get_stable()
    sc.set("pack:v3", schema_bytes)
"""

from .stable_cache import StableCache
from .volatile_cache import VolatileCache
from .registry import CacheRegistry

__all__ = ["StableCache", "VolatileCache", "CacheRegistry"]
