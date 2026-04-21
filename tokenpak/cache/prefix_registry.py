"""
tokenpak/cache/prefix_registry.py

StablePrefixRegistry — Content-address registry for stable prompt prefixes.

Purpose
-------
Assigns deterministic, content-derived IDs to stable prompt payloads.
Identical content (regardless of key ordering or whitespace in JSON structures)
always maps to the same ID, enabling:

  - Cache-hit attribution across sessions
  - Drift detection (same block_id → guaranteed identical content)
  - Observability: log/trace which stable blocks are active

ID format::

    spfx-<16-char-hex>   (first 16 hex chars of SHA-256 over canonical payload)

Registry is **in-memory by default** (no disk writes needed for correctness).
It is intentionally not backed by StableCache to avoid circular dependencies;
it's a plain dict with a lock.

Usage::

    from tokenpak.cache.prefix_registry import get_registry, fingerprint

    reg = get_registry()
    block_id, is_new = reg.get_or_create(payload)
    print(block_id)            # "spfx-3a7f1c..."
    print(reg.metadata(block_id))
    # {"block_id": "spfx-...", "first_seen": 1741..., "last_seen": 1741...,
    #  "hit_count": 3, "size_bytes": 412}

Non-breaking integration
------------------------
Call ``reg.get_or_create(payload)`` anywhere a stable prefix is assembled.
The returned ``block_id`` can be attached to request diagnostics without
changing the payload wire format.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_ID_PREFIX = "spfx-"
_HASH_CHARS = 16  # 16 hex chars → 64-bit collision resistance (sufficient for prefix IDs)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def canonicalize(payload: Any) -> bytes:
    """Return a canonical byte representation of *payload*.

    Rules:
    - dicts → JSON with sorted keys, no extra whitespace
    - strings → UTF-8 encoded as-is (no JSON quoting)
    - bytes → used directly
    - anything else → JSON with sorted keys

    The goal is that two payloads considered "the same" produce the same bytes
    regardless of key order or incidental whitespace variation.
    """
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    # dict / list / other — normalise via JSON with sorted keys
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def fingerprint(payload: Any) -> str:
    """Return a stable block ID string for *payload*.

    The same content always produces the same ID (deterministic, content-addressed).

    >>> fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})
    True
    >>> fingerprint("hello") == fingerprint("hello")
    True
    >>> fingerprint({"a": 1}) != fingerprint({"a": 2})
    True
    """
    raw = canonicalize(payload)
    digest = hashlib.sha256(raw).hexdigest()[:_HASH_CHARS]
    return f"{_ID_PREFIX}{digest}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class StablePrefixRegistry:
    """Thread-safe in-memory registry mapping content-addressed IDs to metadata.

    >>> reg = StablePrefixRegistry()
    >>> bid, is_new = reg.get_or_create({"system": "You are helpful."})
    >>> is_new
    True
    >>> bid2, is_new2 = reg.get_or_create({"system": "You are helpful."})
    >>> bid == bid2
    True
    >>> is_new2
    False
    >>> reg.metadata(bid)["hit_count"]
    2
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # block_id → {"block_id", "first_seen", "last_seen", "hit_count", "size_bytes"}
        self._entries: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def get_or_create(self, payload: Any) -> Tuple[str, bool]:
        """Return (block_id, is_new) for *payload*.

        - ``block_id`` is deterministic: identical payloads always yield the same ID.
        - ``is_new`` is True on the first time this ID is seen in this registry instance.
        - Metadata counters are updated on every call.

        This method is thread-safe.
        """
        block_id = fingerprint(payload)
        now = time.time()
        size = len(canonicalize(payload))

        with self._lock:
            if block_id in self._entries:
                entry = self._entries[block_id]
                entry["last_seen"] = now
                entry["hit_count"] += 1
                logger.debug(
                    "[PrefixRegistry] hit block_id=%s hit_count=%d",
                    block_id,
                    entry["hit_count"],
                )
                return block_id, False
            else:
                self._entries[block_id] = {
                    "block_id": block_id,
                    "first_seen": now,
                    "last_seen": now,
                    "hit_count": 1,
                    "size_bytes": size,
                }
                logger.debug("[PrefixRegistry] new block_id=%s size=%d", block_id, size)
                return block_id, True

    def metadata(self, block_id: str) -> Optional[Dict[str, Any]]:
        """Return a copy of the metadata dict for *block_id*, or None if unknown."""
        with self._lock:
            entry = self._entries.get(block_id)
            return dict(entry) if entry is not None else None

    def all_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Return a snapshot of all registry entries (copies)."""
        with self._lock:
            return {k: dict(v) for k, v in self._entries.items()}

    def size(self) -> int:
        """Number of distinct stable blocks currently tracked."""
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """Wipe all entries (useful for tests)."""
        with self._lock:
            self._entries.clear()

    def summary(self) -> Dict[str, Any]:
        """Return a high-level summary suitable for diagnostics/logging."""
        with self._lock:
            total_hits = sum(e["hit_count"] for e in self._entries.values())
            return {
                "distinct_blocks": len(self._entries),
                "total_hits": total_hits,
                "block_ids": list(self._entries.keys()),
            }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StablePrefixRegistry blocks={self.size()}>"


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
_default_registry: Optional[StablePrefixRegistry] = None


def get_registry() -> StablePrefixRegistry:
    """Return the process-level singleton StablePrefixRegistry."""
    global _default_registry
    with _registry_lock:
        if _default_registry is None:
            _default_registry = StablePrefixRegistry()
        return _default_registry


def reset_registry() -> None:
    """Reset the process-level singleton (primarily for test isolation)."""
    global _default_registry
    with _registry_lock:
        _default_registry = None
