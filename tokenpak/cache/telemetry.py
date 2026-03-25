"""
tokenpak/cache/telemetry.py

Cache telemetry for the TokenPak proxy.

Tracks per-request cache hit/miss data and aggregates into session-level
statistics.  Designed for zero-overhead when no consumers are attached:
``CacheTelemetryCollector.record()`` is always O(1) with bounded memory.

Classes
-------
CacheMetrics          — per-request snapshot (dataclass)
CacheTelemetryCollector — thread-safe session-level aggregator

Usage::

    from tokenpak.cache.telemetry import (
        CacheMetrics,
        CacheTelemetryCollector,
        get_collector,
    )

    # Record a request
    collector = get_collector()
    collector.record(CacheMetrics(
        request_id="req_001",
        stable_prefix_tokens=15_000,
        stable_cached=True,
        cache_read_tokens=13_500,
        total_input_tokens=15_200,
        volatile_tail_tokens=200,
        output_tokens=512,
    ))

    # Query aggregate stats
    print(collector.hit_rate())         # 1.0
    print(collector.avg_cache_ratio())  # 0.888...
    print(collector.summary())          # dict with all KPIs
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = [
    "CacheMetrics",
    "CacheTelemetryCollector",
    "get_collector",
    "reset_collector",
]

# ---------------------------------------------------------------------------
# Per-request metrics
# ---------------------------------------------------------------------------

# Estimated cost-saving multiplier for cache-read tokens vs fresh input tokens.
# Anthropic charges ~10% for cache reads vs full price for fresh input.
_CACHE_READ_COST_MULTIPLIER = 0.10


@dataclass
class CacheMetrics:
    """Snapshot of cache behaviour for a single proxy request.

    Parameters
    ----------
    request_id:
        Unique identifier for the request (any string; auto-generated if
        ``""`` is passed, but callers should supply a meaningful id).
    stable_prefix_tokens:
        Estimated token count of the *stable* portion of the prompt that
        is expected to be cache-resident after the first request.
    stable_cached:
        True when the LLM reported cache-read tokens > 0.
    cache_miss_reason:
        Human-readable diagnosis string when the cache missed.
        ``None`` means cache hit (or unknown miss, not diagnosed).
    volatile_tail_tokens:
        Tokens in the *volatile* tail (user message + tool call etc.).
    total_input_tokens:
        Total input token count as reported by the LLM API response.
    cache_read_tokens:
        Tokens served from the prompt cache (``cache_read_input_tokens``
        in Anthropic's usage object).
    cache_creation_tokens:
        Tokens written into the prompt cache for this request
        (``cache_creation_input_tokens``).
    output_tokens:
        Output / completion tokens for this request.
    timestamp:
        Unix epoch seconds when the request was recorded.
    """

    request_id: str
    stable_prefix_tokens: int
    stable_cached: bool
    cache_read_tokens: int = 0
    total_input_tokens: int = 0
    cache_miss_reason: Optional[str] = None
    volatile_tail_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    timestamp: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def cache_hit(self) -> bool:
        """True when the prompt cache served at least one token."""
        return self.cache_read_tokens > 0

    @property
    def cache_hit_ratio(self) -> float:
        """Fraction of input tokens served from cache (0.0–1.0)."""
        if self.total_input_tokens == 0:
            return 0.0
        return self.cache_read_tokens / self.total_input_tokens

    @property
    def effective_tokens(self) -> int:
        """Total tokens minus cache_read tokens (new tokens processed)."""
        return self.total_input_tokens - self.cache_read_tokens

    @property
    def cache_ratio(self) -> float:
        """Alias for cache_hit_ratio (cache_read / total input)."""
        return self.cache_hit_ratio

    @property
    def cost_saved(self) -> float:
        """Estimated relative cost saving from cache reads.

        Cache reads cost ~10% of fresh token price, so we save ~90% on
        those tokens.  The value is expressed in *equivalent fresh token*
        units (multiply by your per-token price to get dollars).
        """
        return self.cache_read_tokens * (1.0 - _CACHE_READ_COST_MULTIPLIER)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "stable_prefix_tokens": self.stable_prefix_tokens,
            "stable_cached": self.stable_cached,
            "cache_hit": self.cache_hit,
            "cache_hit_ratio": round(self.cache_hit_ratio, 4),
            "cache_miss_reason": self.cache_miss_reason,
            "volatile_tail_tokens": self.volatile_tail_tokens,
            "total_input_tokens": self.total_input_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "output_tokens": self.output_tokens,
            "cost_saved": round(self.cost_saved, 2),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Session-level aggregator
# ---------------------------------------------------------------------------

_MAX_RECENT = 100  # keep last N requests in memory


class CacheTelemetryCollector:
    """Thread-safe session-level cache telemetry aggregator.

    All public methods are safe to call from multiple threads.

    Parameters
    ----------
    max_recent:
        Maximum number of per-request ``CacheMetrics`` objects to retain
        in memory.  Older entries are dropped (FIFO) to bound memory use.
    """

    def __init__(self, max_recent: int = _MAX_RECENT) -> None:
        self._lock = threading.Lock()
        self._recent: List[CacheMetrics] = []
        self._max_recent = max_recent

        # Running totals (never reset; allow computing session-lifetime KPIs)
        self._total_requests: int = 0
        self._total_hits: int = 0
        self._total_cache_read_tokens: int = 0
        self._total_cache_creation_tokens: int = 0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_cost_saved: float = 0.0
        self._miss_reasons: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record(self, metrics: CacheMetrics) -> None:
        """Record a single request's cache metrics.

        Parameters
        ----------
        metrics:
            A populated ``CacheMetrics`` instance.
        """
        with self._lock:
            # Update running totals
            self._total_requests += 1
            if metrics.cache_hit:
                self._total_hits += 1
            self._total_cache_read_tokens += metrics.cache_read_tokens
            self._total_cache_creation_tokens += metrics.cache_creation_tokens
            self._total_input_tokens += metrics.total_input_tokens
            self._total_output_tokens += metrics.output_tokens
            self._total_cost_saved += metrics.cost_saved

            if metrics.cache_miss_reason:
                key = metrics.cache_miss_reason
                self._miss_reasons[key] = self._miss_reasons.get(key, 0) + 1

            # Bounded recent list
            self._recent.append(metrics)
            if len(self._recent) > self._max_recent:
                self._recent.pop(0)

    # ------------------------------------------------------------------
    # Read path (aggregate KPIs)
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear all recorded metrics and reset state."""
        with self._lock:
            self._recent = []
            self._total_requests = 0
            self._total_hits = 0
            self._total_cache_read_tokens = 0
            self._total_cache_creation_tokens = 0
            self._total_input_tokens = 0
            self._total_output_tokens = 0
            self._total_cost_saved = 0.0
            self._miss_reasons = {}

    def hit_rate(self) -> float:
        """Fraction of requests that were cache hits (0.0–1.0)."""
        with self._lock:
            if self._total_requests == 0:
                return 0.0
            return self._total_hits / self._total_requests

    def total(self) -> int:
        """Total number of requests recorded."""
        with self._lock:
            return self._total_requests

    def hits(self) -> int:
        """Total number of cache hits recorded."""
        with self._lock:
            return self._total_hits

    def misses(self) -> int:
        """Total number of cache misses recorded."""
        with self._lock:
            return self._total_requests - self._total_hits

    def avg_cache_ratio(self) -> float:
        """Average per-request cache-read / total-input ratio (0.0–1.0)."""
        with self._lock:
            recent = list(self._recent)
        if not recent:
            return 0.0
        ratios = [m.cache_hit_ratio for m in recent]
        return sum(ratios) / len(ratios)

    def by_miss_reason(self) -> Dict[str, int]:
        """Return a copy of the miss-reason histogram."""
        with self._lock:
            return dict(self._miss_reasons)

    def recent_requests(self, n: int = 10) -> List[dict]:
        """Return the last *n* requests as dicts (newest last)."""
        with self._lock:
            snapshot = list(self._recent)
        return [m.to_dict() for m in snapshot[-n:]]

    def summary(self) -> dict:
        """Return all KPIs as a JSON-serialisable dict."""
        with self._lock:
            total = self._total_requests
            hits = self._total_hits
            misses = total - hits
            hit_rate = hits / total if total else 0.0
            miss_rate = misses / total if total else 0.0
            reasons = dict(self._miss_reasons)
            cost_saved = round(self._total_cost_saved, 2)
            cache_read = self._total_cache_read_tokens
            cache_creation = self._total_cache_creation_tokens
            input_total = self._total_input_tokens
            output_total = self._total_output_tokens
            recent_snap = list(self._recent)

        # Compute avg_cache_ratio outside the lock (from already-captured snapshot)
        if recent_snap:
            avg_ratio = sum(m.cache_hit_ratio for m in recent_snap) / len(recent_snap)
        else:
            avg_ratio = 0.0

        recent_dicts = [m.to_dict() for m in recent_snap[-10:]]

        return {
            "total": total,  # alias for backward compat
            "hits": hits,  # alias
            "misses": misses,  # alias
            "total_requests": total,
            "cache_hits": hits,
            "cache_misses": misses,
            "hit_rate": round(hit_rate, 4),
            "miss_rate": round(miss_rate, 4),
            "hit_rate_pct": round(hit_rate * 100, 1),
            "avg_cache_ratio": round(avg_ratio, 4),
            "avg_cache_ratio_pct": round(avg_ratio * 100, 1),
            "total_cache_read_tokens": cache_read,
            "total_cache_creation_tokens": cache_creation,
            "total_input_tokens": input_total,
            "total_output_tokens": output_total,
            "estimated_cost_saved_tokens": cost_saved,
            "miss_reasons": reasons,
            "recent_requests": recent_dicts,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_collector_lock = threading.Lock()
_collector: Optional[CacheTelemetryCollector] = None


def get_collector() -> CacheTelemetryCollector:
    """Return the module-level shared ``CacheTelemetryCollector`` (lazy init)."""
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = CacheTelemetryCollector()
    return _collector


def reset_collector() -> None:
    """Reset the module-level collector (useful in tests)."""
    global _collector
    with _collector_lock:
        _collector = None
