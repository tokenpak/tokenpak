"""
tokenpak/cache/stable_cache.py

StableCache — Long-lived, size-bounded LRU cache for content that rarely
changes between sessions (e.g. compiled pack schemas, tool definitions,
static vault index snapshots).

Key properties:
  - No TTL by default (or a very long one — hours)
  - LRU eviction when max_size is reached
  - Thread-safe
  - Serialisable key/value store (strings, bytes, or JSON-able dicts)
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE = 500
_DEFAULT_TTL = 24 * 3600  # 24 hours — effectively "stable"


class StableCache:
    """LRU cache with a long (default 24 h) TTL.

    >>> sc = StableCache(max_size=10)
    >>> sc.set("k", "v")
    >>> sc.get("k")
    'v'
    >>> sc.size()
    1
    >>> sc.is_cached("k")
    True
    """

    def __init__(
        self,
        max_size: int = _DEFAULT_MAX_SIZE,
        ttl: float = _DEFAULT_TTL,
        name: str = "stable",
    ) -> None:
        self._max_size = max_size
        self._ttl = ttl
        self._name = name
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API (matches the spec: is_cached / retrieve / set / size)
    # ------------------------------------------------------------------

    def is_cached(self, key: str) -> bool:
        """Return True if *key* is present and not expired."""
        import time

        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return False
            # Move to end (LRU touch)
            self._store.move_to_end(key)
            return True

    def retrieve(self, key: str) -> Optional[Any]:
        """Return cached value for *key*, or None if missing / expired."""
        import time

        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    # Alias for natural usage
    def get(self, key: str, default: Any = None) -> Any:
        v = self.retrieve(key)
        return v if v is not None else default

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store *value* under *key*.  Evicts LRU entry if at capacity."""
        import time

        expires_at = time.monotonic() + (ttl if ttl is not None else self._ttl)
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expires_at)
            if len(self._store) > self._max_size:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug("[StableCache:%s] evicted key=%s", self._name, evicted_key)

    def invalidate(self, key: str) -> bool:
        """Remove *key*. Returns True if it existed."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Wipe all entries."""
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        """Return the number of live (non-expired) entries."""
        import time

        now = time.monotonic()
        with self._lock:
            return sum(1 for _, (_, exp) in self._store.items() if now <= exp)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StableCache name={self._name!r} size={self.size()}/{self._max_size}>"
