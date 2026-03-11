"""savings command — show token compression savings across periods.

Usage:
    tokenpak savings                    # show 24h savings (default)
    tokenpak savings --period 7d        # show 7-day savings
    tokenpak savings --period 30d       # show 30-day savings
    tokenpak savings --verbose          # per-model breakdown
    tokenpak savings --json             # machine-readable output

Flags:
    --period {24h,7d,30d}   Time window (default: 24h)
    --verbose               Show per-model compression breakdown
    --json                  Output as machine-readable JSON
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _period_to_days(period: str) -> int:
    """Convert period string to days."""
    period = period.lower().strip()
    if period in ("24h", "1d", "today"):
        return 1
    if period in ("7d", "week"):
        return 7
    if period in ("30d", "month"):
        return 30
    try:
        return int(period.rstrip("d"))
    except ValueError:
        return 1


def _fmt_tokens(n: int) -> str:
    """Format token count with commas."""
    return f"{n:,}"


def _fmt_pct(ratio: float) -> str:
    """Format compression ratio as percentage."""
    return f"{ratio * 100:.1f}%"


def _fmt_cost(c: float) -> str:
    """Format cost as USD."""
    if c < 0.01:
        return f"${c:.4f}"
    if c < 1.0:
        return f"${c:.3f}"
    return f"${c:.2f}"


SEP = "────────────────────────"


# ---------------------------------------------------------------------------
# Data queries using anon_metrics
# ---------------------------------------------------------------------------


def _query_metrics_summary(days: int) -> dict:
    """Aggregate metrics from anon_metrics store for the period.
    
    Returns dict with:
    - input_tokens_raw: total input (before compression)
    - input_tokens_compressed: total input (after compression)
    - tokens_saved: total tokens saved by compression
    - compression_ratio: overall compression ratio
    - avg_latency_ms: average latency
    - request_count: number of requests
    - per_model: dict of per-model stats
    """
    from tokenpak.telemetry.anon_metrics import get_store
    from datetime import date
    
    store = get_store()
    history = store.history(days=days)
    
    if not history:
        return {
            "input_tokens_raw": 0,
            "input_tokens_compressed": 0,
            "tokens_saved": 0,
            "compression_ratio": 0.0,
            "avg_latency_ms": 0.0,
            "request_count": 0,
            "per_model": {},
        }
    
    total_input = sum(r.input_tokens for r in history)
    total_saved = sum(r.tokens_saved for r in history)
    total_output = sum(r.output_tokens for r in history)
    total_latency = sum(r.latency_ms for r in history)
    
    # Per-model breakdown
    per_model = {}
    for rec in history:
        if rec.model not in per_model:
            per_model[rec.model] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "tokens_saved": 0,
                "requests": 0,
                "avg_latency_ms": 0.0,
            }
        m = per_model[rec.model]
        m["input_tokens"] += rec.input_tokens
        m["output_tokens"] += rec.output_tokens
        m["tokens_saved"] += rec.tokens_saved
        m["requests"] += 1
        m["avg_latency_ms"] += rec.latency_ms
    
    # Calculate per-model average latency
    for model in per_model:
        count = per_model[model]["requests"]
        if count > 0:
            per_model[model]["avg_latency_ms"] /= count
            per_model[model]["compression_ratio"] = (
                per_model[model]["tokens_saved"] / per_model[model]["input_tokens"]
                if per_model[model]["input_tokens"] > 0
                else 0.0
            )
    
    return {
        "input_tokens_raw": total_input,
        "input_tokens_compressed": total_input - total_saved,
        "tokens_saved": total_saved,
        "compression_ratio": total_saved / total_input if total_input > 0 else 0.0,
        "avg_latency_ms": total_latency / len(history) if history else 0.0,
        "request_count": len(history),
        "per_model": per_model,
    }


def _estimate_cost_saved(tokens_saved: int, model: str = "gpt-4o") -> float:
    """Rough estimate of cost saved by tokens (input tokens saved).
    
    Uses approximate rates:
    - gpt-4o: $0.0025/1K input tokens
    - claude-sonnet: $0.003/1K input tokens
    - claude-haiku: $0.00025/1K input tokens
    - Default fallback: $0.001/1K
    """
    rates_per_1k = {
        "gpt-4o": 0.0025,
        "gpt-4-turbo": 0.01,
        "claude-sonnet": 0.003,
        "claude-opus": 0.015,
        "claude-haiku": 0.00025,
        "gemini-2-flash": 0.0001,
        "llama": 0.0005,
    }
    
    # Match prefix
    rate = rates_per_1k.get("gpt-4o")  # default
    for model_prefix, r in rates_per_1k.items():
        if model.lower().startswith(model_prefix.lower()):
            rate = r
            break
    
    return (tokens_saved / 1000) * rate


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _print_summary(days: int, period_str: str, data: dict) -> None:
    """Print human-readable savings summary."""
    print(f"TOKENPAK  |  Compression Savings ({period_str})\n{SEP}\n")
    
    if data["request_count"] == 0:
        print("  No compression metrics recorded yet.")
        print()
        return
    
    # Main metrics
    print(f"  {'Raw Input Tokens:':<25}{_fmt_tokens(data['input_tokens_raw'])}")
    print(f"  {'Compressed Input:':<25}{_fmt_tokens(data['input_tokens_compressed'])}")
    print(f"  {'Tokens Saved:':<25}{_fmt_tokens(data['tokens_saved'])}")
    print(f"  {'Reduction %:':<25}{_fmt_pct(data['compression_ratio'])}")
    print()
    
    # Cost estimate
    estimated_saved = _estimate_cost_saved(data["tokens_saved"])
    print(f"  {'Estimated $ Saved (24h):':<25}{_fmt_cost(estimated_saved)}")
    print()
    
    # Performance
    print(f"  {'Requests:':<25}{data['request_count']:,}")
    print(f"  {'Avg Latency:':<25}{data['avg_latency_ms']:.1f}ms")
    print()


def _print_verbose(days: int, period_str: str, data: dict) -> None:
    """Print per-model breakdown."""
    _print_summary(days, period_str, data)
    
    if not data["per_model"]:
        return
    
    print("Per-Model Breakdown:")
    print("─" * 80)
    
    # Header
    print(
        f"  {'Model':<30} {'Raw Input':>12} {'Saved':>10} {'Reduction':>10} {'Requests':>8}"
    )
    print(f"  {'─'*30} {'─'*12} {'─'*10} {'─'*10} {'─'*8}")
    
    # Rows (sorted by tokens saved, descending)
    sorted_models = sorted(
        data["per_model"].items(),
        key=lambda x: x[1]["tokens_saved"],
        reverse=True,
    )
    for model, stats in sorted_models:
        model_display = model[:30] if model else "unknown"
        print(
            f"  {model_display:<30} "
            f"{_fmt_tokens(stats['input_tokens']):>12} "
            f"{_fmt_tokens(stats['tokens_saved']):>10} "
            f"{_fmt_pct(stats['compression_ratio']):>10} "
            f"{stats['requests']:>8}"
        )
    
    print()


def _print_json(data: dict) -> None:
    """Print machine-readable JSON output."""
    # Prepare for JSON serialization
    output = {
        "input_tokens_raw": data["input_tokens_raw"],
        "input_tokens_compressed": data["input_tokens_compressed"],
        "tokens_saved": data["tokens_saved"],
        "compression_ratio": round(data["compression_ratio"], 4),
        "avg_latency_ms": round(data["avg_latency_ms"], 2),
        "request_count": data["request_count"],
        "per_model": {},
    }
    
    for model, stats in data["per_model"].items():
        output["per_model"][model] = {
            "input_tokens": stats["input_tokens"],
            "output_tokens": stats["output_tokens"],
            "tokens_saved": stats["tokens_saved"],
            "compression_ratio": round(stats.get("compression_ratio", 0.0), 4),
            "requests": stats["requests"],
            "avg_latency_ms": round(stats["avg_latency_ms"], 2),
        }
    
    print(json.dumps(output, indent=2))


# ---------------------------------------------------------------------------
# Main subcommands
# ---------------------------------------------------------------------------


def cmd_show(args=None) -> None:
    """Show savings summary (default command)."""
    period_str = getattr(args, "period", "24h") or "24h"
    days = _period_to_days(period_str)
    verbose = getattr(args, "verbose", False)
    output_json = getattr(args, "json", False)
    
    data = _query_metrics_summary(days)
    
    if output_json:
        _print_json(data)
    elif verbose:
        _print_verbose(days, period_str, data)
    else:
        _print_summary(days, period_str, data)


# ---------------------------------------------------------------------------
# Click interface (optional, for Click-based CLI)
# ---------------------------------------------------------------------------

try:
    import click

    @click.group("savings")
    def savings_group():
        """Show compression savings and token reduction metrics."""
        pass

    @savings_group.command("show")
    @click.option("--period", default="24h", help="Time window (24h, 7d, 30d)")
    @click.option("--verbose", is_flag=True, help="Per-model breakdown")
    @click.option("--json", is_flag=True, help="Machine-readable JSON output")
    def savings_show_cmd(period, verbose, json):
        """Show compression savings summary."""

        class _Args:
            pass

        a = _Args()
        a.period = period
        a.verbose = verbose
        a.json = json
        cmd_show(a)

except ImportError:
    pass
