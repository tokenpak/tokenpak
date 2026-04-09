"""
tokenpak/cache_manager.py

CacheManager — Unified interface over the existing cache subsystem.

Provides a single entry point for get/set/delete/clear operations that
transparently routes to the appropriate cache layer (volatile or stable)
based on TTL heuristics or an explicit *layer* argument.

Routing rules
-------------
- ``layer="auto"`` (default):
    - ``set(ttl < 300)``  → VolatileCache
    - ``set(ttl >= 300)`` → StableCache
    - ``get`` / ``delete``    → searched in volatile first, then stable
- ``layer="volatile"`` → always use VolatileCache
- ``layer="stable"``   → always use StableCache

Thread safety
-------------
Each underlying cache already holds its own lock, so CacheManager is
thread-safe without an additional outer lock.

Context manager support
-----------------------
Using the manager as a context manager is allowed; no special
setup/teardown occurs, but ``__exit__`` is a no-op so ``with`` blocks
work naturally::

    with CacheManager() as cm:
        cm.set("k", "v", ttl=60)
        value = cm.get("k")

Usage::

    from tokenpak.cache_manager import CacheManager

    cm = CacheManager()
    cm.set("session-abc", {"data": 1}, ttl=120)   # → volatile
    cm.set("schema-v3",   {"spec": 2}, ttl=86400) # → stable
    val = cm.get("session-abc")                   # → {"data": 1}
    cm.delete("session-abc")
    cm.clear(layer="volatile")
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from tokenpak.cache.stable_cache import StableCache
from tokenpak.cache.volatile_cache import VolatileCache

logger = logging.getLogger(__name__)

# Threshold (seconds) below which data is considered "short-lived" → volatile
_VOLATILE_THRESHOLD = 300.0

Layer = str  # "auto" | "volatile" | "stable" | "all"


class CacheManager:
    """Unified interface over :class:`VolatileCache` and :class:`StableCache`.

    Parameters
    ----------
    volatile_cache:
        Existing :class:`VolatileCache` instance to use. If not provided, a
        new instance with default settings is created.
    stable_cache:
        Existing :class:`StableCache` instance to use. If not provided, a
        new instance with default settings is created.
    volatile_threshold:
        TTL threshold (seconds). ``set()`` calls with ``ttl < threshold``
        route to the volatile layer; ``>= threshold`` route to stable.
        Defaults to 300 s.

    Examples
    --------
    >>> cm = CacheManager()
    >>> cm.set("k", "v", ttl=60)
    >>> cm.get("k")
    'v'
    >>> cm.delete("k")
    >>> cm.get("k") is None
    True
    """

    def __init__(
        self,
        volatile_cache: Optional[VolatileCache] = None,
        stable_cache: Optional[StableCache] = None,
        volatile_threshold: float = _VOLATILE_THRESHOLD,
    ) -> None:
        self._volatile: VolatileCache = volatile_cache or VolatileCache(name="manager_volatile")
        self._stable: StableCache = stable_cache or StableCache(name="manager_stable")
        self._threshold = volatile_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, layer: Layer = "auto", default: Any = None) -> Any:
        """Retrieve *key* from the cache.

        Parameters
        ----------
        key:
            Cache key to look up.
        layer:
            ``"auto"``     — search volatile first, then stable.
            ``"volatile"`` — search only the volatile layer.
            ``"stable"``   — search only the stable layer.
        default:
            Value returned when the key is not found (default ``None``).
        """
        if layer == "volatile":
            val = self._volatile.retrieve(key)
        elif layer == "stable":
            val = self._stable.retrieve(key)
        else:  # "auto"
            val = self._volatile.retrieve(key)
            if val is None:
                val = self._stable.retrieve(key)

        return val if val is not None else default

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        layer: Layer = "auto",
    ) -> None:
        """Store *value* under *key*.

        Parameters
        ----------
        key:
            Cache key.
        value:
            Value to store (must be serialisable for persistence scenarios).
        ttl:
            Time-to-live in seconds. ``None`` uses the target layer's default.
        layer:
            ``"auto"``     — route based on *ttl* vs ``volatile_threshold``.
            ``"volatile"`` — force volatile layer.
            ``"stable"``   — force stable layer.
        """
        if layer == "volatile":
            self._volatile.set(key, value, ttl=ttl)
        elif layer == "stable":
            self._stable.set(key, value, ttl=ttl)
        else:  # "auto"
            if ttl is not None and ttl < self._threshold:
                logger.debug(
                    "[CacheManager] auto → volatile  key=%s ttl=%.1fs", key, ttl
                )
                self._volatile.set(key, value, ttl=ttl)
            else:
                logger.debug(
                    "[CacheManager] auto → stable    key=%s ttl=%s",
                    key,
                    f"{ttl:.1f}s" if ttl is not None else "default",
                )
                self._stable.set(key, value, ttl=ttl)

    def delete(self, key: str) -> bool:
        """Remove *key* from both layers.

        Returns ``True`` if the key was found and removed from at least one layer.
        """
        v_hit = self._volatile.invalidate(key)
        s_hit = self._stable.invalidate(key)
        return v_hit or s_hit

    def clear(self, layer: Layer = "all") -> None:
        """Clear one or both layers.

        Parameters
        ----------
        layer:
            ``"all"``      — clear both layers (default).
            ``"volatile"`` — clear only the volatile layer.
            ``"stable"``   — clear only the stable layer.
        """
        if layer in ("all", "volatile"):
            self._volatile.clear()
        if layer in ("all", "stable"):
            self._stable.clear()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def volatile(self) -> VolatileCache:
        """Direct access to the underlying :class:`VolatileCache`."""
        return self._volatile

    @property
    def stable(self) -> StableCache:
        """Direct access to the underlying :class:`StableCache`."""
        return self._stable

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "CacheManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # No cleanup required; underlying caches own their own resources.
        return False  # don't suppress exceptions

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CacheManager threshold={self._threshold}s "
            f"volatile_size={self._volatile.size()} "
            f"stable_size={self._stable.size()}>"
        )
