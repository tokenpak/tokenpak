"""Demo data seed generator for TokenPak dashboard.

Generates realistic sample telemetry data for OSS demo/trial users.
Populates a 24-hour period with 500+ requests showing cache patterns,
model diversity, and realistic savings.

Usage::

    from tokenpak.cli.demo import seed_demo_data, clear_demo_data

    seed_demo_data()           # Populate with 24h of demo data
    clear_demo_data()          # Remove all demo data
    get_demo_db_path()         # Get the telemetry database path
"""

import random
import sqlite3
import time
import uuid
from pathlib import Path

from .telemetry.models import Cost, Segment, TelemetryEvent, Usage
from .telemetry.storage import TelemetryDB


def get_demo_db_path() -> str:
    """Return path to telemetry database (creates parent dir if needed)."""
    db_path = Path.home() / ".tokenpak" / "telemetry.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def seed_demo_data(count: int = 500, hours: int = 24) -> dict:
    """Seed the telemetry database with realistic demo data.

    Parameters
    ----------
    count : int
        Total number of requests to generate (default: 500).
    hours : int
        Time period to span (default: 24).

    Returns
    -------
    dict
        Summary: {'events': N, 'usage': N, 'costs': N, 'segments': N}
    """
    db_path = get_demo_db_path()
    db = TelemetryDB(db_path)

    now = time.time()
    period_seconds = hours * 3600
    start_time = now - period_seconds

    # Model distribution (with providers)
    models = [
        ("anthropic", "claude-haiku-3-5", 0.20),
        ("anthropic", "claude-sonnet-4-5", 0.30),
        ("openai", "gpt-4o", 0.20),
        ("openai", "gpt-4-turbo", 0.20),
        ("openai", "text-davinci-003", 0.10),
    ]

    # Business hours: 9-17 (8h), 70% of traffic
    # Off-hours: 17-9 (16h), 30% of traffic
    business_requests = int(count * 0.70)
    offhours_requests = count - business_requests

    events_list = []
    usage_list = []
    cost_list = []
    segments_list = []
    cache_hit_count = 0

    # Generate business hour requests
    for i in range(business_requests):
        hour_offset = random.randint(9, 16)  # 9-16 inclusive (covers 9-17 boundary)
        minute_offset = random.randint(0, 59)
        second_offset = random.randint(0, 59)

        # Pick a random day in the period
        day_offset = random.randint(0, max(0, hours // 24))
        request_ts = (
            start_time
            + (day_offset * 86400)
            + (hour_offset * 3600)
            + (minute_offset * 60)
            + second_offset
        )

        trace_id = f"demo-trace-{uuid.uuid4()}"
        request_id = f"req-{uuid.uuid4()}"

        # Pick model
        provider, model, _ = random.choices(models, weights=[m[2] for m in models], k=1)[0]

        # 70% cache hit rate
        is_cache_hit = random.random() < 0.70
        if is_cache_hit:
            cache_hit_count += 1

        # Event
        event = TelemetryEvent(
            trace_id=trace_id,
            request_id=request_id,
            event_type="request_end",
            ts=request_ts,
            provider=provider,
            model=model,
            agent_id=f"demo-agent-{i % 5}",
            api=f"{provider}-messages" if provider == "anthropic" else "openai-responses",
            stop_reason="end_turn",
            session_id=f"demo-session-{i // 50}",
            duration_ms=random.uniform(500, 5000),
            status="ok",
            payload={"is_demo": True, "cache_hit": is_cache_hit},
        )
        events_list.append(event)

        # Usage (if cache hit, only cache_read; else both input and output)
        if is_cache_hit:
            usage = Usage(
                trace_id=trace_id,
                input_billed=0,
                output_billed=0,
                cache_read=random.randint(500, 2000),
                cache_write=0,
            )
        else:
            usage = Usage(
                trace_id=trace_id,
                input_billed=random.randint(200, 1000),
                output_billed=random.randint(100, 500),
                cache_read=0,
                cache_write=random.randint(300, 1500),
            )
        usage_list.append(usage)

        # Cost
        if is_cache_hit:
            input_cost = 0.0
            output_cost = 0.0
            actual_cost = 0.0
            baseline_cost = 0.0
        else:
            # Approximate pricing
            model_prices = {
                "claude-haiku-3-5": (0.00008, 0.0004),
                "claude-sonnet-4-5": (0.003, 0.015),
                "gpt-4o": (0.005, 0.015),
                "gpt-4-turbo": (0.01, 0.03),
                "text-davinci-003": (0.002, 0.002),
            }
            in_price, out_price = model_prices.get(model, (0.0001, 0.0005))
            input_cost = (usage.input_billed / 1_000_000) * in_price
            output_cost = (usage.output_billed / 1_000_000) * out_price
            actual_cost = input_cost + output_cost
            # Assume 40% baseline cost without compression
            baseline_cost = actual_cost / 0.6

        cost = Cost(
            trace_id=trace_id,
            cost_input=input_cost,
            cost_output=output_cost,
            cost_total=actual_cost,
            cost_source="estimated",
            actual_cost=actual_cost,
            baseline_cost=baseline_cost,
            savings_total=baseline_cost - actual_cost,
            savings_tp=(baseline_cost - actual_cost) * 0.8,
        )
        cost_list.append(cost)

        # Segments (1-3 per request)
        for seg_idx in range(random.randint(1, 3)):
            segment_id = f"{trace_id}-seg-{seg_idx}"
            segment = Segment(
                trace_id=trace_id,
                segment_id=segment_id,
                order=seg_idx,
                segment_type="context" if seg_idx == 0 else "response",
                tokens_raw=random.randint(100, 1000),
                tokens_after_tp=random.randint(50, 800),
                segment_source="demo",
            )
            segments_list.append(segment)

    # Generate off-hours requests (similar pattern, fewer)
    for i in range(offhours_requests):
        # Pick off-hours: 17-9 next day
        hour_offset = random.choice(list(range(17, 24)) + list(range(0, 9)))
        minute_offset = random.randint(0, 59)
        second_offset = random.randint(0, 59)

        day_offset = random.randint(0, max(0, hours // 24))
        request_ts = (
            start_time
            + (day_offset * 86400)
            + (hour_offset * 3600)
            + (minute_offset * 60)
            + second_offset
        )

        trace_id = f"demo-trace-{uuid.uuid4()}"
        request_id = f"req-{uuid.uuid4()}"

        provider, model, _ = random.choices(models, weights=[m[2] for m in models], k=1)[0]
        is_cache_hit = random.random() < 0.70
        if is_cache_hit:
            cache_hit_count += 1

        event = TelemetryEvent(
            trace_id=trace_id,
            request_id=request_id,
            event_type="request_end",
            ts=request_ts,
            provider=provider,
            model=model,
            agent_id=f"demo-agent-{business_requests + i % 5}",
            api=f"{provider}-messages" if provider == "anthropic" else "openai-responses",
            stop_reason="end_turn",
            session_id=f"demo-session-{(business_requests + i) // 50}",
            duration_ms=random.uniform(500, 5000),
            status="ok",
            payload={"is_demo": True, "cache_hit": is_cache_hit},
        )
        events_list.append(event)

        if is_cache_hit:
            usage = Usage(
                trace_id=trace_id,
                input_billed=0,
                output_billed=0,
                cache_read=random.randint(500, 2000),
            )
        else:
            usage = Usage(
                trace_id=trace_id,
                input_billed=random.randint(200, 1000),
                output_billed=random.randint(100, 500),
                cache_write=random.randint(300, 1500),
            )
        usage_list.append(usage)

        if is_cache_hit:
            input_cost = 0.0
            output_cost = 0.0
            actual_cost = 0.0
            baseline_cost = 0.0
        else:
            model_prices = {
                "claude-haiku-3-5": (0.00008, 0.0004),
                "claude-sonnet-4-5": (0.003, 0.015),
                "gpt-4o": (0.005, 0.015),
                "gpt-4-turbo": (0.01, 0.03),
                "text-davinci-003": (0.002, 0.002),
            }
            in_price, out_price = model_prices.get(model, (0.0001, 0.0005))
            input_cost = (usage.input_billed / 1_000_000) * in_price
            output_cost = (usage.output_billed / 1_000_000) * out_price
            actual_cost = input_cost + output_cost
            baseline_cost = actual_cost / 0.6

        cost = Cost(
            trace_id=trace_id,
            cost_input=input_cost,
            cost_output=output_cost,
            cost_total=actual_cost,
            cost_source="estimated",
            actual_cost=actual_cost,
            baseline_cost=baseline_cost,
            savings_total=baseline_cost - actual_cost,
            savings_tp=(baseline_cost - actual_cost) * 0.8,
        )
        cost_list.append(cost)

        for seg_idx in range(random.randint(1, 3)):
            segment_id = f"{trace_id}-seg-{seg_idx}"
            segment = Segment(
                trace_id=trace_id,
                segment_id=segment_id,
                order=seg_idx,
                segment_type="context" if seg_idx == 0 else "response",
                tokens_raw=random.randint(100, 1000),
                tokens_after_tp=random.randint(50, 800),
                segment_source="demo",
            )
            segments_list.append(segment)

    # Batch insert all
    db.insert_events(events_list)
    db.insert_usages(usage_list)
    db.insert_costs(cost_list)
    db.insert_segments(segments_list)

    db.close()

    return {
        "events": len(events_list),
        "usage": len(usage_list),
        "costs": len(cost_list),
        "segments": len(segments_list),
        "cache_hit_rate": cache_hit_count / len(events_list) if events_list else 0,
    }


def clear_demo_data() -> dict:
    """Remove all demo data from the telemetry database.

    Returns
    -------
    dict
        Summary: {'deleted_events': N, 'deleted_usage': N, 'deleted_costs': N, 'deleted_segments': N}
    """
    db_path = get_demo_db_path()

    if not Path(db_path).exists():
        return {"deleted_events": 0, "deleted_usage": 0, "deleted_costs": 0, "deleted_segments": 0}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Delete demo data (WHERE payload contains is_demo=true)
    cursor.execute("SELECT trace_id FROM tp_events WHERE json_extract(payload, '$.is_demo') = true")
    demo_traces = [row[0] for row in cursor.fetchall()]

    deleted = {"deleted_events": 0, "deleted_usage": 0, "deleted_costs": 0, "deleted_segments": 0}

    if demo_traces:
        # Batch delete by trace_id
        placeholders = ",".join("?" * len(demo_traces))

        cursor.execute(f"DELETE FROM tp_events WHERE trace_id IN ({placeholders})", demo_traces)
        deleted["deleted_events"] = cursor.rowcount

        cursor.execute(f"DELETE FROM tp_usage WHERE trace_id IN ({placeholders})", demo_traces)
        deleted["deleted_usage"] = cursor.rowcount

        cursor.execute(f"DELETE FROM tp_costs WHERE trace_id IN ({placeholders})", demo_traces)
        deleted["deleted_costs"] = cursor.rowcount

        cursor.execute(f"DELETE FROM tp_segments WHERE trace_id IN ({placeholders})", demo_traces)
        deleted["deleted_segments"] = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted
