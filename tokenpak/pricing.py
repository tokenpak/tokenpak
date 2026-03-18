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
    
    cost_with = (tokens_fresh / 1_000_000) * rates["input"] + (tokens_from_cache / 1_000_000) * rates["cached"]
    
    # Total savings
    total_cost_saved = cost_without - cost_with
    reduction_pct = (total_cost_saved / cost_without * 100.0) if cost_without > 0 else 0.0
    
    # Cache hit rate
    cache_hit_rate = (cache_read_tokens / tokens_after_compression * 100.0) if tokens_after_compression > 0 else 0.0
    
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


def calculate_request_cost(model: str, input_tokens: int, cache_read_tokens: int = 0, cache_creation_tokens: int = 0, output_tokens: int = 0) -> float:
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


def calculate_request_cost_baseline(model: str, total_input_tokens: int, output_tokens: int = 0) -> float:
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


def calculate_savings_from_proxy_stats(stats: dict, by_model: dict) -> dict:
    """Calculate dollar savings broken down by source using live proxy stats.

    Args:
        stats: Session stats dict from /stats endpoint (``session`` key or top-level).
        by_model: Per-model breakdown dict from /stats ``by_model`` key.

    Returns:
        Dict with keys: cost_without_tokenpak, cost_with_tokenpak, total_saved,
        cache_saved, compression_saved, routing_saved, total_saved_pct,
        cache_hit_rate, total_requests, per_model.
    """
    # Accept either /stats shape: top-level or nested under "session"
    s = stats.get("session", stats)
    total_requests = s.get("requests", 0)
    input_tokens = s.get("input_tokens", 0)
    saved_tokens = s.get("saved_tokens", 0)  # compression-only
    output_tokens = s.get("output_tokens", 0)
    cache_read_tokens = s.get("cache_read_tokens", 0)
    actual_cost = s.get("cost", 0.0)
    cache_hits = s.get("cache_hits", 0)
    cache_misses = s.get("cache_misses", 0)
    total_cache_decisions = cache_hits + cache_misses
    cache_hit_rate = (cache_hits / total_cache_decisions * 100.0) if total_cache_decisions > 0 else 0.0

    # Estimate baseline cost per model
    cost_without = 0.0
    per_model_rows = []
    for model_name, mstats in (by_model or {}).items():
        rates = get_rates(model_name)
        m_input = mstats.get("input_tokens", 0)
        m_output = mstats.get("output_tokens", 0)
        m_cost_actual = mstats.get("cost", 0.0)
        m_cache_read = mstats.get("cache_read_tokens", 0)
        m_requests = mstats.get("requests", 0)

        # Baseline = ALL tokens at full rates (input + cache_read = what would
        # have been charged without caching; cache_read are tokens that were
        # served from Anthropic's prompt cache instead of being billed at full rate)
        m_total_input = m_input + m_cache_read
        m_cost_baseline = (
            (m_total_input / 1_000_000) * rates["input"]
            + (m_output / 1_000_000) * rates["output"]
        )
        cost_without += m_cost_baseline

        # Cache hit rate: fraction of total input volume served from cache
        m_cache_hit_rate = 0.0
        if m_total_input > 0:
            m_cache_hit_rate = (m_cache_read / m_total_input) * 100.0

        per_model_rows.append({
            "model": model_name,
            "requests": m_requests,
            "cost": round(m_cost_actual, 2),
            "cost_baseline": round(m_cost_baseline, 2),
            "cache_read_tokens": m_cache_read,
            "cache_hit_rate": round(m_cache_hit_rate, 0),
        })

    per_model_rows.sort(key=lambda r: r["cost"], reverse=True)

    total_saved = max(cost_without - actual_cost, 0.0)
    total_saved_pct = (total_saved / cost_without * 100.0) if cost_without > 0 else 0.0

    # Decompose savings sources using per-model rates for accuracy
    # Cache saved = cache_read_tokens billed at cached rate instead of full rate
    cache_saved_raw = 0.0
    compression_saved_raw = 0.0
    for model_name, mstats in (by_model or {}).items():
        rates = get_rates(model_name)
        m_cache_read = mstats.get("cache_read_tokens", 0)
        # Cache savings: tokens served from cache at cached_rate vs full input_rate
        cache_saved_raw += (m_cache_read / 1_000_000) * (rates["input"] - rates["cached"])

    # Compression savings: tokens eliminated before sending (saved_tokens)
    # Use weighted average input rate across all models
    if input_tokens > 0 and cost_without > 0:
        effective_input_rate = (cost_without / ((input_tokens + cache_read_tokens) / 1_000_000))
    else:
        effective_input_rate = DEFAULT_RATE["input"]
    compression_saved_raw = (saved_tokens / 1_000_000) * effective_input_rate

    # Routing savings = residual (model downgrades, smarter routing choices)
    cache_saved = min(cache_saved_raw, total_saved)
    compression_saved = min(compression_saved_raw, total_saved - cache_saved)
    routing_saved = max(total_saved - cache_saved - compression_saved, 0.0)

    return {
        "cost_without_tokenpak": round(cost_without, 2),
        "cost_with_tokenpak": round(actual_cost, 2),
        "total_saved": round(total_saved, 2),
        "cache_saved": round(cache_saved, 2),
        "compression_saved": round(compression_saved, 2),
        "routing_saved": round(routing_saved, 2),
        "total_saved_pct": round(total_saved_pct, 1),
        "cache_hit_rate": round(cache_hit_rate, 1),
        "total_requests": total_requests,
        "per_model": per_model_rows,
    }
