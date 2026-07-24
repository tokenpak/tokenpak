"""TokenPak Proxy Cache — LRU eviction with TTL support + cache savings estimation.

Extended in TPK-CONSOLIDATION-A2c with:
- estimate_cache_savings() — USD saved from prompt-cache hits (was MISSING from modular tree)


Provides a thread-safe in-memory cache with:
- LRU eviction when max_size_mb is reached
- Per-entry TTL expiration
- Prometheus-compatible metrics
- Config-driven tuning (proxy.yaml: cache.max_size_mb, cache.ttl_seconds)
"""

import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Cache savings estimation
# Transferred from monolith (TPK-CONSOLIDATION-A2c, lines 3287–3326)
# CACHE_COST_MULTIPLIERS lives in telemetry/cost.py (added in A2a);
# Model costs loaded from dynamic registry (zero heavy deps).
# ---------------------------------------------------------------------------

# Per-provider cache read multipliers (fraction of input cost charged for cached tokens)
_CACHE_READ_MULTIPLIERS_LOCAL = {
    "anthropic": 0.10,
    "openai": 0.50,
    "azure_openai": 0.50,
    "xai": 0.50,
    "groq": 0.0,
    "fireworks": 0.0,
    "together": 0.0,
    "gemini": 0.25,
    "bedrock": 0.10,
    "codex": 0.50,
    "unknown": 0.10,
}


def estimate_cache_savings(provider: Any, cache_read_tokens: int, model: str = "") -> float:
    """Estimate USD saved from cache hits for a given provider.

    Formula: cache_read_tokens * input_cost * (1.0 - read_multiplier)
    Example: 1000 Anthropic cache reads at $3/MTok input → 1000 * 0.000003 * 0.90 = $0.0027 saved

    Args:
        provider: Provider enum value or string name.
        cache_read_tokens: Number of tokens served from cache.
        model: Model name string (used to look up input cost rate).

    Returns:
        Estimated USD savings (float ≥ 0.0).
    """
    if cache_read_tokens <= 0:
        return 0.0

    # Get input cost per token from dynamic registry
    from tokenpak.models import get_model_costs

    costs = get_model_costs(model) if model else {"input": 3.0}
    input_cost_per_mtok = costs["input"]
    input_cost_per_tok = input_cost_per_mtok / 1_000_000

    # Resolve read multiplier — try Provider enum first, fall back to string name
    read_mult = 0.10  # conservative default
    try:
        # Try telemetry/cost.py CACHE_COST_MULTIPLIERS (Provider-keyed)
        from tokenpak.telemetry.cost import CACHE_COST_MULTIPLIERS as _CCM

        entry = _CCM.get(provider)
        if entry is None:
            # Fall back to string key
            provider_str = provider.value if hasattr(provider, "value") else str(provider)
            entry = _CCM.get(provider_str)
        if entry is not None:
            read_mult = entry["read"]
        else:
            # Local fallback
            provider_str = provider.value if hasattr(provider, "value") else str(provider)
            read_mult = _CACHE_READ_MULTIPLIERS_LOCAL.get(provider_str, 0.10)
    except Exception:
        provider_str = provider.value if hasattr(provider, "value") else str(provider)
        read_mult = _CACHE_READ_MULTIPLIERS_LOCAL.get(str(provider_str), 0.10)

    # Savings = tokens * cost * (1 - discount)
    return cache_read_tokens * input_cost_per_tok * (1.0 - read_mult)


@dataclass
class CacheEntry:
    """Single cache entry with metadata."""

    key: str
    value: Any
    created_at: float
    last_accessed: float
    ttl_seconds: Optional[float]
    size_bytes: int

    def is_expired(self) -> bool:
        if self.ttl_seconds is None:
            return False
        elapsed = time.monotonic() - self.created_at
        return elapsed > self.ttl_seconds


@dataclass
class CacheMetrics:
    """Cache performance metrics."""

    hits: int = 0
    misses: int = 0
    evictions_lru: int = 0
    evictions_ttl: int = 0
    current_entries: int = 0
    current_size_bytes: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions_lru": self.evictions_lru,
            "evictions_ttl": self.evictions_ttl,
            "hit_rate": round(self.hit_rate, 4),
            "current_entries": self.current_entries,
            "current_size_mb": round(self.current_size_bytes / 1024 / 1024, 3),
        }


class LRUCache:
    """Thread-safe LRU cache with TTL and size-based eviction.

    Configuration (via proxy.yaml):
        cache:
            max_size_mb: 256
            ttl_seconds: 3600
            eviction_policy: lru

    Usage:
        cache = LRUCache(max_size_mb=256, ttl_seconds=3600)
        cache.set("key", value)
        result = cache.get("key")  # None if missing or expired
    """

    def __init__(
        self,
        max_size_mb: float = 256.0,
        ttl_seconds: Optional[float] = 3600.0,
        eviction_policy: str = "lru",
    ):
        self._max_size_bytes = int(max_size_mb * 1024 * 1024)
        self._ttl_seconds = ttl_seconds
        self._eviction_policy = eviction_policy
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._metrics = CacheMetrics()

    def get(self, key: str) -> Optional[Any]:
        """Retrieve value. Returns None if missing or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._metrics.misses += 1
                return None

            if entry.is_expired():
                self._evict_entry(key, reason="ttl")
                self._metrics.misses += 1
                return None

            # Move to end (most recently used)
            self._store.move_to_end(key)
            entry.last_accessed = time.monotonic()
            self._metrics.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        """Store a value. Evicts LRU entries if size limit reached."""
        size = self._estimate_size(value)
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        now = time.monotonic()

        with self._lock:
            # Remove existing entry if updating
            if key in self._store:
                old = self._store.pop(key)
                self._metrics.current_size_bytes -= old.size_bytes
                self._metrics.current_entries -= 1

            # Evict until there's room
            while self._metrics.current_size_bytes + size > self._max_size_bytes and self._store:
                self._evict_lru()

            entry = CacheEntry(
                key=key,
                value=value,
                created_at=now,
                last_accessed=now,
                ttl_seconds=effective_ttl,
                size_bytes=size,
            )
            self._store[key] = entry
            self._store.move_to_end(key)
            self._metrics.current_size_bytes += size
            self._metrics.current_entries += 1

    def delete(self, key: str) -> bool:
        """Delete a key. Returns True if it existed."""
        with self._lock:
            if key in self._store:
                self._evict_entry(key, reason="manual")
                return True
            return False

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._store.clear()
            self._metrics.current_size_bytes = 0
            self._metrics.current_entries = 0

    def evict_expired(self) -> int:
        """Scan and evict all expired entries. Returns count evicted."""
        count = 0
        with self._lock:
            expired_keys = [k for k, v in self._store.items() if v.is_expired()]
            for key in expired_keys:
                self._evict_entry(key, reason="ttl")
                count += 1
        return count

    @property
    def metrics(self) -> CacheMetrics:
        return self._metrics

    def metrics_dict(self) -> Dict[str, Any]:
        """Prometheus-compatible metrics dict."""
        return self._metrics.to_dict()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    # ------------------------------------------------------------------
    # Internal helpers (must be called with _lock held)
    # ------------------------------------------------------------------

    def _evict_lru(self) -> None:
        """Evict the least-recently-used entry."""
        if not self._store:
            return
        oldest_key, _ = next(iter(self._store.items()))
        self._evict_entry(oldest_key, reason="lru")

    def _evict_entry(self, key: str, reason: str = "lru") -> None:
        """Remove an entry and update metrics."""
        entry = self._store.pop(key, None)
        if entry is None:
            return
        self._metrics.current_size_bytes -= entry.size_bytes
        self._metrics.current_entries -= 1
        if reason == "lru":
            self._metrics.evictions_lru += 1
        elif reason == "ttl":
            self._metrics.evictions_ttl += 1

    @staticmethod
    def _estimate_size(value: Any) -> int:
        """Estimate memory size of a value in bytes."""
        try:
            return sys.getsizeof(value)
        except Exception:
            return 1024  # default 1KB estimate


# Module-level singleton
_default_cache: Optional[LRUCache] = None
_cache_lock = threading.Lock()


def get_cache(max_size_mb: float = 256.0, ttl_seconds: float = 3600.0) -> LRUCache:
    """Get or create the default singleton cache."""
    global _default_cache
    if _default_cache is None:
        with _cache_lock:
            if _default_cache is None:
                _default_cache = LRUCache(
                    max_size_mb=max_size_mb,
                    ttl_seconds=ttl_seconds,
                )
    return _default_cache
