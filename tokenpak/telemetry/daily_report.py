# SPDX-License-Identifier: Apache-2.0
"""TokenPak Daily Savings Report Generator

Generates formatted daily summaries for TokenPak usage and savings.
Suitable for automated reporting via CLI, cron, or messaging.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, Optional


@dataclass
class DailySavingsData:
    """Daily savings summary data."""

    timestamp: str  # ISO format
    requests: int
    savings_amount: float
    savings_percent: float
    cache_hit_rate: float
    compression_percent: float
    top_model: str
    top_model_savings: float
    uptime_hours: float
    uptime_minutes: int
    errors: int
    estimated_monthly_rate: float


def _proxy_get(path: str, port: Optional[int] = None) -> dict | None:
    """Fetch JSON from running proxy. Returns None if unreachable."""
    import urllib.request as _urlreq

    port = port or int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
        return json.loads(resp.read())
    except Exception:
        return None


def _get_savings_report() -> dict:
    """Get historical savings data from telemetry."""
    try:
        from .telemetry.query import get_savings_report

        report = get_savings_report(days=1)
        return {
            "total_cost": report.total_cost,
            "estimated_without_compression": report.estimated_without_compression,
            "savings_amount": report.savings_amount,
            "savings_pct": report.savings_pct,
            "cache_hit_rate": report.cache_hit_rate,
        }
    except Exception:
        return {
            "total_cost": 0.0,
            "estimated_without_compression": 0.0,
            "savings_amount": 0.0,
            "savings_pct": 0.0,
            "cache_hit_rate": 0.0,
        }


def _calculate_data() -> DailySavingsData:
    """Collect live proxy stats and calculate daily summary."""
    health = _proxy_get("/health") or {}
    stats = _proxy_get("/stats") or {}
    cache = _proxy_get("/cache-stats") or {}
    telemetry = _get_savings_report()

    # Extract stats
    health_stats = health.get("stats", {})
    uptime_s = time.time() - health_stats.get("start_time", time.time())
    uptime_h = int(uptime_s // 3600)
    uptime_m = int((uptime_s % 3600) // 60)

    # Requests and errors
    requests = stats.get("requests", 0)
    errors = stats.get("errors", 0)

    # Tokens
    input_tokens = stats.get("input_tokens", 0)
    saved_tokens = stats.get("saved_tokens", 0)
    compression_pct = (saved_tokens / input_tokens * 100) if input_tokens > 0 else 0

    # Cache
    cache_hits = cache.get("cache_hits", 0)
    cache_misses = cache.get("cache_misses", 0)
    cache_total = cache_hits + cache_misses
    cache_hit_rate = (cache_hits / cache_total) if cache_total > 0 else 0.0

    # Savings from telemetry
    savings_amount = telemetry.get("savings_amount", 0.0)
    savings_percent = telemetry.get("savings_pct", 0.0)

    # Top model (from telemetry or fallback)
    top_model = "unknown"
    top_model_savings = 0.0
    try:
        from .telemetry.query import get_model_usage

        usage = get_model_usage(days=1)
        if usage:
            # Find model with highest cost
            model_costs = {}
            for u in usage:
                # Estimate cost based on tokens (simplified)
                model_costs[u.model] = u.request_count
            if model_costs:
                top_model = max(model_costs, key=lambda k: model_costs.get(k, 0))
                top_model_savings = savings_amount  # Proxy: assume savings proportional
    except Exception:
        pass

    # Estimated monthly rate
    if requests > 0 and savings_amount > 0:
        # Rough estimate: daily savings * 30 / time elapsed
        days_running = max(uptime_h / 24, 0.1)
        daily_savings = savings_amount / max(days_running, 0.1)
        estimated_monthly = daily_savings * 30
    else:
        estimated_monthly = 0.0

    return DailySavingsData(
        timestamp=datetime.now().isoformat(),
        requests=requests,
        savings_amount=savings_amount,
        savings_percent=savings_percent,
        cache_hit_rate=cache_hit_rate,
        compression_percent=compression_pct,
        top_model=top_model,
        top_model_savings=top_model_savings,
        uptime_hours=uptime_h,
        uptime_minutes=uptime_m,
        errors=errors,
        estimated_monthly_rate=estimated_monthly,
    )


def _format_terminal(data: DailySavingsData) -> str:
    """Format as terminal-friendly output."""
    lines = [
        "📊 TokenPak Daily Report",
        "─" * 40,
        f"  Date:       {data.timestamp.split('T')[0]}",
        f"  Requests:   {data.requests:,}",
        f"  Saved:      ${data.savings_amount:.2f} ({data.savings_percent:.1f}%)",
        f"  Cache Hit:  {data.cache_hit_rate * 100:.0f}%",
        f"  Compression: {data.compression_percent:.1f}%",
        f"  Top Model:  {data.top_model}",
        f"  Uptime:     {data.uptime_hours}h {data.uptime_minutes:02d}m",
        f"  Errors:     {data.errors}",
        f"  Monthly Rate: ${data.estimated_monthly_rate:.0f}/mo",
    ]
    return "\n".join(lines)


def _format_markdown(data: DailySavingsData) -> str:
    """Format as markdown (suitable for Telegram/messaging)."""
    lines = [
        "## 📊 TokenPak Daily Report",
        "",
        f"**Date:** {data.timestamp.split('T')[0]}",
        "",
        "| Metric | Value |",
        "| ------ | ----- |",
        f"| Requests | {data.requests:,} |",
        f"| Savings | ${data.savings_amount:.2f} ({data.savings_percent:.1f}%) |",
        f"| Cache Hit Rate | {data.cache_hit_rate * 100:.0f}% |",
        f"| Compression | {data.compression_percent:.1f}% |",
        f"| Top Model | {data.top_model} |",
        f"| Uptime | {data.uptime_hours}h {data.uptime_minutes:02d}m |",
        f"| Errors | {data.errors} |",
        f"| Est. Monthly | ${data.estimated_monthly_rate:.0f}/mo |",
    ]
    return "\n".join(lines)


def _format_json(data: DailySavingsData) -> dict:
    """Format as JSON dict."""
    return asdict(data)


def generate_report(
    format: Literal["terminal", "markdown", "json"] = "terminal",
) -> str | dict:
    """Generate daily savings report in specified format.

    Args:
        format: Output format ('terminal', 'markdown', 'json')

    Returns:
        Formatted report string or dict
    """
    data = _calculate_data()

    if format == "markdown":
        return _format_markdown(data)
    elif format == "json":
        return _format_json(data)
    else:  # terminal
        return _format_terminal(data)
