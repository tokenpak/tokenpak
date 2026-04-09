"""
tokenpak/cache/volatile_cache.py

VolatileCache — Short-lived TTL cache for per-session or per-request data
(e.g. vault injection text, BM25 search results, per-session context).

Key properties:
  - Configurable TTL (default 270 s — matches proxy injection cache)
  - Thread-safe
  - Passive expiry (no background thread; expired entries are pruned on access
    and on explicit sweep())
  - Matches the existing _INJECTION_CACHE dict pattern in proxy.py so it can
    serve as a drop-in formalisation
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 270.0  # 4.5 minutes — aligns with proxy injection cache
_DEFAULT_MAX_SIZE = 1000


class VolatileCache:
    """Short-lived TTL cache.

    >>> vc = VolatileCache(ttl=60)
    >>> vc.set("session-abc", {"text": "hello", "tokens": 42})
    >>> vc.is_cached("session-abc")
    True
    >>> vc.retrieve("session-abc")
    {'text': 'hello', 'tokens': 42}
    >>> vc.size()
    1
    """

    def __init__(
        self,
        ttl: float = _DEFAULT_TTL,
        max_size: int = _DEFAULT_MAX_SIZE,
        name: str = "volatile",
    ) -> None:
        self._ttl = ttl
        self._max_size = max_size
        self._name = name
        self._store: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_cached(self, key: str) -> bool:
        """Return True if *key* exists and has not expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return False
            return True

    def retrieve(self, key: str) -> Optional[Any]:
        """Return the cached value or None if missing / expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    def get(self, key: str, default: Any = None) -> Any:
        v = self.retrieve(key)
        return v if v is not None else default

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store *value* under *key* with an optional per-entry TTL override."""
        expires_at = time.monotonic() + (ttl if ttl is not None else self._ttl)
        with self._lock:
            # If at capacity, drop the oldest entry
            if len(self._store) >= self._max_size and key not in self._store:
                oldest_key = next(iter(self._store))
                del self._store[oldest_key]
                logger.debug("[VolatileCache:%s] evicted oldest key=%s", self._name, oldest_key)
            self._store[key] = (value, expires_at)

    def invalidate(self, key: str) -> bool:
        """Remove *key*. Returns True if it was present."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def sweep(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
        return len(expired)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def size(self) -> int:
        """Return the number of live (non-expired) entries."""
        now = time.monotonic()
        with self._lock:
            return sum(1 for _, (_, exp) in self._store.items() if now <= exp)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VolatileCache name={self._name!r} ttl={self._ttl}s size={self.size()}>"
