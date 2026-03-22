"""request_explorer.py — Utilities for live request exploration."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

REQUESTS_PATH = Path.home() / ".tokenpak" / "requests.jsonl"


@dataclass
class RequestView:
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    saved_cost: float
    status: str
    timestamp: str
    session_id: str = ""


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_int(value) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def load_requests(path: Optional[Path] = None, limit: Optional[int] = None) -> list[dict]:
    p = path or REQUESTS_PATH
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit:
        return rows[-limit:]
    return rows


def get_request_by_id(request_id: str, path: Optional[Path] = None) -> Optional[dict]:
    for row in load_requests(path=path):
        if row.get("id") == request_id:
            return row
    return None


def to_view(row: dict) -> RequestView:
    return RequestView(
        request_id=str(row.get("id", "")),
        model=str(row.get("model", "")),
        input_tokens=_safe_int(row.get("input_tokens")),
        output_tokens=_safe_int(row.get("output_tokens")),
        cache_read=_safe_int(row.get("cache_read")),
        saved_cost=_safe_float(row.get("saved_cost")),
        status=str(row.get("status", "")),
        timestamp=str(row.get("timestamp", "")),
        session_id=str(row.get("session_id", "")),
    )


def cache_pct(view: RequestView) -> float:
    if view.input_tokens <= 0:
        return 0.0
    return round((view.cache_read / view.input_tokens) * 100, 1)


def status_label(view: RequestView) -> str:
    if view.status and view.status != "success":
        return "error"
    if view.cache_read > 0:
        return "cached"
    return "fresh"


def age_label(timestamp: str) -> str:
    dt = _parse_iso(timestamp)
    if not dt:
        return "?"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"




def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


__all__ = [
    "REQUESTS_PATH",
    "RequestView",
    "load_requests",
    "get_request_by_id",
    "to_view",
    "cache_pct",
    "status_label",
    "age_label",
    "format_tokens",
]
