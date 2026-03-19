"""TokenPak Agent Demo Command — visualize the compression pipeline + seed demo data."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

from tokenpak.telemetry.models import TelemetryEvent, Usage, Cost
from .collector import TelemetryCollector


def run_demo(request_id: Optional[str] = None) -> str:
    """Render a demo pipeline breakdown using synthetic data.

    Returns a formatted string suitable for terminal output.
    Used by `tokenpak demo` CLI command.
    """
    req, sess = TelemetryCollector.create_demo_stats()

    lines = [
        "",
        "╔══════════════════════════════════════════════╗",
        "║          TokenPak Compression Pipeline        ║",
        "╠══════════════════════════════════════════════╣",
        f"║  Stage 1 — Ingest      {req.input_tokens_raw:>6,} tokens          ║",
        "║  Stage 2 — Segment     split into blocks      ║",
        "║  Stage 3 — Deduplicate remove repeating segs  ║",
        "║  Stage 4 — Compress    apply recipe rules     ║",
        f"║  Stage 5 — Emit        {req.input_tokens_sent:>6,} tokens          ║",
        "╠══════════════════════════════════════════════╣",
        f"║  Saved:  {req.tokens_saved:>4,} tokens  ({req.percent_saved:.1f}%)  ${req.cost_saved:.3f}   ║",
        "╠══════════════════════════════════════════════╣",
        f"║  Session: {sess.session_requests} reqs | -{sess.session_total_saved:,} tokens | ${sess.session_total_cost_saved:.2f} ║",
        "╚══════════════════════════════════════════════╝",
        "",
    ]
    return "\n".join(lines)


def print_demo(request_id: Optional[str] = None) -> None:
    """Print the demo pipeline to stdout."""
    print(run_demo(request_id=request_id))


def seed_demo_data(count: int = 500, hours: int = 24) -> dict:
    """Generate realistic demo telemetry events for dashboard population.
    
    Writes `count` compression events spread across `hours` time window to
    ~/.tokenpak/compression_events.jsonl with:
    - Time distribution favoring business hours (9-17)
    - Multiple models (haiku, sonnet, gpt-4, gpt-3.5, opus)
    - ~70% cache hit rate
    - Realistic token counts and latencies
    - Demo marker in payload
    
    Uses the real telemetry storage backend (CompressionStats JSONL format).
    
    Args:
        count: Number of demo events to generate (default 500)
        hours: Time window in hours (default 24)
    
    Returns:
        Dict with:
        - events: number of events written
        - cache_hit_rate: calculated hit rate (float 0-1)
        - total_events: total now in the file (including demo)
        - cache_read_total: total cache-read tokens across demo set
    """
    from tokenpak.agent.proxy.stats import get_compression_stats
    from datetime import timezone
    
    models = ["claude-haiku-3-5", "claude-sonnet-4", "gpt-4", "gpt-3.5-turbo", "claude-opus-4"]
    
    stats = get_compression_stats()
    cache_hits = 0
    total_cache_read = 0
    
    now = datetime.now(tz=timezone.utc)
    start_time = now - timedelta(hours=hours)
    
    for i in range(count):
        # Spread requests across the time window, favoring business hours (9-17)
        # Total seconds in the window
        total_seconds = hours * 3600
        
        # Pick a random timestamp within the window
        random_seconds = random.randint(0, int(total_seconds))
        base_ts_dt = start_time + timedelta(seconds=random_seconds)
        
        # If this hour is off-hours and we're in the 70% business hour bucket,
        # or it's business hours and we're in the 30% off-hours bucket,
        # adjust the hour
        current_hour = base_ts_dt.hour
        is_business_hour = 9 <= current_hour <= 17
        prefer_business = random.random() < 0.7
        
        if prefer_business and not is_business_hour:
            # Move to a business hour while preserving the day
            target_hour = random.randint(9, 17)
            ts_dt = base_ts_dt.replace(hour=target_hour)
        elif not prefer_business and is_business_hour:
            # Move to an off-hour while preserving the day
            target_hour = random.choice(list(range(0, 9)) + list(range(18, 24)))
            ts_dt = base_ts_dt.replace(hour=target_hour)
        else:
            ts_dt = base_ts_dt
        
        # Select model
        model = random.choice(models)
        
        # Token counts with realistic ranges
        input_tokens_raw = random.randint(500, 4000)
        is_cache_hit = random.random() < 0.70
        
        if is_cache_hit:
            cache_hits += 1
            cache_read = random.randint(100, input_tokens_raw // 2)
            total_cache_read += cache_read
            compression_ratio = random.uniform(0.65, 0.85)
        else:
            cache_read = 0
            compression_ratio = random.uniform(0.80, 0.95)
        
        input_tokens_sent = int(input_tokens_raw * compression_ratio)
        output_tokens = random.randint(100, 2000)
        latency_ms = random.randint(500, 5000)
        
        # Write using the real stats backend
        # This appends to ~/.tokenpak/compression_events.jsonl
        event_dict = {
            "ts": ts_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": model,
            "input_tokens": input_tokens_raw,
            "output_tokens": output_tokens,
            "ratio": round(compression_ratio, 4),
            "latency_ms": int(latency_ms),
            "status": "ok",
            "is_demo": True,  # Mark as demo data
            "cache_read": cache_read,
            "input_tokens_sent": input_tokens_sent,
        }
        
        # Write directly to JSONL file
        import json
        from pathlib import Path
        import os
        
        log_dir = os.path.expanduser("~/.tokenpak")
        log_path = Path(log_dir) / "compression_events.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event_dict) + "\n")
    
    cache_hit_rate = cache_hits / count if count > 0 else 0
    
    # Count total events in file
    total_events = 0
    log_path = Path(os.path.expanduser("~/.tokenpak")) / "compression_events.jsonl"
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as fh:
            total_events = sum(1 for _ in fh if _.strip())
    
    return {
        "events": count,
        "cache_hit_rate": cache_hit_rate,
        "total_events": total_events,
        "cache_read_total": total_cache_read,
    }


def clear_demo_data() -> dict:
    """Remove all demo data from telemetry storage.
    
    Reads ~/.tokenpak/compression_events.jsonl, filters out records with
    is_demo=true, and rewrites the file with only non-demo records.
    
    Returns:
        Dict with deletion counts:
        - deleted_events: number of demo events deleted
        - remaining_events: number of non-demo events kept
    """
    import json
    from pathlib import Path
    import os
    
    log_dir = os.path.expanduser("~/.tokenpak")
    log_path = Path(log_dir) / "compression_events.jsonl"
    
    if not log_path.exists():
        return {
            "deleted_events": 0,
            "remaining_events": 0,
        }
    
    # Read all events
    all_events = []
    deleted_count = 0
    
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if event.get("is_demo") == True:
                        deleted_count += 1
                    else:
                        all_events.append(event)
                except json.JSONDecodeError:
                    # Keep malformed lines as-is
                    all_events.append(line)
    except Exception:
        pass  # File may not exist or be readable yet
    
    # Write back only non-demo events
    try:
        with log_path.open("w", encoding="utf-8") as fh:
            for event in all_events:
                if isinstance(event, dict):
                    fh.write(json.dumps(event) + "\n")
                else:
                    fh.write(event + "\n")
    except Exception:
        pass
    
    return {
        "deleted_events": deleted_count,
        "remaining_events": len(all_events),
    }
