"""
tokenpak/cache/registry.py

CacheRegistry — Central registry for named StableCache / VolatileCache
instances.  Provides application-level singleton access without global
variables scattered across modules.

Usage::

    from tokenpak.cache import CacheRegistry

    # Get (or lazily create) the default volatile cache
    cache = CacheRegistry.get_default()
    cache.set("session-xyz", data)

    # Register a named cache
    CacheRegistry.register("pack_schemas", StableCache(max_size=200))

    # Retrieve it later
    schema_cache = CacheRegistry.get("pack_schemas")

Architecture::

    CacheRegistry  (class-level singleton dict)
    ├── "default"       → VolatileCache (ttl=270 s)
    ├── "stable"        → StableCache  (ttl=24 h)
    ├── "injection"     → VolatileCache (ttl=270 s, alias for proxy injection)
    └── <user-defined>  → any cache instance
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Union

from .stable_cache import StableCache
from .volatile_cache import VolatileCache

CacheInstance = Union[StableCache, VolatileCache]

_DEFAULT_VOLATILE_TTL = 270.0   # 4.5 minutes
_DEFAULT_STABLE_TTL = 86400.0   # 24 hours


class CacheRegistry:
    """Class-level registry; no instantiation needed."""

    _lock: threading.Lock = threading.Lock()
    _registry: Dict[str, CacheInstance] = {}

    # ------------------------------------------------------------------
    # Default instances (lazy)
    # ------------------------------------------------------------------

    @classmethod
    def get_default(cls) -> VolatileCache:
        """Return the default VolatileCache, creating it on first call."""
        return cls._get_or_create("default", lambda: VolatileCache(ttl=_DEFAULT_VOLATILE_TTL, name="default"))  # type: ignore[return-value]

    @classmethod
    def get_stable(cls) -> StableCache:
        """Return the default StableCache, creating it on first call."""
        return cls._get_or_create("stable", lambda: StableCache(ttl=_DEFAULT_STABLE_TTL, name="stable"))  # type: ignore[return-value]

    @classmethod
    def get_injection(cls) -> VolatileCache:
        """Return the injection cache (alias for the proxy vault-injection cache)."""
        return cls._get_or_create("injection", lambda: VolatileCache(ttl=_DEFAULT_VOLATILE_TTL, name="injection"))  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Generic named access
    # ------------------------------------------------------------------

    @classmethod
    def register(cls, name: str, cache: CacheInstance, *, overwrite: bool = False) -> None:
        """Register *cache* under *name*.

        Raises ValueError if *name* is already registered and overwrite=False.
        """
        with cls._lock:
            if name in cls._registry and not overwrite:
                raise ValueError(
                    f"CacheRegistry: '{name}' is already registered. "
                    "Pass overwrite=True to replace it."
                )
            cls._registry[name] = cache

    @classmethod
    def get(cls, name: str) -> Optional[CacheInstance]:
        """Return the cache registered under *name*, or None."""
        with cls._lock:
            return cls._registry.get(name)

    @classmethod
    def names(cls) -> list[str]:
        """Return all registered cache names."""
        with cls._lock:
            return list(cls._registry.keys())

    @classmethod
    def summary(cls) -> dict[str, dict]:
        """Return a size snapshot for all registered caches."""
        with cls._lock:
            result = {}
            for name, cache in cls._registry.items():
                result[name] = {
                    "type": type(cache).__name__,
                    "size": cache.size(),
                }
            return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _get_or_create(cls, name: str, factory) -> CacheInstance:
        with cls._lock:
            if name not in cls._registry:
                cls._registry[name] = factory()
            return cls._registry[name]
