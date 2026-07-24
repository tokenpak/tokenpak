# SPDX-License-Identifier: Apache-2.0
"""TokenPak Daily Savings Report Generator

Generates formatted daily summaries for TokenPak usage and savings.
Suitable for automated reporting via CLI, cron, or messaging.
"""

from __future__ import annotations

__all__ = (
    "DailySavingsData",
    "ModelCompressionRow",
    "generate_report",
)


import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal, TypedDict, cast


@dataclass
class ModelCompressionRow:
    """Per-model compression row for the daily report."""

    model: str
    request_count: int
    avg_compression_ratio: float  # final/raw; lower = more compression
    tokens_saved: int
    savings_amount: float


@dataclass
class DailySavingsData:
    """Daily savings summary data."""

    timestamp: str  # ISO format
    requests: int | Literal["unknown"]
    savings_amount: float | Literal["unknown"]
    savings_percent: float | Literal["unknown"]
    cache_hit_rate: float | Literal["unknown"]
    compression_percent: float | Literal["unknown"]
    top_model: str
    top_model_savings: float | Literal["unknown"]
    uptime_hours: int | Literal["unknown"]
    uptime_minutes: int | Literal["unknown"]
    errors: int | Literal["unknown"]
    estimated_monthly_rate: float | Literal["unknown"]
    model_compression: list[ModelCompressionRow] = field(default_factory=list)


class SavingsSummary(TypedDict):
    """Fields consumed from a one-day telemetry savings report."""

    total_cost: float | Literal["unknown"]
    estimated_without_compression: float | Literal["unknown"]
    savings_amount: float | Literal["unknown"]
    savings_pct: float | Literal["unknown"]
    cache_hit_rate: float | Literal["unknown"]


def _proxy_get(path: str, port: int | None = None) -> dict[str, object] | None:
    """Fetch JSON from running proxy. Returns None if unreachable."""
    import urllib.request as _urlreq

    port = port or int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
        payload = json.loads(resp.read())
        return cast(dict[str, object], payload) if isinstance(payload, dict) else None
    except Exception:
        return None


def _get_model_compression_breakdown() -> list[ModelCompressionRow]:
    """Fetch per-model compression breakdown from telemetry. Returns [] on error."""
    try:
        from tokenpak.telemetry.query_dsl import get_model_compression_breakdown

        rows = get_model_compression_breakdown(days=1)
        return [
            ModelCompressionRow(
                model=r.model,
                request_count=r.request_count,
                avg_compression_ratio=r.avg_compression_ratio,
                tokens_saved=r.tokens_saved,
                savings_amount=r.savings_amount,
            )
            for r in rows
        ]
    except Exception:
        return []


def _get_savings_report() -> SavingsSummary:
    """Get historical savings data from telemetry."""
    try:
        from tokenpak.telemetry.query_dsl import get_savings_report

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
            "total_cost": "unknown",
            "estimated_without_compression": "unknown",
            "savings_amount": "unknown",
            "savings_pct": "unknown",
            "cache_hit_rate": "unknown",
        }


def _calculate_data() -> DailySavingsData:
    """Collect live proxy stats and calculate daily summary."""
    health_payload = _proxy_get("/health")
    stats_payload = _proxy_get("/stats")
    cache_payload = _proxy_get("/cache-stats")
    health = health_payload if isinstance(health_payload, dict) else {}
    stats = stats_payload if isinstance(stats_payload, dict) else {}
    cache = cache_payload if isinstance(cache_payload, dict) else {}
    telemetry = _get_savings_report()

    # Uptime and request counters are canonical top-level /health fields.
    uptime_value = health.get("uptime_seconds")
    if not isinstance(uptime_value, (int, float)) or isinstance(uptime_value, bool):
        uptime_h: int | Literal["unknown"] = "unknown"
        uptime_m: int | Literal["unknown"] = "unknown"
    else:
        uptime_s = max(0.0, float(uptime_value))
        uptime_h = int(uptime_s // 3600)
        uptime_m = int((uptime_s % 3600) // 60)

    # Requests and errors
    requests_value = health.get("requests_total")
    errors_value = health.get("requests_errors")
    requests: int | Literal["unknown"] = (
        int(requests_value)
        if isinstance(requests_value, (int, float)) and not isinstance(requests_value, bool)
        else "unknown"
    )
    errors: int | Literal["unknown"] = (
        int(errors_value)
        if isinstance(errors_value, (int, float)) and not isinstance(errors_value, bool)
        else "unknown"
    )

    # Token counters live in the /stats session object, not /health.
    stats_session_value = stats.get("session")
    stats_session = stats_session_value if isinstance(stats_session_value, dict) else {}
    input_value = stats_session.get("input_tokens")
    saved_value = stats_session.get("saved_tokens")
    input_tokens = (
        float(input_value)
        if isinstance(input_value, (int, float)) and not isinstance(input_value, bool)
        else None
    )
    saved_tokens = (
        float(saved_value)
        if isinstance(saved_value, (int, float)) and not isinstance(saved_value, bool)
        else None
    )
    compression_pct: float | Literal["unknown"] = (
        saved_tokens / input_tokens * 100
        if input_tokens is not None and input_tokens > 0 and saved_tokens is not None
        else "unknown"
    )

    # Cache
    hits_value = cache.get("cache_hits")
    misses_value = cache.get("cache_misses")
    cache_hits = (
        float(hits_value)
        if isinstance(hits_value, (int, float)) and not isinstance(hits_value, bool)
        else None
    )
    cache_misses = (
        float(misses_value)
        if isinstance(misses_value, (int, float)) and not isinstance(misses_value, bool)
        else None
    )
    cache_total = (
        cache_hits + cache_misses if cache_hits is not None and cache_misses is not None else None
    )
    cache_hit_rate: float | Literal["unknown"] = (
        cache_hits / cache_total
        if cache_hits is not None and cache_total is not None and cache_total > 0
        else "unknown"
    )

    # Savings from telemetry
    savings_amount_value = telemetry.get("savings_amount")
    savings_percent_value = telemetry.get("savings_pct")
    savings_amount: float | Literal["unknown"] = (
        float(savings_amount_value)
        if isinstance(savings_amount_value, (int, float))
        and not isinstance(savings_amount_value, bool)
        else "unknown"
    )
    savings_percent: float | Literal["unknown"] = (
        float(savings_percent_value)
        if isinstance(savings_percent_value, (int, float))
        and not isinstance(savings_percent_value, bool)
        else "unknown"
    )

    # Top model (from telemetry or fallback)
    top_model = "unknown"
    top_model_savings: float | Literal["unknown"] = "unknown"
    try:
        from tokenpak.telemetry.query_dsl import get_model_usage

        usage = get_model_usage(days=1)
        if usage:
            # Find model with highest cost
            model_costs = {}
            for u in usage:
                # Estimate cost based on tokens (simplified)
                model_costs[u.model] = u.request_count
            if model_costs:
                top_model = max(model_costs, key=lambda model: model_costs[model])
                # No per-model savings attribution is available from this
                # request-count ranking, so do not assign the aggregate value.
    except Exception:
        pass

    # Estimated monthly rate
    if (
        isinstance(requests, int)
        and requests > 0
        and isinstance(savings_amount, float)
        and savings_amount > 0
    ):
        # Rough estimate: daily savings * 30 / time elapsed
        days_running = max(uptime_h / 24, 0.1) if uptime_h != "unknown" else 0.1
        daily_savings = savings_amount / max(days_running, 0.1)
        estimated_monthly = daily_savings * 30
    else:
        estimated_monthly: float | Literal["unknown"] = (
            0.0
            if isinstance(requests, int)
            and isinstance(savings_amount, float)
            and (requests == 0 or savings_amount == 0)
            else "unknown"
        )

    # Per-model compression breakdown
    model_compression = _get_model_compression_breakdown()

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
        model_compression=model_compression,
    )


def _format_compression_table_terminal(rows: list[ModelCompressionRow]) -> list[str]:
    """Format per-model compression breakdown as terminal lines."""
    if not rows:
        return ["  (no per-model compression data)"]
    lines = [
        "",
        "  Per-Model Compression Breakdown:",
        f"  {'Model':<30} {'Reqs':>6} {'Ratio':>6} {'Saved Tok':>10} {'Saved $':>9}",
        "  " + "─" * 65,
    ]
    for r in rows:
        ratio_pct = (
            f"{(1 - r.avg_compression_ratio) * 100:.1f}%"
            if r.avg_compression_ratio < 1.0
            else "0.0%"
        )
        lines.append(
            f"  {r.model:<30} {r.request_count:>6,} {ratio_pct:>6} {r.tokens_saved:>10,} {r.savings_amount:>9.4f}"
        )
    return lines


def _format_terminal(data: DailySavingsData) -> str:
    """Format as terminal-friendly output."""
    lines = [
        "📊 TokenPak Daily Report",
        "─" * 40,
        f"  Date:       {data.timestamp.split('T')[0]}",
        f"  Requests:   {data.requests:,}"
        if isinstance(data.requests, int)
        else "  Requests:   unknown",
        (
            f"  Saved:      ${data.savings_amount:.2f} ({data.savings_percent:.1f}%)"
            if isinstance(data.savings_amount, float) and isinstance(data.savings_percent, float)
            else "  Saved:      unknown"
        ),
        (
            f"  Cache Hit:  {data.cache_hit_rate * 100:.0f}%"
            if isinstance(data.cache_hit_rate, float)
            else "  Cache Hit:  unknown"
        ),
        (
            f"  Compression: {data.compression_percent:.1f}%"
            if isinstance(data.compression_percent, float)
            else "  Compression: unknown"
        ),
        f"  Top Model:  {data.top_model}",
        (
            f"  Uptime:     {data.uptime_hours}h {data.uptime_minutes:02d}m"
            if isinstance(data.uptime_hours, int) and isinstance(data.uptime_minutes, int)
            else "  Uptime:     unknown"
        ),
        f"  Errors:     {data.errors}",
        (
            f"  Monthly Rate: ${data.estimated_monthly_rate:.0f}/mo"
            if isinstance(data.estimated_monthly_rate, float)
            else "  Monthly Rate: unknown"
        ),
    ]
    lines.extend(_format_compression_table_terminal(data.model_compression or []))
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
        f"| Requests | {data.requests:,} |"
        if isinstance(data.requests, int)
        else "| Requests | unknown |",
        (
            f"| Savings | ${data.savings_amount:.2f} ({data.savings_percent:.1f}%) |"
            if isinstance(data.savings_amount, float) and isinstance(data.savings_percent, float)
            else "| Savings | unknown |"
        ),
        (
            f"| Cache Hit Rate | {data.cache_hit_rate * 100:.0f}% |"
            if isinstance(data.cache_hit_rate, float)
            else "| Cache Hit Rate | unknown |"
        ),
        (
            f"| Compression | {data.compression_percent:.1f}% |"
            if isinstance(data.compression_percent, float)
            else "| Compression | unknown |"
        ),
        f"| Top Model | {data.top_model} |",
        (
            f"| Uptime | {data.uptime_hours}h {data.uptime_minutes:02d}m |"
            if isinstance(data.uptime_hours, int) and isinstance(data.uptime_minutes, int)
            else "| Uptime | unknown |"
        ),
        f"| Errors | {data.errors} |",
        (
            f"| Est. Monthly | ${data.estimated_monthly_rate:.0f}/mo |"
            if isinstance(data.estimated_monthly_rate, float)
            else "| Est. Monthly | unknown |"
        ),
    ]
    rows = data.model_compression or []
    if rows:
        lines += [
            "",
            "### Per-Model Compression Breakdown",
            "",
            "| Model | Reqs | Compression | Tokens Saved | Saved $ |",
            "| ----- | ---: | ----------: | -----------: | ------: |",
        ]
        for r in rows:
            ratio_pct = (
                f"{(1 - r.avg_compression_ratio) * 100:.1f}%"
                if r.avg_compression_ratio < 1.0
                else "0.0%"
            )
            lines.append(
                f"| {r.model} | {r.request_count:,} | {ratio_pct} | {r.tokens_saved:,} | ${r.savings_amount:.4f} |"
            )
    else:
        lines += ["", "_No per-model compression data available._"]
    return "\n".join(lines)


def _format_json(data: DailySavingsData) -> dict[str, object]:
    """Format as JSON dict."""
    result = asdict(data)
    # model_compression is a list of ModelCompressionRow dataclasses; asdict handles them
    return cast(dict[str, object], result)


def generate_report(
    format: Literal["terminal", "markdown", "json"] = "terminal",
) -> str | dict[str, object]:
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
