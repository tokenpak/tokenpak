"""pricing.py — Model pricing rates and savings calculations.

Provides hardcoded rates for major AI models and utilities to compute
compression + cache savings from proxy stats.
"""

from __future__ import annotations

from typing import Optional

# Pricing per million tokens (input, cached, output)
# Format: {"input": price, "cached": price, "output": price}

MODEL_RATES = {
    "claude-opus-4-5": {"input": 15.0, "cached": 1.50, "output": 75.0},
    "claude-opus-4-6": {"input": 15.0, "cached": 1.50, "output": 75.0},
    "claude-sonnet-4-5": {"input": 3.0, "cached": 0.30, "output": 15.0},
    "claude-sonnet-4-6": {"input": 3.0, "cached": 0.30, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "cached": 0.08, "output": 4.0},
    "claude-haiku-4-6": {"input": 0.80, "cached": 0.08, "output": 4.0},
    "gpt-4o": {"input": 2.50, "cached": 1.25, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.60},
    "gpt-4-turbo": {"input": 10.0, "cached": 5.0, "output": 30.0},
}

# Default rates (use for unknown models)
DEFAULT_RATE = {"input": 3.0, "cached": 0.30, "output": 15.0}  # sonnet default


def get_rates(model: Optional[str] = None) -> dict:
    """Get pricing rates for a model. Falls back to DEFAULT_RATE if not found."""
    if not model:
        return DEFAULT_RATE
    return MODEL_RATES.get(model, DEFAULT_RATE)


def estimate_savings(stats: dict, model: Optional[str] = None) -> dict:
    """Calculate compression + cache savings from proxy stats.

    Args:
        stats: Dict with keys like:
            - tokens_raw: total input tokens (pre-compression)
            - tokens_saved: tokens removed by compression
            - cache_read_tokens: tokens served from cache
            - cache_write_tokens: tokens written to cache
            - model (optional): model name override

        model: Optional model name override. If not provided, uses stats["model"].

    Returns:
        Dict with:
            - compression_tokens_saved: tokens removed by compression
            - compression_cost_saved: estimated $ saved by compression
            - cache_hit_rate: percentage of requests from cache (if available)
            - cache_tokens_saved: tokens served from cache instead of fresh
            - cache_cost_saved: estimated $ saved by cache hits
            - total_tokens_saved: compression + cache combined
            - total_cost_saved: combined $ savings
            - cost_without_tokenpak: estimated cost if no compression/cache
            - cost_with_tokenpak: estimated cost with compression/cache
            - reduction_percent: % cost reduction
    """
    # Determine model to use
    model_name = model or stats.get("model")
    rates = get_rates(model_name)

    # Extract stats with defaults
    tokens_raw = stats.get("tokens_raw", stats.get("input_tokens", 0))
    tokens_saved_compression = stats.get("tokens_saved", 0)
    cache_read_tokens = stats.get("cache_read_tokens", 0)
    cache_write_tokens = stats.get("cache_write_tokens", 0)
    session_requests = stats.get("session_requests", 0)

    # Compression savings
    compression_cost_saved = (tokens_saved_compression / 1_000_000) * rates["input"]

    # Cache savings: cache_read_tokens are served at cached rate instead of input rate
    # The "savings" is the difference between input rate and cached rate
    cache_cost_saved = (cache_read_tokens / 1_000_000) * (rates["input"] - rates["cached"])

    # Calculate "without TokenPak" cost (all tokens at input rate)
    cost_without = (tokens_raw / 1_000_000) * rates["input"]

    # Calculate "with TokenPak" cost
    # After compression, remaining tokens + cache hits at cached rate
    tokens_after_compression = tokens_raw - tokens_saved_compression
    tokens_from_cache = cache_read_tokens
    tokens_fresh = tokens_after_compression - tokens_from_cache

    cost_with = (tokens_fresh / 1_000_000) * rates["input"] + (
        tokens_from_cache / 1_000_000
    ) * rates["cached"]

    # Total savings
    total_cost_saved = cost_without - cost_with
    reduction_pct = (total_cost_saved / cost_without * 100.0) if cost_without > 0 else 0.0

    # Cache hit rate
    cache_hit_rate = (
        (cache_read_tokens / tokens_after_compression * 100.0)
        if tokens_after_compression > 0
        else 0.0
    )

    return {
        "compression_tokens_saved": tokens_saved_compression,
        "compression_cost_saved": round(compression_cost_saved, 4),
        "cache_hit_rate": round(cache_hit_rate, 1),
        "cache_tokens_saved": cache_read_tokens,
        "cache_cost_saved": round(cache_cost_saved, 4),
        "total_tokens_saved": tokens_saved_compression + cache_read_tokens,
        "total_cost_saved": round(total_cost_saved, 4),
        "cost_without_tokenpak": round(cost_without, 4),
        "cost_with_tokenpak": round(cost_with, 4),
        "reduction_percent": round(reduction_pct, 1),
    }


def calculate_request_cost(
    model: str,
    input_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    """Calculate actual cost for a request routed through TokenPak."""
    rates = get_rates(model)
    input_rate = rates.get("input", 3.0)
    output_rate = rates.get("output", 15.0)
    cache_rate = rates.get("cached", input_rate * 0.1)

    cost = (input_tokens / 1_000_000) * input_rate
    cost += (cache_read_tokens / 1_000_000) * cache_rate
    cost += (cache_creation_tokens / 1_000_000) * input_rate * 1.25
    cost += (output_tokens / 1_000_000) * output_rate
    return round(cost, 6)


def calculate_request_cost_baseline(
    model: str, total_input_tokens: int, output_tokens: int = 0
) -> float:
    """Calculate what a request would cost WITHOUT TokenPak (no cache, no compression)."""
    rates = get_rates(model)
    input_rate = rates.get("input", 3.0)
    output_rate = rates.get("output", 15.0)

    cost = (total_input_tokens / 1_000_000) * input_rate
    cost += (output_tokens / 1_000_000) * output_rate
    return round(cost, 6)


def get_price(model: str, direction: str = "input") -> float:
    """Get per-million-token price for a model and direction (input/output/cached)."""
    rates = get_rates(model)
    if direction == "output":
        return rates.get("output", 15.0)
    elif direction == "cached":
        return rates.get("cached", 0.30)
    return rates.get("input", 3.0)


def calculate_fleet_savings(db_path: str, period: Optional[str] = None) -> dict:
    """Calculate real dollar savings from the monitor DB using per-model rates.

    Args:
        db_path: Path to monitor.db SQLite file.
        period: Time window — "1h", "24h", "7d", "30d", or None (all-time).

    Returns:
        Dict with total_requests, cost_without_tokenpak, cost_with_tokenpak,
        total_saved, reduction_percent, per_model list, and velocity sub-dict.
    """
    import sqlite3
    from datetime import datetime, timedelta, timezone

    # Build period filter
    period_map = {"1h": timedelta(hours=1), "24h": timedelta(hours=24),
                  "7d": timedelta(days=7), "30d": timedelta(days=30)}
    where_clause = ""
    if period and period in period_map:
        cutoff = (datetime.now(timezone.utc) - period_map[period]).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        where_clause = f"WHERE timestamp >= '{cutoff}'"

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        rows = cur.execute(
            f"""
            SELECT model,
                   COUNT(*) AS requests,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                   COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                   COALESCE(SUM(compressed_tokens), 0) AS compressed_tokens
            FROM requests
            {where_clause}
            GROUP BY model
            ORDER BY requests DESC
            """
        ).fetchall()
    except Exception:
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    per_model = []
    total_cost_without = 0.0
    total_cost_with = 0.0
    total_requests = 0

    for row in rows:
        rates = get_rates(row["model"])
        inp = row["input_tokens"]
        out = row["output_tokens"]
        cache_r = row["cache_read_tokens"]
        cache_c = row["cache_creation_tokens"]

        # "Without" cost: all tokens (input + cache_read) at full input rate + output
        cost_without = (
            (inp + cache_r) / 1_000_000 * rates["input"]
            + out / 1_000_000 * rates["output"]
            + cache_c / 1_000_000 * rates["input"] * 1.25
        )

        # "With" cost: input at input rate + cache_read at cached rate + output + cache_creation
        cost_with = (
            inp / 1_000_000 * rates["input"]
            + cache_r / 1_000_000 * rates["cached"]
            + out / 1_000_000 * rates["output"]
            + cache_c / 1_000_000 * rates["input"] * 1.25
        )

        saved = cost_without - cost_with
        total_input_plus_cache = inp + cache_r
        cache_hit_pct = (
            cache_r / total_input_plus_cache * 100.0 if total_input_plus_cache > 0 else 0.0
        )
        reduction_pct = saved / cost_without * 100.0 if cost_without > 0 else 0.0

        per_model.append({
            "model": row["model"],
            "requests": row["requests"],
            "cost": round(cost_with, 4),
            "cost_without": round(cost_without, 4),
            "saved": round(saved, 4),
            "cache_hit_percent": round(cache_hit_pct, 1),
            "reduction_percent": round(reduction_pct, 1),
        })

        total_cost_without += cost_without
        total_cost_with += cost_with
        total_requests += row["requests"]

    total_saved = total_cost_without - total_cost_with
    reduction_pct = total_saved / total_cost_without * 100.0 if total_cost_without > 0 else 0.0

    # Velocity: compute last_hour, last_24h, all_time regardless of period filter
    def _saved_for_window(window_clause: str) -> float:
        try:
            conn2 = sqlite3.connect(db_path)
            conn2.row_factory = sqlite3.Row
            wrows = conn2.execute(
                f"""
                SELECT model,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                       COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens
                FROM requests
                {window_clause}
                GROUP BY model
                """
            ).fetchall()
            conn2.close()
        except Exception:
            return 0.0
        total = 0.0
        for r in wrows:
            rt = get_rates(r["model"])
            cw = (r["cache_read_tokens"] / 1_000_000) * (rt["input"] - rt["cached"])
            total += cw
        return round(total, 4)

    now = datetime.now(timezone.utc)
    cutoff_1h = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")

    velocity = {
        "last_hour_saved": _saved_for_window(f"WHERE timestamp >= '{cutoff_1h}'"),
        "last_24h_saved": _saved_for_window(f"WHERE timestamp >= '{cutoff_24h}'"),
        "all_time_saved": _saved_for_window(""),
    }

    return {
        "period": period or "all-time",
        "total_requests": total_requests,
        "cost_without_tokenpak": round(total_cost_without, 4),
        "cost_with_tokenpak": round(total_cost_with, 4),
        "total_saved": round(total_saved, 4),
        "reduction_percent": round(reduction_pct, 1),
        "per_model": per_model,
        "velocity": velocity,
    }


def calculate_savings_breakdown(per_model_data: list) -> dict:
    """Break down savings by type: cache optimization vs token compression.

    Args:
        per_model_data: The per_model list from calculate_fleet_savings().

    Returns:
        Dict with cache_optimization, token_compression, and total savings in $.
    """
    # All savings in per_model are from cache hits (cache_read at cached vs input rate).
    # Compression savings are not separately tracked in the current DB schema
    # (compressed_tokens column is present but represents tokens after compression).
    # We attribute all measurable savings to cache_optimization.
    cache_savings = sum(entry.get("saved", 0.0) for entry in per_model_data)
    compression_savings = 0.0  # would need pre/post compression token counts per request

    return {
        "cache_optimization": round(cache_savings, 4),
        "token_compression": round(compression_savings, 4),
        "total": round(cache_savings + compression_savings, 4),
    }


def calculate_savings_from_proxy_stats(stats: dict, by_model: dict) -> dict:
    """Compute savings summary from proxy session stats and per-model breakdown.

    Parameters
    ----------
    stats : dict
        Aggregate session stats (keys: requests, input_tokens, sent_input_tokens,
        saved_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
        cost, cache_hits, cache_misses, …).
    by_model : dict
        Per-model breakdown (model name → same keys as stats).

    Returns
    -------
    dict with keys:
        cost_without_tokenpak, cost_with_tokenpak, total_saved,
        cache_saved, compression_saved, routing_saved,
        total_saved_pct, cache_hit_rate, total_requests, per_model.
    """
    requests = stats.get("requests", 0)
    cache_hits = stats.get("cache_hits", 0)
    cache_misses = stats.get("cache_misses", requests)
    total_reqs = requests or 1
    cache_hit_rate = cache_hits / total_reqs

    input_tokens = stats.get("input_tokens", 0)
    sent_input_tokens = stats.get("sent_input_tokens", input_tokens)
    saved_tokens = stats.get("saved_tokens", input_tokens - sent_input_tokens)
    output_tokens = stats.get("output_tokens", 0)
    cost_with = stats.get("cost", 0.0)

    # Estimate cost without tokenpak from raw input tokens
    default_model = "claude-sonnet-4-6"
    in_rate = MODEL_RATES.get(default_model, DEFAULT_RATE).get("input", DEFAULT_RATE)
    out_rate = MODEL_RATES.get(default_model, DEFAULT_RATE).get("output", DEFAULT_RATE)
    cost_without = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000

    # Estimate per-component savings
    compression_saved = (saved_tokens * in_rate) / 1_000_000 if saved_tokens else 0.0
    cache_saved = max(0.0, cost_without - cost_with - compression_saved)
    routing_saved = 0.0  # routing savings not tracked at this level

    total_saved = max(0.0, cost_without - cost_with)
    total_saved_pct = (total_saved / cost_without * 100) if cost_without > 0 else 0.0

    # Per-model breakdown
    per_model = {}
    for model, mstats in by_model.items():
        m_in = mstats.get("input_tokens", 0)
        m_out = mstats.get("output_tokens", 0)
        m_in_rate = MODEL_RATES.get(model, {}).get("input", DEFAULT_RATE)
        m_out_rate = MODEL_RATES.get(model, {}).get("output", DEFAULT_RATE)
        m_cost_without = (m_in * m_in_rate + m_out * m_out_rate) / 1_000_000
        m_cost_with = mstats.get("cost", 0.0)
        per_model[model] = {
            "cost_without_tokenpak": m_cost_without,
            "cost_with_tokenpak": m_cost_with,
            "total_saved": max(0.0, m_cost_without - m_cost_with),
        }

    return {
        "cost_without_tokenpak": cost_without,
        "cost_with_tokenpak": cost_with,
        "total_saved": total_saved,
        "cache_saved": cache_saved,
        "compression_saved": compression_saved,
        "routing_saved": routing_saved,
        "total_saved_pct": total_saved_pct,
        "cache_hit_rate": cache_hit_rate,
        "total_requests": requests,
        "per_model": per_model,
    }
