"""
tokenpak/cache/telemetry.py

CacheMetrics and CacheTelemetryCollector — lightweight telemetry for tracking
Anthropic prompt-cache hit/miss behaviour per request.

Usage::

    from tokenpak.cache.telemetry import CacheMetrics, CacheTelemetryCollector

    collector = CacheTelemetryCollector()

    # Record a cache hit
    collector.record(CacheMetrics(
        request_id="req_001",
        stable_prefix_tokens=15000,
        stable_cached=True,
        cache_read_tokens=13500,
        total_input_tokens=15000,
    ))

    # Inspect aggregates
    print(collector.hit_rate())          # 1.0
    print(collector.by_miss_reason())    # {}
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class CacheMetrics:
    """Per-request cache telemetry snapshot.

    Attributes
    ----------
    request_id:
        Unique identifier for the request (used for deduplication).
    stable_prefix_tokens:
        Approximate token count of the stable system prefix.
    stable_cached:
        True if Anthropic returned cache_read_input_tokens > 0, indicating
        the stable prefix was served from cache.
    cache_read_tokens:
        Tokens read from cache (from Anthropic usage.cache_read_input_tokens).
    total_input_tokens:
        Total input tokens for the request.
    cache_miss_reason:
        Human-readable reason for cache miss (e.g. "timestamp", "retrieval",
        "cold_start", "schema_change").  Empty/None on cache hit.
    volatile_tail_tokens:
        Approximate token count of the volatile tail (user message + injection).
    cache_creation_tokens:
        Tokens written to cache (from Anthropic usage.cache_creation_input_tokens).
    """

    request_id: str
    stable_prefix_tokens: int
    stable_cached: bool
    cache_read_tokens: int
    total_input_tokens: int
    cache_miss_reason: Optional[str] = None
    volatile_tail_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def effective_tokens(self) -> int:
        """Tokens actually processed (total minus cached)."""
        return self.total_input_tokens - self.cache_read_tokens

    @property
    def cache_ratio(self) -> float:
        """Fraction of input tokens served from cache (0.0–1.0)."""
        if self.total_input_tokens == 0:
            return 0.0
        return self.cache_read_tokens / self.total_input_tokens


class CacheTelemetryCollector:
    """Thread-safe collector for CacheMetrics records.

    Aggregates hit rate and miss reason breakdown across multiple requests.

    >>> collector = CacheTelemetryCollector()
    >>> collector.record(CacheMetrics(request_id="r1", stable_prefix_tokens=1000,
    ...     stable_cached=True, cache_read_tokens=900, total_input_tokens=1000))
    >>> collector.hit_rate()
    1.0
    >>> collector.total()
    1
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: List[CacheMetrics] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, metrics: CacheMetrics) -> None:
        """Append a CacheMetrics record to the collector."""
        with self._lock:
            self._records.append(metrics)

    def clear(self) -> None:
        """Remove all records."""
        with self._lock:
            self._records.clear()

    # ------------------------------------------------------------------
    # Aggregates
    # ------------------------------------------------------------------

    def total(self) -> int:
        """Total number of records."""
        with self._lock:
            return len(self._records)

    def hits(self) -> int:
        """Number of cache-hit records."""
        with self._lock:
            return sum(1 for r in self._records if r.stable_cached)

    def misses(self) -> int:
        """Number of cache-miss records."""
        with self._lock:
            return sum(1 for r in self._records if not r.stable_cached)

    def hit_rate(self) -> float:
        """Fraction of requests that were cache hits (0.0–1.0).

        Returns 0.0 when no records have been collected.
        """
        with self._lock:
            total = len(self._records)
            if total == 0:
                return 0.0
            hit_count = sum(1 for r in self._records if r.stable_cached)
            return hit_count / total

    def by_miss_reason(self) -> Dict[str, int]:
        """Return a dict mapping miss reason → count.

        Only includes miss records (stable_cached=False) that have a
        non-empty cache_miss_reason.

        >>> c = CacheTelemetryCollector()
        >>> from tokenpak.cache.telemetry import CacheMetrics
        >>> c.record(CacheMetrics("r1", 100, False, 0, 100, "timestamp"))
        >>> c.record(CacheMetrics("r2", 100, False, 0, 100, "timestamp"))
        >>> c.record(CacheMetrics("r3", 100, False, 0, 100, "retrieval"))
        >>> c.by_miss_reason()
        {'timestamp': 2, 'retrieval': 1}
        """
        result: Dict[str, int] = {}
        with self._lock:
            for r in self._records:
                if not r.stable_cached and r.cache_miss_reason:
                    result[r.cache_miss_reason] = result.get(r.cache_miss_reason, 0) + 1
        return result

    def avg_cache_ratio(self) -> float:
        """Average cache_ratio across all records."""
        with self._lock:
            if not self._records:
                return 0.0
            return sum(r.cache_ratio for r in self._records) / len(self._records)

    def summary(self) -> dict:
        """Return a dict summarising collector state."""
        with self._lock:
            total = len(self._records)
            hits = sum(1 for r in self._records if r.stable_cached)
            misses = total - hits
            reasons: Dict[str, int] = {}
            for r in self._records:
                if not r.stable_cached and r.cache_miss_reason:
                    reasons[r.cache_miss_reason] = reasons.get(r.cache_miss_reason, 0) + 1
            return {
                "total": total,
                "hits": hits,
                "misses": misses,
                "hit_rate": hits / total if total > 0 else 0.0,
                "miss_reasons": reasons,
            }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<CacheTelemetryCollector total={self.total()} "
            f"hit_rate={self.hit_rate():.0%}>"
        )


__all__ = ["CacheMetrics", "CacheTelemetryCollector"]
