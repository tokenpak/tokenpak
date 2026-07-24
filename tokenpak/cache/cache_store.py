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

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, List, Optional, TypedDict, cast

try:  # POSIX-only inter-process file locking
    import fcntl
except ImportError:  # pragma: no cover - Windows
    # On Windows there is no fcntl; cross-process saves fall back to
    # lock-free behaviour (unique tmp names still prevent torn files,
    # but concurrent writers may lose each other's keys).
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path.home() / ".tokenpak" / "cache_store.json"


class _CacheEntry(TypedDict):
    """Validated in-memory shape for one persisted cache entry."""

    value: object
    expires_at: float | None


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

    def __init__(self, path: Optional[os.PathLike[str] | str] = None) -> None:
        self._path = Path(path) if path is not None else _DEFAULT_PATH
        self._lock = threading.Lock()
        # In-memory store: key → {"value": ..., "expires_at": float | None}
        self._data: dict[str, _CacheEntry] = {}
        self._loaded = False
        # Keys deleted locally since the last successful save; consulted by
        # the read-modify-write merge in _save() so a delete in this process
        # is not resurrected from another process's on-disk write.
        self._deleted: set[str] = set()
        # clear() requests a full on-disk reset (skip the merge once).
        self._reset_pending = False
        # Failed save cycles are surfaced here (and logged) instead of being
        # silently swallowed: each failure means an update may be lost.
        self.save_errors: int = 0
        self.last_save_error: Optional[str] = None

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
            self._deleted.add(key)
        if existed:
            self._save()
        return existed

    def clear(self) -> None:
        """Remove all entries and persist the empty store."""
        self._ensure_loaded()
        with self._lock:
            self._data.clear()
            self._deleted.clear()
            self._reset_pending = True
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
                self._data = {
                    key: cast(_CacheEntry, entry)
                    for key, entry in parsed.items()
                    if isinstance(key, str) and isinstance(entry, dict)
                }
            else:
                logger.warning("[CacheStore] Unexpected format in %s; resetting.", self._path)
                self._data = {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[CacheStore] Failed to load %s: %s", self._path, exc)
            self._data = {}

    @contextlib.contextmanager
    def _process_lock(self) -> Iterator[None]:
        """Exclusive inter-process lock around the save read-modify-write.

        Uses ``fcntl.flock`` on a sidecar ``<path>.lock`` file so two
        processes sharing one store path serialise their whole-file
        read-merge-write cycles instead of overwriting each other
        (last-writer-wins key loss).

        POSIX-only: on Windows (no fcntl) this degrades to a no-op — saves
        are still torn-file-safe thanks to unique tmp names + os.replace,
        but concurrent processes may lose each other's keys.
        """
        if fcntl is None:  # pragma: no cover - Windows fallback, lock-free
            yield
            return
        lock_path = self._path.with_name(self._path.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _save(self) -> None:
        """Persist in-memory data to disk, pruning expired entries first.

        Concurrency contract:

        - The write is atomic: a per-writer unique tmp file in the target
          directory is populated, fsynced, then ``os.replace``d in, so a
          concurrent reader sees either the old or the new file — never a
          partial one, and never a missing one (the old fixed ``.tmp`` name
          let two processes replace each other's tmp out from underneath,
          losing whole updates to a swallowed FileNotFoundError).
        - Cross-process key loss is prevented by an fcntl.flock-guarded
          read-modify-write: under the lock the current on-disk state is
          re-read and merged beneath this process's entries (locally
          deleted keys stay deleted), so two processes writing disjoint
          keys both survive. Per-key conflicts remain last-writer-wins.
        - Failures are counted in ``save_errors`` and logged as lost
          updates rather than silently swallowed.
        """
        now = time.time()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._process_lock():
                with self._lock:
                    if self._reset_pending:
                        disk: dict[str, _CacheEntry] = {}
                    else:
                        disk = self._read_disk_for_merge()
                        for key in self._deleted:
                            disk.pop(key, None)
                    merged = dict(disk)
                    merged.update(self._data)
                    # Prune expired entries before writing
                    clean: dict[str, _CacheEntry] = {}
                    for key, entry in merged.items():
                        expires_at = entry.get("expires_at")
                        if expires_at is None or expires_at > now:
                            clean[key] = entry
                    self._data = clean
                    payload = json.dumps(clean, indent=2)

                    tmp = self._path.with_name(
                        f"{self._path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
                    )
                    fh = open(tmp, "w", encoding="utf-8")
                    try:
                        fh.write(payload)
                        fh.flush()
                        os.fsync(fh.fileno())
                    finally:
                        fh.close()
                    os.replace(tmp, self._path)
                    # Persisted: deletions are now on disk and the reset (if
                    # any) has been applied.
                    self._deleted.clear()
                    self._reset_pending = False
        except OSError as exc:
            with self._lock:
                self.save_errors += 1
                self.last_save_error = str(exc)
            logger.error(
                "[CacheStore] Failed to save %s (update may be lost; save_errors=%d): %s",
                self._path,
                self.save_errors,
                exc,
            )

    def _read_disk_for_merge(self) -> dict[str, _CacheEntry]:
        """Best-effort fresh read of the on-disk store for the save merge."""
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "[CacheStore] Could not merge on-disk state from %s: %s",
                self._path,
                exc,
            )
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {
            key: cast(_CacheEntry, entry)
            for key, entry in parsed.items()
            if isinstance(key, str) and isinstance(entry, dict)
        }

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CacheStore path={self._path} keys={len(self.keys())}>"


# Sentinel for has() implementation
_SENTINEL = object()
