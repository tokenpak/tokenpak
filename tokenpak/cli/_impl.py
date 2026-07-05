# SPDX-License-Identifier: Apache-2.0
"""tokenpak/cli/_impl.py — Fleet rollup status implementation.

Entry point: run_fleet(since_days, as_json, db_path)

Reads from rollup_daily (populated nightly by fleet-telemetry-rollup.sh).
If rollup_daily is empty or missing, falls back to a live aggregation from
the requests table so the command is useful before the first cron run.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    """Resolve monitor.db path. Checks env, then common locations."""
    if db_path:
        return db_path
    for candidate in [
        os.environ.get("TOKENPAK_DB", ""),
        os.path.expanduser("~/tokenpak/monitor.db"),
        os.path.expanduser("~/.tokenpak/data/monitor.db"),
    ]:
        if candidate and Path(candidate).exists():
            return candidate
    return os.path.expanduser("~/tokenpak/monitor.db")


def _open_db(db_path: str) -> Optional[sqlite3.Connection]:
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Rollup queries
# ---------------------------------------------------------------------------


def _query_rollup(conn: sqlite3.Connection, since_days: int) -> List[Dict[str, Any]]:
    """Query rollup_daily for the last N days. Returns list of row dicts."""
    try:
        rows = conn.execute(
            """
            SELECT date, agent_id, host, model,
                   requests, input_tokens, output_tokens,
                   cache_read_tokens, cache_creation_tokens,
                   estimated_cost, would_have_saved
            FROM rollup_daily
            WHERE date >= date('now', ? )
            ORDER BY date DESC, agent_id, model
            """,
            (f"-{since_days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def _query_live_fallback(conn: sqlite3.Connection, since_days: int) -> List[Dict[str, Any]]:
    """Live aggregation from requests when rollup_daily is empty/missing."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
    agent_expr = "agent_id" if "agent_id" in cols else "COALESCE(attribution_source, 'unknown')"
    host_expr = "host" if "host" in cols else "'local'"
    try:
        rows = conn.execute(
            f"""
            SELECT
                date(timestamp)          AS date,
                {agent_expr}             AS agent_id,
                {host_expr}              AS host,
                model,
                COUNT(*)                 AS requests,
                COALESCE(SUM(input_tokens),          0) AS input_tokens,
                COALESCE(SUM(output_tokens),         0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens),     0) AS cache_read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                COALESCE(SUM(estimated_cost),      0.0) AS estimated_cost,
                COALESCE(SUM(would_have_saved),      0) AS would_have_saved
            FROM requests
            WHERE timestamp >= date('now', ?)
            GROUP BY date(timestamp), {agent_expr}, {host_expr}, model
            ORDER BY date(timestamp) DESC, {agent_expr}, model
            """,
            (f"-{since_days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_cost(v: float) -> str:
    if v >= 1.0:
        return f"${v:.2f}"
    if v >= 0.01:
        return f"${v:.3f}"
    return f"${v:.4f}"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _would_have_saved_usd(model: Optional[str], would_have_saved_tokens: int) -> float:
    """Convert the ``would_have_saved`` column to USD via model rates.

    The column stores TOKENS (input tokens avoided before send, written by
    the proxy as ``input_tokens - sent_input_tokens``) — NOT micro-dollars.
    Value them at the model's registry input rate; fall back to a
    sonnet-class default rate when the registry is unavailable.
    """
    if would_have_saved_tokens <= 0:
        return 0.0
    try:
        from tokenpak.models import get_rates

        rate_per_mtok = get_rates(model or None).get("input", 3.0)
    except Exception:
        rate_per_mtok = 3.0
    return would_have_saved_tokens * rate_per_mtok / 1_000_000


def _saved_pct(
    estimated_cost: float,
    would_have_saved: int,
    model: Optional[str] = None,
) -> str:
    """Compute saved_pct; returns 'TBD' when cost=0 but savings exist (unknown model pricing).

    ``would_have_saved`` is in TOKENS and is converted to USD via the
    model's registry input rate before being compared with the actual cost.
    """
    if estimated_cost == 0.0 and would_have_saved > 0:
        return "TBD"
    saved_usd = _would_have_saved_usd(model, would_have_saved)
    total = estimated_cost + saved_usd
    if total <= 0:
        return "n/a"
    pct = saved_usd / total * 100
    return f"{pct:.1f}%"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_fleet(
    since_days: int = 7,
    as_json: bool = False,
    db_path: Optional[str] = None,
) -> None:
    """Print fleet rollup status table (or JSON) to stdout.

    Args:
        since_days: Window in days (--since=Nd → N).
        as_json: Emit JSON instead of table.
        db_path: Override monitor.db path.
    """
    resolved = _resolve_db_path(db_path)
    conn = _open_db(resolved)
    if conn is None:
        print(f"⚠️  Fleet status: monitor.db not found at {resolved}")
        print("   Run fleet-telemetry-rollup.sh or start the proxy to populate data.")
        return

    rows = _query_rollup(conn, since_days)
    live_fallback = False
    if not rows:
        rows = _query_live_fallback(conn, since_days)
        live_fallback = True
    conn.close()

    if as_json:
        _print_fleet_json(rows, since_days, live_fallback)
    else:
        _print_fleet_table(rows, since_days, live_fallback)


# ---------------------------------------------------------------------------
# Output renderers
# ---------------------------------------------------------------------------


def _print_fleet_table(
    rows: List[Dict[str, Any]],
    since_days: int,
    live_fallback: bool,
) -> None:
    header_note = " (live, rollup pending)" if live_fallback else ""
    print(f"\n  Fleet status — last {since_days}d{header_note}")
    print("  " + "─" * 82)

    if not rows:
        print("  No data found. Run fleet-telemetry-rollup.sh or check monitor.db.")
        print()
        return

    fmt = "  {:<12} {:<10} {:<26} {:>7} {:>8} {:>9} {:>9} {:>10}"
    print(fmt.format("agent", "runtime", "model", "reqs", "cache↑", "cache✦", "cost", "saved%"))
    print("  " + "─" * 82)

    for r in rows:
        agent = (r.get("agent_id") or "—")[:12]
        host = (r.get("host") or "—")[:10]
        model = (r.get("model") or "—")[:26]
        reqs = r.get("requests", 0)
        cr = r.get("cache_read_tokens", 0)
        cc = r.get("cache_creation_tokens", 0)
        cost = float(r.get("estimated_cost", 0.0))
        saved = int(r.get("would_have_saved", 0))  # tokens avoided, not dollars
        pct = _saved_pct(cost, saved, model=r.get("model"))

        print(fmt.format(
            agent, host, model,
            f"{reqs:,}",
            _fmt_tokens(cr),
            _fmt_tokens(cc),
            _fmt_cost(cost),
            pct,
        ))

    print("  " + "─" * 82)
    total_reqs = sum(r.get("requests", 0) for r in rows)
    total_cost = sum(float(r.get("estimated_cost", 0.0)) for r in rows)
    print(f"  {len(rows)} rows | {total_reqs:,} total requests | {_fmt_cost(total_cost)} total cost")
    print()


def _print_fleet_json(
    rows: List[Dict[str, Any]],
    since_days: int,
    live_fallback: bool,
) -> None:
    output: Dict[str, Any] = {
        "since_days": since_days,
        "source": "live_requests" if live_fallback else "rollup_daily",
        "row_count": len(rows),
        "rows": [],
    }
    for r in rows:
        cost = float(r.get("estimated_cost", 0.0))
        saved = int(r.get("would_have_saved", 0))  # tokens avoided, not dollars
        output["rows"].append({
            "date": r.get("date"),
            "agent_id": r.get("agent_id"),
            "host": r.get("host"),
            "model": r.get("model"),
            "requests": r.get("requests", 0),
            "input_tokens": r.get("input_tokens", 0),
            "output_tokens": r.get("output_tokens", 0),
            "cache_read_tokens": r.get("cache_read_tokens", 0),
            "cache_creation_tokens": r.get("cache_creation_tokens", 0),
            "estimated_cost": cost,
            "would_have_saved": saved,
            "would_have_saved_unit": "tokens",
            "would_have_saved_usd": round(
                _would_have_saved_usd(r.get("model"), saved), 6
            ),
            "saved_pct": _saved_pct(cost, saved, model=r.get("model")),
        })
    print(json.dumps(output, indent=2, default=str))
