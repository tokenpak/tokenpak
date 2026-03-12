"""request_audit.py — Per-request savings audit and tracking.

Provides RequestAudit dataclass, RequestAuditor tracker, and cost
calculation utilities for proving TokenPak's value per-request.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tokenpak.pricing import get_rates

# Cache read cost = 10% of input cost (Anthropic standard)
CACHE_READ_DISCOUNT = 0.1


@dataclass
class RequestAudit:
    """Per-request audit record with savings breakdown."""

    request_id: str = ""
    timestamp: float = 0.0
    model: str = ""
    input_tokens: int = 0
    sent_input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_hit: bool = False
    status: int = 200
    latency_ms: int = 0
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def compression_tokens_saved(self) -> int:
        return max(0, self.input_tokens - self.sent_input_tokens)

    @property
    def baseline_cost(self) -> float:
        """Cost without TokenPak (all tokens at full input price)."""
        rates = get_rates(self.model)
        input_cost = self.input_tokens * rates["input"] / 1_000_000
        output_cost = self.output_tokens * rates["output"] / 1_000_000
        return input_cost + output_cost

    @property
    def actual_cost(self) -> float:
        """Cost with TokenPak (compression + cache discounts)."""
        rates = get_rates(self.model)
        sent_cost = self.sent_input_tokens * rates["input"] / 1_000_000
        cache_cost = self.cache_read_tokens * rates.get("cached", rates["input"] * CACHE_READ_DISCOUNT) / 1_000_000
        output_cost = self.output_tokens * rates["output"] / 1_000_000
        return sent_cost + cache_cost + output_cost

    @property
    def compression_savings(self) -> float:
        """USD saved by compression."""
        rates = get_rates(self.model)
        return self.compression_tokens_saved * rates["input"] / 1_000_000

    @property
    def cache_savings(self) -> float:
        """USD saved by cache reads (vs paying full input price)."""
        if self.cache_read_tokens <= 0:
            return 0.0
        rates = get_rates(self.model)
        full_cost = self.cache_read_tokens * rates["input"] / 1_000_000
        cache_cost = self.cache_read_tokens * rates.get("cached", rates["input"] * CACHE_READ_DISCOUNT) / 1_000_000
        return full_cost - cache_cost

    @property
    def total_savings(self) -> float:
        return self.compression_savings + self.cache_savings

    @property
    def savings_pct(self) -> float:
        if self.baseline_cost <= 0:
            return 0.0
        return (self.total_savings / self.baseline_cost) * 100

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "sent_input_tokens": self.sent_input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_hit": self.cache_hit,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "compression_savings_usd": round(self.compression_savings, 6),
            "cache_savings_usd": round(self.cache_savings, 6),
            "total_savings_usd": round(self.total_savings, 6),
            "baseline_cost_usd": round(self.baseline_cost, 6),
            "actual_cost_usd": round(self.actual_cost, 6),
            "savings_pct": round(self.savings_pct, 1),
            "metadata": self.metadata,
        }


class RequestAuditor:
    """In-memory bounded request audit tracker."""

    def __init__(self, max_recent: int = 1000):
        self._records: deque[RequestAudit] = deque(maxlen=max_recent)

    def record(self, audit: RequestAudit) -> None:
        if audit.timestamp <= 0:
            audit.timestamp = time.time()
        self._records.append(audit)

    def get_recent(self, n: int = 10) -> List[RequestAudit]:
        return list(self._records)[-n:]

    def filter(
        self,
        since: Optional[float] = None,
        model: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> List[RequestAudit]:
        results = list(self._records)
        if since:
            results = [r for r in results if r.timestamp >= since]
        if model:
            results = [r for r in results if model.lower() in r.model.lower()]
        if request_id:
            results = [r for r in results if r.request_id == request_id]
        return results

    def stats(self) -> dict:
        records = list(self._records)
        if not records:
            return {"total": 0, "cache_hits": 0, "avg_savings": 0.0}

        total = len(records)
        cache_hits = sum(1 for r in records if r.cache_hit)
        savings = [r.total_savings for r in records]
        compression_only = sum(1 for r in records if r.compression_savings > 0 and not r.cache_hit)
        no_savings = sum(1 for r in records if r.total_savings <= 0)

        return {
            "total": total,
            "cache_hits": cache_hits,
            "cache_hit_pct": round(cache_hits / total * 100, 1) if total else 0,
            "compression_only": compression_only,
            "no_savings": no_savings,
            "avg_savings": round(sum(savings) / total, 4) if total else 0,
            "median_savings": round(sorted(savings)[total // 2], 4) if total else 0,
            "total_savings": round(sum(savings), 4),
        }

    def to_csv(self, records: Optional[List[RequestAudit]] = None) -> str:
        if records is None:
            records = list(self._records)
        headers = [
            "request_id", "timestamp", "model", "input_tokens",
            "sent_input_tokens", "output_tokens", "cache_read_tokens",
            "cache_hit", "compression_savings_usd", "cache_savings_usd",
            "total_savings_usd", "savings_pct",
        ]
        lines = [",".join(headers)]
        for r in records:
            lines.append(",".join(str(v) for v in [
                r.request_id, r.timestamp, r.model, r.input_tokens,
                r.sent_input_tokens, r.output_tokens, r.cache_read_tokens,
                r.cache_hit, f"{r.compression_savings:.6f}", f"{r.cache_savings:.6f}",
                f"{r.total_savings:.6f}", f"{r.savings_pct:.1f}",
            ]))
        return "\n".join(lines)


def format_audit_report(records: List[RequestAudit]) -> str:
    """Format a human-readable audit report."""
    if not records:
        return "No requests found."

    lines = [
        "TokenPak Request Audit",
        "──────────────────────────────────────────",
        "",
    ]
    for i, r in enumerate(reversed(records), 1):
        from datetime import datetime
        ts = datetime.fromtimestamp(r.timestamp).strftime("%Y-%m-%d %H:%M:%S") if r.timestamp else "unknown"
        status_icon = "✅ CACHE HIT" if r.cache_hit else ("✅ 200" if r.status == 200 else f"❌ {r.status}")
        lines.append(f"Request #{i}:")
        lines.append(f"  Model:              {r.model}")
        lines.append(f"  Timestamp:          {ts}")
        lines.append(f"  Status:             {status_icon}")
        lines.append(f"")
        lines.append(f"  Input tokens:       {r.input_tokens:,}")
        lines.append(f"  After compression:  {r.sent_input_tokens:,} ({r.compression_tokens_saved:,} saved)")
        if r.cache_read_tokens:
            lines.append(f"  Cache read:         {r.cache_read_tokens:,} tokens")
        lines.append(f"")
        lines.append(f"  Cost breakdown:")
        lines.append(f"    Without TokenPak: ${r.baseline_cost:.4f}")
        if r.compression_savings > 0:
            lines.append(f"    Compression:     -${r.compression_savings:.4f}")
        if r.cache_savings > 0:
            lines.append(f"    Cache savings:   -${r.cache_savings:.4f}")
        lines.append(f"    ──────────────────")
        lines.append(f"    With TokenPak:    ${r.actual_cost:.4f}")
        lines.append(f"    💰 SAVED:        ${r.total_savings:.4f} ({r.savings_pct:.0f}% reduction)")
        lines.append(f"")
    return "\n".join(lines)
