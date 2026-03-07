"""
TokenPak Telemetry Cache Layer

In-memory TTL cache for dashboard query results. Reduces database load for
repeated queries on the same date ranges and filter combinations.

Architecture:
- CacheStore: thread-safe dict with TTL per entry
- cache_key(): deterministic key builder from filter params
- invalidate_prefix(): batch invalidation by key prefix
- Integration: server.py middleware wraps get_summary, rollup queries, filter options

Default TTLs:
  rollup / KPI summary: 5 minutes
  filter options:       10 minutes
  insights:             5 minutes
  trace search:         no cache (real-time)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTL constants (seconds)
# ---------------------------------------------------------------------------
TTL_ROLLUP = 300  # 5 minutes
TTL_SUMMARY = 300  # 5 minutes
TTL_FILTER_OPTIONS = 600  # 10 minutes
TTL_INSIGHTS = 300  # 5 minutes
TTL_PRICING = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires_at


# ---------------------------------------------------------------------------
# CacheStore
# ---------------------------------------------------------------------------


class CacheStore:
    """Thread-safe in-memory cache with per-entry TTL.

    Parameters
    ----------
    default_ttl:
        Default time-to-live in seconds (300 = 5 min).
    max_size:
        Maximum number of entries before eviction (LRU-like — clears expired).
    """

    def __init__(self, default_ttl: float = 300, max_size: int = 1000) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> tuple[bool, Any]:
        """Return (hit, value). hit=False means cache miss or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.expired:
                if entry is not None:
                    del self._store[key]
                self._misses += 1
                return False, None
            self._hits += 1
            return True, entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store value under key with given TTL (default: self.default_ttl)."""
        if ttl is None:
            ttl = self.default_ttl
        with self._lock:
            if len(self._store) >= self.max_size:
                self._evict_expired()
                # If still over limit, drop oldest 10%
                if len(self._store) >= self.max_size:
                    drop_n = max(1, self.max_size // 10)
                    for k in list(self._store.keys())[:drop_n]:
                        del self._store[k]
            self._store[key] = _CacheEntry(value, ttl)

    def delete(self, key: str) -> bool:
        """Delete a specific key. Returns True if key existed."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def invalidate_prefix(self, prefix: str) -> int:
        """Delete all keys starting with prefix. Returns count deleted."""
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            if keys:
                logger.debug(f"Cache: invalidated {len(keys)} entries with prefix '{prefix}'")
            return len(keys)

    def clear(self) -> int:
        """Clear all cache entries. Returns count deleted."""
        with self._lock:
            count = len(self._store)
            self._store.clear()
            logger.info(f"Cache: cleared {count} entries")
            return count

    def _evict_expired(self) -> int:
        """Remove expired entries. Must be called with lock held."""
        expired = [k for k, v in self._store.items() if v.expired]
        for k in expired:
            del self._store[k]
        return len(expired)

    def evict_expired(self) -> int:
        """Public: remove expired entries. Returns count evicted."""
        with self._lock:
            return self._evict_expired()

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total * 100, 1) if total else 0.0,
                "max_size": self.max_size,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# Cache key builder
# ---------------------------------------------------------------------------


def cache_key(*parts: Any, prefix: str = "") -> str:
    """Build a deterministic cache key from parts.

    Parameters
    ----------
    *parts:
        Components to include in the key (strings, ints, dicts, None).
    prefix:
        Optional prefix to group related keys for batch invalidation.

    Returns
    -------
    str
        A compact cache key string.

    Examples
    --------
    >>> cache_key("summary", 30, prefix="rollup")
    'rollup:summary:30'
    >>> cache_key("summary", {"provider": "anthropic", "model": None}, prefix="kpi")
    'kpi:summary:...'
    """
    normalized = []
    for p in parts:
        if p is None:
            normalized.append("*")
        elif isinstance(p, dict):
            # Sort dict for determinism
            filtered = {k: v for k, v in sorted(p.items()) if v is not None}
            normalized.append(json.dumps(filtered, separators=(",", ":")))
        else:
            normalized.append(str(p))

    key = ":".join(normalized)
    if prefix:
        key = f"{prefix}:{key}"

    # If key is very long, hash the suffix
    if len(key) > 128:
        suffix = hashlib.md5(key.encode()).hexdigest()[:16]
        key = f"{key[:80]}...{suffix}"

    return key


# ---------------------------------------------------------------------------
# Module-level singleton (shared by server.py)
# ---------------------------------------------------------------------------

_default_cache: Optional[CacheStore] = None


def get_cache() -> CacheStore:
    """Return the module-level shared cache instance (lazy init)."""
    global _default_cache
    if _default_cache is None:
        _default_cache = CacheStore(default_ttl=TTL_SUMMARY)
    return _default_cache


def reset_cache() -> None:
    """Reset the module-level cache (useful for testing)."""
    global _default_cache
    _default_cache = None
