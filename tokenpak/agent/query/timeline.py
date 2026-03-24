"""tokenpak/agent/query/timeline.py — Hourly Cost Time-Series Bucketing

This module provides hourly bucketing of telemetry data for timeline visualization.
Supports cost breakdown by model and time-based aggregation.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _parse_timestamp(ts: str | int | float) -> datetime:
    """Parse timestamp in multiple formats (ISO8601, ms epoch, unix epoch).
    
    Handles:
    - ISO8601 strings: "2026-03-16T19:30:00Z"
    - Unix epoch seconds: 1771725600
    - Unix epoch milliseconds: 1771725600000
    """
    if isinstance(ts, (int, float)):
        # Assume milliseconds if > 10^10, else seconds
        ts_sec = ts / 1000.0 if ts > 10**10 else float(ts)
        return datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    
    # ISO8601 string
    try:
        s = str(ts).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Cannot parse timestamp: {ts}") from e


def _cost_for_tokens(model: str, tokens: int, cache_hit: bool = False) -> float:
    """Estimate cost based on model and token count.
    
    Pricing per 1M tokens (2024 rates):
    - claude-opus-4-5: $15 input, $60 output
    - claude-sonnet-4-6: $3 input, $15 output
    - gpt-4-turbo: $10 input, $30 output
    - gpt-3.5-turbo: $0.50 input, $1.50 output
    
    Cache hits get 90% discount on input.
    """
    rates = {
        "claude-opus-4-5": (15e-6, 60e-6),
        "claude-sonnet-4-6": (3e-6, 15e-6),
        "gpt-4-turbo": (10e-6, 30e-6),
        "gpt-4": (3e-6, 6e-6),
        "gpt-3.5-turbo": (0.5e-6, 1.5e-6),
    }
    
    # Estimate 30% input, 70% output for this calculation
    input_rate, output_rate = rates.get(model, (1e-6, 1e-6))
    input_tokens = int(tokens * 0.3)
    output_tokens = int(tokens * 0.7)
    
    cost = input_tokens * input_rate + output_tokens * output_rate
    
    # Cache hit: 90% discount on input
    if cache_hit:
        cost -= input_tokens * input_rate * 0.9
    
    return max(cost, 0.0001)  # Minimum cost


class TimelineGenerator:
    """Generate hourly buckets of cost/token data from entries."""
    
    def __init__(self):
        pass
    
    def hourly_buckets(
        self,
        entries: list[dict[str, Any]],
        start_hour: Optional[datetime] = None,
        num_hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Bucket entries by hour.
        
        Args:
            entries: List of telemetry entries
            start_hour: Start hour for first bucket (defaults to current hour UTC)
            num_hours: Number of hours to return (default 24)
        
        Returns:
            List of hourly buckets in chronological order starting from start_hour: 
            [{hour, cost, requests, models: {model: cost}}, ...]
        """
        if not entries and num_hours == 0:
            return []
        
        start_dt = start_hour or datetime.now(tz=timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        
        buckets: dict[str, dict[str, Any]] = {}
        
        for entry in entries:
            try:
                ts_str = entry.get("timestamp", "")
                ts_dt = _parse_timestamp(ts_str)
            except (ValueError, KeyError):
                logger.warning("Skipping entry with invalid timestamp: %s", entry.get("timestamp"))
                continue
            
            # Convert to UTC and bucket to the hour
            ts_utc = ts_dt.astimezone(timezone.utc) if ts_dt.tzinfo else ts_dt.replace(tzinfo=timezone.utc)
            hour_dt = ts_utc.replace(minute=0, second=0, microsecond=0)
            hour_key = hour_dt.isoformat()
            
            if hour_key not in buckets:
                buckets[hour_key] = {
                    "hour": hour_key,
                    "cost": 0.0,
                    "requests": 0,
                    "models": {},  # {model: cost}
                }
            
            bucket = buckets[hour_key]
            bucket["requests"] += 1
            
            # Calculate cost
            model = entry.get("model", "unknown")
            tokens = entry.get("tokens") or entry.get("total_tokens", 0)
            cache_tokens = (entry.get("extra") or {}).get("cache_tokens", 0)
            cache_hit = cache_tokens > 0
            
            cost = _cost_for_tokens(model, tokens, cache_hit)
            bucket["cost"] += cost
            bucket["models"][model] = bucket["models"].get(model, 0.0) + cost
        
        # Build result in chronological order (start to end)
        result = []
        current = start_dt
        for _ in range(num_hours):
            hour_key = current.isoformat()
            bucket = buckets.get(hour_key, {
                "hour": hour_key,
                "cost": 0.0,
                "requests": 0,
                "models": {},
            })
            result.append(bucket)
            current += timedelta(hours=1)
        
        return result
    
    def model_breakdown(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, float]:
        """Calculate total cost by model.
        
        Returns: {model: total_cost}
        """
        breakdown: dict[str, float] = defaultdict(float)
        
        for entry in entries:
            model = entry.get("model", "unknown")
            tokens = entry.get("tokens") or entry.get("total_tokens", 0)
            cache_tokens = (entry.get("extra") or {}).get("cache_tokens", 0)
            cache_hit = cache_tokens > 0
            
            cost = _cost_for_tokens(model, tokens, cache_hit)
            breakdown[model] += cost
        
        return dict(breakdown)
