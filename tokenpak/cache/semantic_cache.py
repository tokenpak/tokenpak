"""
tokenpak/cache/semantic_cache.py

SemanticCache — Near-duplicate query reuse for TokenPak proxy.

When a new query is sufficiently similar to a recent query, the previous
LLM response is returned directly — no upstream call is made.

Matching pipeline (fastest → slowest):
  1. Exact normalised match          → always reuse
  2. Jaccard similarity on token set → threshold (default 0.90)
  3. Optional embedding cosine sim   → threshold (default 0.95)

Configuration
-------------
All options live in ``SemanticCacheConfig``::

    cfg = SemanticCacheConfig(
        enabled=True,
        ttl_seconds=300,
        similarity_threshold=0.90,
        max_entries=100,
        scope="session",        # "session" | "agent" | "global"
    )
    sc = SemanticCache(cfg)

Integration (wire-format-aware)
-----------
Call ``lookup`` BEFORE the upstream LLM call, passing the expected response
format.  Call ``store`` AFTER you receive the upstream response, passing the
raw bytes and content-type::

    entry = cache.lookup(query_text, expected_format="json")
    if entry.hit:
        # Serve entry.response bytes with entry.content_type header
        return entry.entry.response, entry.entry.content_type

    response_bytes, content_type = call_llm(query_text)
    wire_format = "sse" if "text/event-stream" in content_type else "json"
    cache.store(query_text, response_bytes, content_type, wire_format)

Wire-format rules:
  - JSON entries (wire_format="json") are only served to JSON clients.
  - SSE entries (wire_format="sse") are only served to streaming clients.
  - Cross-format lookups always return a cache miss — never serve JSON bytes
    to an SSE parser or vice versa.

Hit/miss details are returned as ``SemanticCacheLookup``; attach to trace
metadata for observability.

Schema migration note (wire-format migration):
  Legacy (pre wire-format-migration) entries stored ``response`` as ``dict``.  On first call to
  ``_evict_expired`` (triggered by any ``lookup``), old-schema entries are
  detected and removed with a warning.  The cache then rebuilds from live
  upstream traffic.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TTL = 300  # 5 minutes
_DEFAULT_MAX_ENTRIES = 100
_DEFAULT_THRESHOLD = 0.90
_FILLER_RE = re.compile(
    r"\b(please|kindly|could you|can you|would you|hey|hi|hello|thanks|"
    r"thank you|just|really|very|so|also|actually|basically)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SemanticCacheEntry:
    """A single cached query/response pair.

    Response is stored as raw bytes with an explicit wire_format and
    content_type so the proxy can serve the entry back to the client with the
    correct HTTP Content-Type header without any re-serialization.
    """

    query_normalized: str
    query_hash: str
    response: bytes  # raw upstream bytes (JSON or SSE)
    content_type: str  # e.g. "application/json" or "text/event-stream; charset=utf-8"
    wire_format: Literal["json", "sse"]  # "json" | "sse" — never cross-serve
    created_at: float
    ttl_seconds: int = _DEFAULT_TTL
    hit_count: int = 0
    similarity_score: float = 1.0  # 1.0 for exact matches
    # Additional cache-key dimensions (model, etc.).
    key_features: dict[str, object] = field(default_factory=dict)

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl_seconds

    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at


@dataclass
class SemanticCacheLookup:
    """Result of a cache lookup — attach to trace/span metadata."""

    hit: bool
    query_hash: str = ""
    matched_hash: str = ""
    similarity: float = 0.0
    match_strategy: str = "none"  # "exact" | "jaccard" | "embedding" | "none"
    entry: Optional[SemanticCacheEntry] = None
    savings_tokens: int = 0  # estimated input tokens saved


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SemanticCacheConfig:
    """Runtime configuration for SemanticCache."""

    enabled: bool = True
    ttl_seconds: int = _DEFAULT_TTL
    similarity_threshold: float = _DEFAULT_THRESHOLD
    max_entries: int = _DEFAULT_MAX_ENTRIES
    scope: str = "session"  # "session" | "agent" | "global"


# ---------------------------------------------------------------------------
# Core cache
# ---------------------------------------------------------------------------


class SemanticCache:
    """
    Semantic query cache with normalised-text and Jaccard matching.

    Thread-safe.  Uses an OrderedDict (LRU eviction by insertion order).

    Wire-format-aware — lookup requires ``expected_format`` and only
    returns entries whose ``wire_format`` matches.  Store requires raw bytes +
    content_type + wire_format.

    >>> import json
    >>> cfg = SemanticCacheConfig(ttl_seconds=60, max_entries=10)
    >>> sc = SemanticCache(cfg)
    >>> sc.store("What is the capital of France?", b'{"answer":"Paris"}', "application/json", "json")
    SemanticCacheEntry(...)
    >>> result = sc.lookup("What is the capital of France?", expected_format="json")
    >>> result.hit
    True
    >>> result.match_strategy
    'exact'
    """

    def __init__(self, config: Optional[SemanticCacheConfig] = None) -> None:
        self._cfg = config or SemanticCacheConfig()
        self._store: OrderedDict[str, SemanticCacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._total_hits = 0
        self._total_misses = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, query: str, *, expected_format: str = "json") -> SemanticCacheLookup:
        """
        Look up *query* in the cache, returning only entries matching *expected_format*.

        Only returns entries whose ``wire_format`` matches *expected_format*.
        Cross-format lookups always return a miss (never serve JSON bytes to
        an SSE client or vice versa).

        The internal store key is composite (``query_hash:wire_format``) so JSON
        and SSE entries for the same query can coexist without overwriting each
        other (``key_features`` dimension).

        Returns a ``SemanticCacheLookup`` with ``hit=True`` and the cached
        ``entry`` when a match is found, otherwise ``hit=False``.
        """
        if not self._cfg.enabled:
            return SemanticCacheLookup(hit=False, match_strategy="disabled")

        self._evict_expired()

        normalised = _normalise(query)
        query_hash = _hash(normalised)

        with self._lock:
            # Only consider entries matching the requested wire format.
            # Store keys are composite (hash:wire_format) so this filter is O(n)
            # but n is small (bounded by max_entries).
            entries = [e for e in self._store.values() if e.wire_format == expected_format]

        # --- 1. Exact normalised match ---
        for entry in entries:
            if entry.query_hash == query_hash or entry.query_normalized == normalised:
                with self._lock:
                    entry.hit_count += 1
                self._total_hits += 1
                logger.debug(
                    "[SemanticCache] HIT exact hash=%s fmt=%s hits=%d",
                    query_hash[:8],
                    expected_format,
                    entry.hit_count,
                )
                return SemanticCacheLookup(
                    hit=True,
                    query_hash=query_hash,
                    matched_hash=entry.query_hash,
                    similarity=1.0,
                    match_strategy="exact",
                    entry=entry,
                )

        # --- 2. Jaccard similarity ---
        best: Optional[SemanticCacheEntry] = None
        best_score = 0.0
        query_tokens = _tokenize(normalised)

        for entry in entries:
            score = _jaccard(query_tokens, _tokenize(entry.query_normalized))
            if score > best_score:
                best_score = score
                best = entry

        if best is not None and best_score >= self._cfg.similarity_threshold:
            with self._lock:
                best.hit_count += 1
            self._total_hits += 1
            logger.debug(
                "[SemanticCache] HIT jaccard=%.3f hash=%s fmt=%s",
                best_score,
                query_hash[:8],
                expected_format,
            )
            return SemanticCacheLookup(
                hit=True,
                query_hash=query_hash,
                matched_hash=best.query_hash,
                similarity=best_score,
                match_strategy="jaccard",
                entry=best,
            )

        # --- Miss ---
        self._total_misses += 1
        logger.debug(
            "[SemanticCache] MISS hash=%s fmt=%s jaccard_best=%.3f",
            query_hash[:8],
            expected_format,
            best_score,
        )
        return SemanticCacheLookup(
            hit=False,
            query_hash=query_hash,
            similarity=best_score,
            match_strategy="none",
        )

    def store(
        self,
        query: str,
        response_bytes: bytes,
        content_type: str = "application/json",
        wire_format: Literal["json", "sse"] = "json",
    ) -> SemanticCacheEntry:
        """
        Store *response_bytes* for *query*.

        Accepts raw bytes + content_type + wire_format.  No JSON
        parsing is performed.  Evicts the oldest entry when at capacity.

        The internal store key is composite (``query_hash:wire_format``) so
        JSON and SSE entries for the same query can coexist.

        Returns the new ``SemanticCacheEntry``.
        """
        if not isinstance(response_bytes, bytes):
            raise TypeError("response_bytes must be bytes")

        normalised = _normalise(query)
        query_hash = _hash(normalised)
        # Composite key — wire_format is a key dimension so JSON and SSE
        # entries for the same query can coexist without overwriting each other.
        _store_key = f"{query_hash}:{wire_format}"

        entry = SemanticCacheEntry(
            query_normalized=normalised,
            query_hash=query_hash,
            response=response_bytes,
            content_type=content_type,
            wire_format=wire_format,
            created_at=time.monotonic(),
            ttl_seconds=self._cfg.ttl_seconds,
        )

        with self._lock:
            # Overwrite if same (query, wire_format) pair exists
            if _store_key in self._store:
                del self._store[_store_key]
            # Evict oldest if at capacity
            while len(self._store) >= self._cfg.max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug("[SemanticCache] evicted key=%s (capacity)", evicted_key[:16])
            self._store[_store_key] = entry

        return entry

    def stats(self) -> Dict[str, int | float]:
        """Return hit/miss statistics."""
        total = self._total_hits + self._total_misses
        return {
            "hits": self._total_hits,
            "misses": self._total_misses,
            "total": total,
            "hit_rate": (self._total_hits / total) if total else 0.0,
            "size": self.size(),
        }

    def size(self) -> int:
        """Return the number of live (non-expired) entries."""
        with self._lock:
            return sum(1 for e in self._store.values() if not e.is_expired())

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
        self._total_hits = 0
        self._total_misses = 0

    def invalidate(self, query: str) -> bool:
        """Remove all entries for *query* (any wire format). Returns True if any was present."""
        normalised = _normalise(query)
        query_hash = _hash(normalised)
        with self._lock:
            # Composite keys — remove all formats for this query
            removed_keys = [k for k in self._store if k.startswith(f"{query_hash}:")]
            for k in removed_keys:
                del self._store[k]
            return bool(removed_keys)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_expired(self) -> int:
        """Remove expired entries and old-schema (pre wire-format-migration) entries.

        Wire-format migration: entries with ``response: dict`` (old schema) are
        deleted and logged as warnings.  The cache rebuilds from fresh traffic.
        Returns count of removed entries.
        """
        with self._lock:
            expired = [k for k, e in self._store.items() if e.is_expired()]
            for k in expired:
                del self._store[k]
            # Clean break — evict any entry whose response is not bytes
            old_schema = [k for k, e in self._store.items() if not isinstance(e.response, bytes)]
            for k in old_schema:
                logger.warning(
                    "[SemanticCache] evicting old-schema entry %s "
                    "(response was dict, now requires bytes) — cache will rebuild",
                    k[:8],
                )
                del self._store[k]
        removed = len(expired) + len(old_schema)
        if expired:
            logger.debug("[SemanticCache] swept %d expired entries", len(expired))
        return removed


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """
    Normalise a query string for comparison.

    Steps:
      1. Strip leading/trailing whitespace
      2. Collapse internal whitespace to single spaces
      3. Lowercase
      4. Remove common filler words
      5. Strip non-alphanumeric characters (keep spaces)
    """
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = text.lower()
    text = _FILLER_RE.sub("", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize(text: str) -> frozenset[str]:
    """Split normalised text into a frozenset of tokens."""
    return frozenset(text.split())


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _hash(normalised: str) -> str:
    """SHA-256 hex digest (truncated to 64 chars) of a normalised query."""
    return hashlib.sha256(normalised.encode()).hexdigest()
