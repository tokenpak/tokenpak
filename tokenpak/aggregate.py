"""aggregate.py — Request ledger aggregation across machines."""
from __future__ import annotations

import json
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REQUESTS_PATH = Path.home() / ".tokenpak" / "requests.jsonl"


@dataclass
class AggregateRow:
    agent: str
    machine: str
    model: str
    requests: int
    tokens: int
    cost: float
    saved: float


def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Allow Z suffix
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def parse_since(value: Optional[str]) -> Optional[datetime]:
    """Parse a since value (e.g., "7d", "12h", ISO date)."""
    if not value:
        return None
    raw = value.strip()
    m = re.match(r"^(\d+)([dhm])$", raw)
    if m:
        qty = int(m.group(1))
        unit = m.group(2)
        delta = {"d": timedelta(days=qty), "h": timedelta(hours=qty), "m": timedelta(minutes=qty)}[unit]
        return datetime.now(timezone.utc) - delta
    # ISO date or datetime
    dt = _parse_iso(raw)
    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def load_requests(path: Optional[Path] = None, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
    p = path or REQUESTS_PATH
    if not p.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since:
            ts = _parse_iso(record.get("timestamp", ""))
            if not ts:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < since:
                continue
        rows.append(record)
    return rows


def aggregate_records(records: Iterable[Dict[str, Any]], machine: str) -> Tuple[List[AggregateRow], Dict[str, Any]]:
    totals = {
        "requests": 0,
        "tokens": 0,
        "cost": 0.0,
        "saved": 0.0,
    }
    buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for rec in records:
        agent = rec.get("agent") or "unknown"
        model = rec.get("model") or "unknown"
        input_tokens = _coerce_int(rec.get("input_tokens"))
        output_tokens = _coerce_int(rec.get("output_tokens"))
        tokens = input_tokens + output_tokens
        cost = _coerce_float(rec.get("cost"))
        saved = _coerce_float(rec.get("saved_cost"))

        totals["requests"] += 1
        totals["tokens"] += tokens
        totals["cost"] += cost
        totals["saved"] += saved

        key = (agent, model)
        bucket = buckets.setdefault(key, {"requests": 0, "tokens": 0, "cost": 0.0, "saved": 0.0})
        bucket["requests"] += 1
        bucket["tokens"] += tokens
        bucket["cost"] += cost
        bucket["saved"] += saved

    rows: List[AggregateRow] = []
    for (agent, model), data in buckets.items():
        rows.append(
            AggregateRow(
                agent=agent,
                machine=machine,
                model=model,
                requests=data["requests"],
                tokens=data["tokens"],
                cost=round(data["cost"], 4),
                saved=round(data["saved"], 4),
            )
        )

    rows.sort(key=lambda r: (-r.cost, r.machine, r.agent, r.model))
    totals["cost"] = round(totals["cost"], 4)
    totals["saved"] = round(totals["saved"], 4)
    return rows, totals


def format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def fmt_cost(amount: float) -> str:
    if amount >= 1:
        return f"${amount:.2f}"
    if amount >= 0.01:
        return f"${amount:.2f}"
    return f"${amount:.4f}"


def default_machine_name() -> str:
    return os.environ.get("TOKENPAK_MACHINE") or socket.gethostname().split(".")[0]


def render_table(rows: List[AggregateRow], totals: Dict[str, Any]) -> str:
    if not rows:
        return "No request ledger entries found."

    header = "Agent   Machine  Model              Requests  Tokens   Cost     Saved"
    lines = [header, "─" * len(header)]

    for r in rows:
        line = f"{r.agent:<7} {r.machine:<8} {r.model:<17} {r.requests:>8}  {format_tokens(r.tokens):>6}  {fmt_cost(r.cost):>7}  {fmt_cost(r.saved):>7}"
        lines.append(line)

    lines.append("─" * len(header))
    lines.append(
        f"TOTAL{'':<3}{'':<8}{'':<17} {totals['requests']:>8}  {format_tokens(totals['tokens']):>6}  {fmt_cost(totals['cost']):>7}  {fmt_cost(totals['saved']):>7}"
    )
    return "\n".join(lines)


__all__ = [
    "REQUESTS_PATH",
    "AggregateRow",
    "parse_since",
    "load_requests",
    "aggregate_records",
    "render_table",
    "default_machine_name",
]
