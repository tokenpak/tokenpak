"""
tokenpak/cache_store.py

CacheStore — Persistent key-value store backed by a JSON file.

Suitable for caching configuration, vault metadata, or any data that
should survive process restarts.  Each entry optionally carries a TTL;
expired entries are silently skipped on read and pruned on the next
``_save()`` cycle.

Features
--------
- Configurable storage path (default: ``~/.tokenpak/cache_store.json``)
- Thread-safe via ``threading.Lock``
- Auto-save on every ``set`` / ``delete`` / ``clear``
- Per-entry TTL stored in metadata alongside the value
- Lazy load on first access (file need not exist at import time)

On-disk format
--------------
The JSON file contains a single object whose keys are the user's cache
keys and whose values are objects with the shape::

    {
      "value": <any JSON-serialisable value>,
      "expires_at": <float | null>   // Unix timestamp, or null = no expiry
    }

Usage::

    from tokenpak.cache.cache_store import CacheStore

    store = CacheStore()                     # default path
    store.set("model_list", ["gpt-4", …], ttl=3600)
    names = store.get("model_list")          # list or None after expiry
    store.delete("model_list")

    # Temporary store (e.g. in tests)
    store = CacheStore(path="/tmp/test_store.json")
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path.home() / ".tokenpak" / "cache_store.json"


class CacheStore:
    """Persistent key-value cache backed by a JSON file.

    Parameters
    ----------
    path:
        Filesystem path for the backing JSON file.  Parent directories are
        created automatically on first save.  Defaults to
        ``~/.tokenpak/cache_store.json``.

    Examples
    --------
    >>> import tempfile, os
    >>> with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
    ...     path = f.name
    >>> store = CacheStore(path=path)
    >>> store.set("hello", "world")
    >>> store.get("hello")
    'world'
    >>> store.has("hello")
    True
    >>> store.delete("hello")
    >>> store.get("hello") is None
    True
    >>> os.unlink(path)
    """

    def __init__(self, path: Optional[os.PathLike | str] = None) -> None:
        self._path = Path(path) if path is not None else _DEFAULT_PATH
        self._lock = threading.Lock()
        # In-memory store: key → {"value": ..., "expires_at": float | None}
        self._data: dict[str, dict] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Return the value stored under *key*, or *default* if absent/expired.

        Parameters
        ----------
        key:
            Cache key.
        default:
            Fallback value when the key is missing or expired.
        """
        self._ensure_loaded()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return default
            expires_at = entry.get("expires_at")
            if expires_at is not None and time.time() > expires_at:
                # Lazy expiry — remove and return default
                del self._data[key]
                return default
            return entry["value"]

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Store *value* under *key*.

        Parameters
        ----------
        key:
            Cache key (must be a string).
        value:
            Any JSON-serialisable value.
        ttl:
            Optional time-to-live in seconds.  ``None`` means the entry
            never expires.
        """
        self._ensure_loaded()
        expires_at = (time.time() + ttl) if ttl is not None else None
        with self._lock:
            self._data[key] = {"value": value, "expires_at": expires_at}
        # _save() acquires _lock internally; call AFTER releasing it above
        self._save()

    def delete(self, key: str) -> bool:
        """Remove *key* from the store.

        Returns ``True`` if the key existed (even if already expired).
        """
        self._ensure_loaded()
        with self._lock:
            existed = key in self._data
            self._data.pop(key, None)
        if existed:
            self._save()
        return existed

    def clear(self) -> None:
        """Remove all entries and persist the empty store."""
        self._ensure_loaded()
        with self._lock:
            self._data.clear()
        self._save()

    def has(self, key: str) -> bool:
        """Return ``True`` if *key* exists and has not expired."""
        return self.get(key, default=_SENTINEL) is not _SENTINEL

    def keys(self) -> List[str]:
        """Return all non-expired keys."""
        self._ensure_loaded()
        now = time.time()
        with self._lock:
            result = []
            expired = []
            for k, entry in self._data.items():
                exp = entry.get("expires_at")
                if exp is not None and now > exp:
                    expired.append(k)
                else:
                    result.append(k)
            for k in expired:
                del self._data[k]
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load the backing file on first access (lazy init)."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_locked()
            self._loaded = True

    def _load_locked(self) -> None:
        """Load from disk.  Called with self._lock held."""
        if not self._path.exists():
            self._data = {}
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                self._data = parsed
            else:
                logger.warning("[CacheStore] Unexpected format in %s; resetting.", self._path)
                self._data = {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[CacheStore] Failed to load %s: %s", self._path, exc)
            self._data = {}

    def _save(self) -> None:
        """Persist in-memory data to disk, pruning expired entries first."""
        now = time.time()
        with self._lock:
            # Prune expired entries before writing
            clean = {
                k: v
                for k, v in self._data.items()
                if v.get("expires_at") is None or v["expires_at"] > now
            }
            self._data = clean
            payload = clean

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("[CacheStore] Failed to save %s: %s", self._path, exc)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CacheStore path={self._path} keys={len(self.keys())}>"


# Sentinel for has() implementation
_SENTINEL = object()
