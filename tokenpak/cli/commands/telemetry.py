# SPDX-License-Identifier: Apache-2.0
"""telemetry export command — export telemetry event data to JSON or CSV."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_telemetry_db_path() -> Path:
    from tokenpak.core.paths import get_db_path

    return get_db_path("telemetry.db")


def _connect(db_path: Optional[Path] = None) -> Optional[sqlite3.Connection]:
    p = db_path or _get_telemetry_db_path()
    if not p.exists():
        return None
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def _parse_date_to_ts(date_str: str, label: str) -> float:
    """Parse YYYY-MM-DD to a Unix timestamp (midnight UTC).

    Raises ValueError with ``label`` in the message if the format is wrong.
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        raise ValueError(f"--{label}: expected YYYY-MM-DD, got {date_str!r}")


# ---------------------------------------------------------------------------
# Core query
# ---------------------------------------------------------------------------

_QUERY = """
SELECT
    e.trace_id,
    e.ts,
    datetime(e.ts, 'unixepoch') AS ts_iso,
    e.provider,
    e.model,
    e.agent_id,
    e.status,
    e.duration_ms,
    COALESCE(u.input_billed, 0)        AS input_tokens,
    COALESCE(u.output_billed, 0)       AS output_tokens,
    COALESCE(u.total_tokens_billed, 0) AS total_tokens,
    COALESCE(c.cost_total, 0.0)        AS cost_usd
FROM tp_events e
LEFT JOIN tp_usage u ON u.trace_id = e.trace_id
LEFT JOIN tp_costs c ON c.trace_id = e.trace_id
{where}
ORDER BY e.ts ASC
"""

_FIELDS = [
    "trace_id",
    "ts",
    "ts_iso",
    "provider",
    "model",
    "agent_id",
    "status",
    "duration_ms",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
]


def _query_events(
    conn: sqlite3.Connection,
    since_ts: Optional[float] = None,
    until_ts: Optional[float] = None,
    provider: Optional[str] = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []

    if since_ts is not None:
        conditions.append("e.ts >= ?")
        params.append(since_ts)
    if until_ts is not None:
        conditions.append("e.ts <= ?")
        params.append(until_ts)
    if provider is not None:
        conditions.append("e.provider = ?")
        params.append(provider)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = _QUERY.format(where=where)

    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    return [dict(zip(_FIELDS, tuple(r))) for r in rows]


def _compute_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_requests = len(rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    total_cost_usd = sum(r["cost_usd"] for r in rows)
    return {
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost_usd, 6),
    }


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_json(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    filters: dict[str, Any],
) -> str:
    envelope = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filters": filters,
            "summary": summary,
        },
        "data": rows,
    }
    return json.dumps(envelope, indent=2)


def _format_csv(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    s = summary
    buf.write(
        f"# summary: {s['total_requests']} requests, "
        f"{s['total_tokens']} tokens, "
        f"${s['total_cost_usd']:.6f} cost\n"
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Command entry point
# ---------------------------------------------------------------------------


def cmd_telemetry_export(args: argparse.Namespace) -> None:
    """Handle `tokenpak telemetry export`."""
    fmt = getattr(args, "format", "json")
    since_str = getattr(args, "since", None)
    until_str = getattr(args, "until", None)
    provider = getattr(args, "provider", None)
    db_path: Optional[Path] = getattr(args, "_db_path", None)  # for testing

    since_ts: Optional[float] = None
    until_ts: Optional[float] = None

    if since_str:
        since_ts = _parse_date_to_ts(since_str, "since")
    if until_str:
        until_ts = _parse_date_to_ts(until_str, "until") + 86399

    conn = _connect(db_path)
    if conn is None:
        if fmt == "json":
            summary = {"total_requests": 0, "total_tokens": 0, "total_cost_usd": 0.0}
            filters = {"since": since_str, "until": until_str, "provider": provider}
            print(_format_json([], summary, filters))
        else:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=_FIELDS)
            writer.writeheader()
            buf.write("# summary: 0 requests, 0 tokens, $0.000000 cost\n")
            print(buf.getvalue(), end="")
        return

    rows = _query_events(conn, since_ts=since_ts, until_ts=until_ts, provider=provider)
    conn.close()

    summary = _compute_summary(rows)
    filters = {"since": since_str, "until": until_str, "provider": provider}

    if fmt == "json":
        print(_format_json(rows, summary, filters))
    else:
        print(_format_csv(rows, summary), end="")
