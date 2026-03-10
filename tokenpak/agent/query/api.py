"""tokenpak/agent/query/api.py

Phase 5B: Query API
===================
Provides HTTP endpoints for retrieving and analyzing ingested usage entries
stored as JSONL files in the vault index.

Storage location:
  ~/vault/.tokenpak/entries/YYYY-MM-DD.jsonl

Endpoints:
  GET  /query/entries          — entries for a date range
  GET  /query/stats            — summary metrics for a date
  GET  /query/rollups          — time-series rollup buckets
  GET  /query/top-users        — most active agents
  GET  /query/cache-trends     — cache hit rate over time
  GET  /query/compression-ratio — avg compression per agent
  GET  /query/usage-summary    — daily summary across all agents
  POST /query/export           — export entries as CSV
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

VAULT_ENTRIES_DIR = Path(os.path.expanduser("~/vault/.tokenpak/entries"))


def _date_range(start_date: str, end_date: str) -> list[str]:
    """Return list of YYYY-MM-DD strings from start_date to end_date inclusive."""
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}") from exc
    if start > end:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    dates = []
    cur = start
    while cur <= end:
        dates.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return dates


# ---------------------------------------------------------------------------
# EntryStore
# ---------------------------------------------------------------------------


class EntryStore:
    """Load and aggregate entries from JSONL date-partitioned files."""

    def __init__(self, entries_dir: Optional[Path] = None) -> None:
        self.entries_dir = entries_dir or VAULT_ENTRIES_DIR

    def read_entries(
        self,
        start_date: str,
        end_date: str,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Load entries from JSONL files in the given date range."""
        entries: list[dict[str, Any]] = []
        for date_str in _date_range(start_date, end_date):
            path = self.entries_dir / f"{date_str}.jsonl"
            if not path.exists():
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            logger.warning("Skipping malformed line in %s", path)
            except OSError as exc:
                logger.warning("Cannot read %s: %s", path, exc)
            if limit and len(entries) >= limit:
                break
        if limit:
            entries = entries[:limit]
        return entries

    def compute_stats(self, date: str) -> dict[str, Any]:
        """Aggregate metrics for a single date."""
        entries = self.read_entries(date, date)
        if not entries:
            return {
                "date": date,
                "request_count": 0,
                "token_count": 0,
                "cache_hit_pct": 0.0,
                "compression_pct": 0.0,
                "total_cost": 0.0,
            }
        total_tokens = sum(e.get("tokens", 0) for e in entries)
        total_cost = sum(e.get("cost", 0.0) for e in entries)

        # Cache hit tokens come from extra.cache_tokens if present
        cache_tokens = sum((e.get("extra") or {}).get("cache_tokens", 0) for e in entries)
        cache_hit_pct = (cache_tokens / total_tokens * 100) if total_tokens else 0.0

        # Compression ratio: raw_tokens / final_tokens - 1 (pct reduction)
        compression_pct_values = [
            (e.get("extra") or {}).get("compression_pct", 0.0) for e in entries
        ]
        compression_pct = (
            sum(compression_pct_values) / len(compression_pct_values)
            if compression_pct_values
            else 0.0
        )

        return {
            "date": date,
            "request_count": len(entries),
            "token_count": total_tokens,
            "cache_hit_pct": round(cache_hit_pct, 2),
            "compression_pct": round(compression_pct, 2),
            "total_cost": round(total_cost, 6),
        }

    def compute_rollups(
        self,
        start_date: str,
        end_date: str,
        window_minutes: int = 5,
    ) -> list[dict[str, Any]]:
        """Time-series rollups with configurable window."""
        if window_minutes < 1:
            raise HTTPException(status_code=400, detail="window_minutes must be >= 1")
        entries = self.read_entries(start_date, end_date)
        buckets: dict[str, dict[str, Any]] = {}
        window_sec = window_minutes * 60

        for entry in entries:
            ts_str = entry.get("timestamp", "")
            try:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                epoch = ts_dt.timestamp()
            except (ValueError, AttributeError):
                continue
            bucket_epoch = int(epoch // window_sec) * window_sec
            bucket_ts = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc).isoformat()
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {
                    "timestamp": bucket_ts,
                    "total_tokens": 0,
                    "cache_tokens": 0,
                    "request_count": 0,
                }
            b = buckets[bucket_ts]
            b["total_tokens"] += entry.get("tokens", 0)
            b["cache_tokens"] += (entry.get("extra") or {}).get("cache_tokens", 0)
            b["request_count"] += 1

        return sorted(buckets.values(), key=lambda x: x["timestamp"])

    def top_users(
        self,
        date: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return top agents by request count for a date."""
        entries = self.read_entries(date, date)
        agent_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"request_count": 0, "total_tokens": 0}
        )
        for entry in entries:
            agent_id = entry.get("agent") or "unknown"
            agent_stats[agent_id]["request_count"] += 1
            agent_stats[agent_id]["total_tokens"] += entry.get("tokens", 0)
        result = [{"agent_id": agent, **stats} for agent, stats in agent_stats.items()]
        result.sort(key=lambda x: x["request_count"], reverse=True)
        return result[:limit]

    def cache_trends(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Cache hit rate over time (one point per day)."""
        trends = []
        for date_str in _date_range(start_date, end_date):
            path = self.entries_dir / f"{date_str}.jsonl"
            if not path.exists():
                continue
            entries = self.read_entries(date_str, date_str)
            if not entries:
                continue
            total_tokens = sum(e.get("tokens", 0) for e in entries)
            cache_tokens = sum((e.get("extra") or {}).get("cache_tokens", 0) for e in entries)
            hit_rate = (cache_tokens / total_tokens) if total_tokens else 0.0
            miss_rate = 1.0 - hit_rate
            trends.append(
                {
                    "timestamp": date_str,
                    "hit_rate": round(hit_rate, 4),
                    "miss_rate": round(miss_rate, 4),
                }
            )
        return trends

    def compression_ratios(self, date: str) -> list[dict[str, Any]]:
        """Average compression ratio per agent for a date."""
        entries = self.read_entries(date, date)
        agent_data: dict[str, list[float]] = defaultdict(list)
        for entry in entries:
            agent_id = entry.get("agent") or "unknown"
            ratio = (entry.get("extra") or {}).get("compression_ratio", None)
            if ratio is not None:
                agent_data[agent_id].append(float(ratio))
        result = []
        for agent_id, ratios in agent_data.items():
            result.append(
                {
                    "agent_id": agent_id,
                    "avg_compression_ratio": round(sum(ratios) / len(ratios), 4),
                    "sample_count": len(ratios),
                }
            )
        result.sort(key=lambda x: x["agent_id"])  # type: ignore
        return result

    def usage_summary(self, date: str) -> dict[str, Any]:
        """Daily usage summary across all agents."""
        entries = self.read_entries(date, date)
        if not entries:
            return {
                "date": date,
                "total_requests": 0,
                "total_tokens": 0,
                "cache_tokens": 0,
                "avg_compression": 0.0,
                "unique_agents": 0,
            }
        total_tokens = sum(e.get("tokens", 0) for e in entries)
        cache_tokens = sum((e.get("extra") or {}).get("cache_tokens", 0) for e in entries)
        compression_vals = [(e.get("extra") or {}).get("compression_ratio", None) for e in entries]
        valid_compression = [v for v in compression_vals if v is not None]
        avg_compression = (
            sum(valid_compression) / len(valid_compression) if valid_compression else 0.0
        )
        unique_agents = len({e.get("agent") for e in entries if e.get("agent")})
        return {
            "date": date,
            "total_requests": len(entries),
            "total_tokens": total_tokens,
            "cache_tokens": cache_tokens,
            "avg_compression": round(avg_compression, 4),
            "unique_agents": unique_agents,
        }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    start_date: str
    end_date: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_store = EntryStore()
router = APIRouter(tags=["query"])


@router.get("/query/entries")
def query_entries(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    limit: Optional[int] = Query(None, ge=1, le=10000, description="Max entries to return"),
) -> dict[str, Any]:
    """Return all entries for a date range."""
    entries = _store.read_entries(start_date, end_date, limit=limit)
    return {"status": "ok", "count": len(entries), "entries": entries}


@router.get("/query/stats")
def query_stats(
    date: str = Query(..., description="Date YYYY-MM-DD"),
) -> dict[str, Any]:
    """Summarize metrics for a date."""
    stats = _store.compute_stats(date)
    return {"status": "ok", "stats": stats}


@router.get("/query/rollups")
def query_rollups(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
    window_minutes: int = Query(5, ge=1, description="Rollup window in minutes"),
) -> dict[str, Any]:
    """Return time-series rollup buckets."""
    rollups = _store.compute_rollups(start_date, end_date, window_minutes=window_minutes)
    return {
        "status": "ok",
        "window_minutes": window_minutes,
        "count": len(rollups),
        "rollups": rollups,
    }


@router.get("/query/top-users")
def query_top_users(
    date: str = Query(..., description="Date YYYY-MM-DD"),
    limit: int = Query(10, ge=1, le=100, description="Max users to return"),
) -> dict[str, Any]:
    """Return top agents by request count."""
    users = _store.top_users(date, limit=limit)
    return {"status": "ok", "date": date, "users": users}


@router.get("/query/cache-trends")
def query_cache_trends(
    start_date: str = Query(..., description="Start date YYYY-MM-DD"),
    end_date: str = Query(..., description="End date YYYY-MM-DD"),
) -> dict[str, Any]:
    """Return cache hit rate over time."""
    trends = _store.cache_trends(start_date, end_date)
    return {"status": "ok", "trends": trends}


@router.get("/query/compression-ratio")
def query_compression_ratio(
    date: str = Query(..., description="Date YYYY-MM-DD"),
) -> dict[str, Any]:
    """Return average compression ratio per agent."""
    ratios = _store.compression_ratios(date)
    return {"status": "ok", "date": date, "compression": ratios}


@router.get("/query/usage-summary")
def query_usage_summary(
    date: str = Query(..., description="Date YYYY-MM-DD"),
) -> dict[str, Any]:
    """Daily usage summary across all agents."""
    summary = _store.usage_summary(date)
    return {"status": "ok", "summary": summary}


@router.post("/query/export")
def query_export(body: ExportRequest) -> StreamingResponse:
    """Export entries as CSV download."""
    entries = _store.read_entries(body.start_date, body.end_date)
    if not entries:
        raise HTTPException(status_code=404, detail="No entries found for date range")
    output = io.StringIO()
    fieldnames = ["id", "timestamp", "agent", "model", "provider", "tokens", "cost", "session_id"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for entry in entries:
        writer.writerow({k: entry.get(k, "") for k in fieldnames})
    output.seek(0)
    filename = f"tokenpak-export-{body.start_date}-to-{body.end_date}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_query_app(prefix: str = "") -> Any:
    """Create a standalone FastAPI app with query routes."""
    from fastapi import FastAPI

    app = FastAPI(
        title="TokenPak Query API",
        version="5.0.0",
        description="Phase 5B: Agent usage data query and analysis",
    )
    app.include_router(router, prefix=prefix)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "tokenpak-query"}

    return app
