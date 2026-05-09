# SPDX-License-Identifier: Apache-2.0
"""Cache miss reason tracking — TIP-06.

Provides the ``CacheMissRecord`` dataclass and aggregation helpers for
surfacing why semantic cache lookups fail. Miss reasons use the
``CacheMissReason`` vocabulary from the proxy optimization layer.

These records are persisted to ``tp_cache_miss_reasons`` via TelemetryDB.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence


@dataclass
class CacheMissRecord:
    """Single cache miss event.

    Fields map to ``tp_cache_miss_reasons`` columns.
    """

    request_id: str
    cache_type: str = "semantic"
    reason: str = "unknown"
    route_class: str = ""
    platform: str = ""
    model: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_row(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "cache_type": self.cache_type,
            "reason": self.reason,
            "route_class": self.route_class,
            "platform": self.platform,
            "model": self.model,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


@dataclass
class MissReasonSummary:
    """Aggregated miss metrics for one reason."""

    reason: str
    count: int = 0
    top_routes: Dict[str, int] = field(default_factory=dict)
    top_models: Dict[str, int] = field(default_factory=dict)

    def record(self, *, route: str = "", model: str = "") -> None:
        self.count += 1
        if route:
            self.top_routes[route] = self.top_routes.get(route, 0) + 1
        if model:
            self.top_models[model] = self.top_models.get(model, 0) + 1


def aggregate_cache_miss_reasons(
    records: Sequence[CacheMissRecord],
) -> Dict[str, MissReasonSummary]:
    """Group miss records by reason and count occurrences."""
    result: Dict[str, MissReasonSummary] = {}
    for rec in records:
        if rec.reason not in result:
            result[rec.reason] = MissReasonSummary(reason=rec.reason)
        result[rec.reason].record(route=rec.route_class, model=rec.model)
    return result


def format_miss_reason_summary(
    by_reason: Dict[str, MissReasonSummary],
    *,
    top_n: int = 5,
) -> str:
    """Return a human-readable cache miss reason summary."""
    if not by_reason:
        return "No cache miss data available."

    sorted_reasons = sorted(by_reason.values(), key=lambda x: -x.count)
    total = sum(s.count for s in sorted_reasons)

    lines = [
        f"Cache Miss Reasons (top {top_n})",
        "─" * 40,
        "",
    ]
    for summary in sorted_reasons[:top_n]:
        pct = round(summary.count / total * 100) if total else 0
        lines.append(f"  {summary.reason:<36} {summary.count:>5} ({pct}%)")
        # Top route
        if summary.top_routes:
            top_route = max(summary.top_routes.items(), key=lambda x: x[1])
            lines.append(f"    top route: {top_route[0]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB row from TIP-04 CacheStageTrace
# ---------------------------------------------------------------------------


def cache_stage_trace_to_miss_record(
    request_id: str,
    trace: Any,
    *,
    platform: str = "",
    model: str = "",
) -> Optional[CacheMissRecord]:
    """Convert a CacheStageTrace miss into a CacheMissRecord for storage.

    Returns None if the trace represents a cache hit (no miss to record).
    """
    hit = getattr(trace, "hit", False)
    if hit:
        return None
    reason = getattr(trace, "miss_reason", "") or ""
    if not reason or reason == "context-reuse-only":
        return None

    return CacheMissRecord(
        request_id=request_id,
        cache_type="semantic",
        reason=reason,
        route_class=getattr(trace, "route", "") or "",
        platform=platform,
        model=model,
    )


__all__ = [
    "CacheMissRecord",
    "MissReasonSummary",
    "aggregate_cache_miss_reasons",
    "format_miss_reason_summary",
    "cache_stage_trace_to_miss_record",
]
