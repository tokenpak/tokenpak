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

import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _make_key(key: Any) -> str:
    """Normalize *key* to a hashable string.

    Accepts str (pass-through), dict/list (JSON-serialised, sorted keys),
    or anything else (str()-coerced).  This lets callers pass rich objects
    (e.g. request dicts) without triggering ``TypeError: unhashable type``.
    """
    if isinstance(key, str):
        return key
    if isinstance(key, (dict, list, tuple)):
        return json.dumps(key, sort_keys=True, separators=(",", ":"), default=str)
    return str(key)

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

    def is_cached(self, key: Any) -> bool:
        """Return True if *key* is present and not expired."""
        import time

        k = _make_key(key)
        with self._lock:
            entry = self._store.get(k)
            if entry is None:
                return False
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[k]
                return False
            # Move to end (LRU touch)
            self._store.move_to_end(k)
            return True

    def retrieve(self, key: Any) -> Optional[Any]:
        """Return cached value for *key*, or None if missing / expired."""
        import time

        k = _make_key(key)
        with self._lock:
            entry = self._store.get(k)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[k]
                return None
            self._store.move_to_end(k)
            return value

    # Alias for natural usage
    def get(self, key: Any, default: Any = None) -> Any:
        v = self.retrieve(key)
        return v if v is not None else default

    def set(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        """Store *value* under *key*.  Evicts LRU entry if at capacity."""
        import time

        k = _make_key(key)
        expires_at = time.monotonic() + (ttl if ttl is not None else self._ttl)
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
            self._store[k] = (value, expires_at)
            if len(self._store) > self._max_size:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug("[StableCache:%s] evicted key=%s", self._name, evicted_key)

    def invalidate(self, key: Any) -> bool:
        """Remove *key*. Returns True if it existed."""
        k = _make_key(key)
        with self._lock:
            return self._store.pop(k, None) is not None

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
