"""savings command — dedicated compression efficiency report.

Usage:
    tokenpak savings                # 24h efficiency report
    tokenpak savings --period 7d    # change time window (24h, 7d, 30d)
    tokenpak savings --verbose      # per-model breakdown
    tokenpak savings --json         # machine-readable JSON
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

# Import pricing module for savings estimates
try:
    from tokenpak.pricing import estimate_savings as calc_savings_from_stats
except ImportError:
    calc_savings_from_stats = None

# ---------------------------------------------------------------------------
# DB / formatting helpers
# ---------------------------------------------------------------------------

_MONITOR_DB = os.environ.get(
    "TOKENPAK_DB",
    os.path.expanduser("~/.openclaw/workspace/.tokenpak/monitor.db"),
)

SEP = "────────────────────────────────"


def _connect() -> Optional[sqlite3.Connection]:
    db = Path(_MONITOR_DB)
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _period_days(period: str) -> Optional[int]:
    """Return number of days for named period, or None for all-time."""
    mapping = {"24h": 1, "7d": 7, "30d": 30}
    return mapping.get(period.lower())


def _period_label(period: str) -> str:
    labels = {"24h": "Last 24h", "7d": "Last 7 days", "30d": "Last 30 days"}
    return labels.get(period.lower(), period)


def _fmt_n(n: int) -> str:
    return f"{n:,}"


def _fmt_pct(pct: float) -> str:
    return f"▼ {pct:.1f}%"


def _fmt_cost(c: float) -> str:
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"


# ---------------------------------------------------------------------------
# Core query
# ---------------------------------------------------------------------------


def _query_savings(period: str = "24h", model: Optional[str] = None) -> dict:
    """Query the monitor DB for compression savings data."""
    conn = _connect()
    if not conn:
        return {"error": "DB not found", "db": _MONITOR_DB}

    days = _period_days(period)
    clauses, params = [], []

    if days is not None:
        clauses.append("date(timestamp) >= date('now', ?)")
        params.append(f"-{days} days")
    if model:
        clauses.append("model = ?")
        params.append(model)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    row = conn.execute(
        f"""
        SELECT
            COUNT(*)                                           AS requests,
            COALESCE(AVG(input_tokens), 0)                    AS avg_raw,
            COALESCE(AVG(CASE WHEN compressed_tokens > 0
                              THEN compressed_tokens
                              ELSE input_tokens END), 0)      AS avg_compressed,
            COALESCE(SUM(input_tokens), 0)                    AS total_raw,
            COALESCE(SUM(CASE WHEN compressed_tokens > 0
                              THEN compressed_tokens
                              ELSE input_tokens END), 0)      AS total_compressed,
            COALESCE(SUM(estimated_cost), 0.0)                AS total_cost
        FROM requests {where}
        """,
        params,
    ).fetchone()
    conn.close()

    avg_raw = int(row["avg_raw"])
    avg_compressed = int(row["avg_compressed"])
    total_raw = int(row["total_raw"])
    total_compressed = int(row["total_compressed"])
    total_cost = float(row["total_cost"])
    requests = int(row["requests"])

    tokens_saved = max(0, total_raw - total_compressed)
    pct_saved = (tokens_saved / total_raw * 100.0) if total_raw > 0 else 0.0

    # Estimate $ saved: tokens_saved * (total_cost / total_compressed) if we have data
    # Use cost-per-token from actual spend: cost / compressed_tokens = rate, then * tokens_saved
    if total_compressed > 0 and total_cost > 0:
        cost_per_token = total_cost / total_compressed
        est_cost_saved = tokens_saved * cost_per_token
    else:
        est_cost_saved = 0.0

    # Calculate before/after costs
    cost_without_tokenpak = (
        total_raw * (total_cost / total_compressed) if total_compressed > 0 else 0.0
    )
    cost_with_tokenpak = total_cost
    cost_reduction_pct = (
        ((cost_without_tokenpak - cost_with_tokenpak) / cost_without_tokenpak * 100.0)
        if cost_without_tokenpak > 0
        else 0.0
    )

    return {
        "period": period,
        "requests": requests,
        "avg_raw_tokens": avg_raw,
        "avg_compressed_tokens": avg_compressed,
        "reduction_pct": round(pct_saved, 2),
        "tokens_saved_total": tokens_saved,
        "est_cost_saved_usd": round(est_cost_saved, 4),
        "cost_without_tokenpak": round(cost_without_tokenpak, 2),
        "cost_with_tokenpak": round(cost_with_tokenpak, 2),
        "cost_reduction_pct": round(cost_reduction_pct, 1),
    }


def _query_by_model(period: str = "24h") -> list[dict]:
    """Return per-model savings breakdown."""
    conn = _connect()
    if not conn:
        return []

    days = _period_days(period)
    clauses, params = [], []

    if days is not None:
        clauses.append("date(timestamp) >= date('now', ?)")
        params.append(f"-{days} days")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = conn.execute(
        f"""
        SELECT
            model,
            COUNT(*)                                           AS requests,
            COALESCE(AVG(input_tokens), 0)                    AS avg_raw,
            COALESCE(AVG(CASE WHEN compressed_tokens > 0
                              THEN compressed_tokens
                              ELSE input_tokens END), 0)      AS avg_compressed,
            COALESCE(SUM(input_tokens), 0)                    AS total_raw,
            COALESCE(SUM(CASE WHEN compressed_tokens > 0
                              THEN compressed_tokens
                              ELSE input_tokens END), 0)      AS total_compressed,
            COALESCE(SUM(estimated_cost), 0.0)                AS total_cost
        FROM requests {where}
        GROUP BY model
        ORDER BY total_raw DESC
        """,
        params,
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        total_raw = int(r["total_raw"])
        total_compressed = int(r["total_compressed"])
        tokens_saved = max(0, total_raw - total_compressed)
        pct_saved = (tokens_saved / total_raw * 100.0) if total_raw > 0 else 0.0
        total_cost = float(r["total_cost"])
        if total_compressed > 0 and total_cost > 0:
            est_saved = tokens_saved * (total_cost / total_compressed)
            cost_without = total_raw * (total_cost / total_compressed)
            cost_reduction_pct = (
                ((cost_without - total_cost) / cost_without * 100.0) if cost_without > 0 else 0.0
            )
        else:
            est_saved = 0.0
            cost_reduction_pct = 0.0
        result.append(
            {
                "model": r["model"],
                "requests": int(r["requests"]),
                "avg_raw_tokens": int(r["avg_raw"]),
                "avg_compressed_tokens": int(r["avg_compressed"]),
                "reduction_pct": round(pct_saved, 2),
                "tokens_saved_total": tokens_saved,
                "est_cost_saved_usd": round(est_saved, 4),
                "cost_reduction_pct": round(cost_reduction_pct, 1),
            }
        )
    return result


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _print_summary(data: dict, period: str) -> None:
    label = _period_label(period)
    avg_raw = data["avg_raw_tokens"]
    avg_comp = data["avg_compressed_tokens"]
    avg_saved = max(0, avg_raw - avg_comp)
    pct = data["reduction_pct"]
    tok_saved = data["tokens_saved_total"]
    cost_saved = data["est_cost_saved_usd"]
    requests = data["requests"]
    cost_without = data.get("cost_without_tokenpak", 0.0)
    cost_with = data.get("cost_with_tokenpak", 0.0)
    cost_reduction = data.get("cost_reduction_pct", 0.0)

    print(f"TOKENPAK  |  Savings Report ({label})")
    print(SEP)
    print()

    if requests == 0:
        print("  No requests recorded for this period.")
        print()
        return

    print(f"  📊  This Session ({label})")
    print(f"      Total requests:    {_fmt_n(requests)}")
    print()
    print("      Compression:")
    print(f"        Tokens trimmed:  {_fmt_n(tok_saved)} ({_fmt_pct(pct)})")
    print(f"        Est. saved:      {_fmt_cost(cost_saved)}")
    print()
    if cost_without > 0:
        print(f"      💰 TOTAL SAVED:    {_fmt_cost(cost_saved)}")
        print(f"      📈 Without TokenPak: {_fmt_cost(cost_without)}")
        print(f"      📉 With TokenPak:    {_fmt_cost(cost_with)}")
        print(f"      🔥 Reduction:        {cost_reduction:.0f}%")
    print()
    print("  💡 Enable more modules for higher savings:")
    print("     tokenpak config profile aggressive")
    print()


def _print_verbose(rows: list[dict], period: str) -> None:
    if not rows:
        print("  No per-model data available.")
        print()
        return

    label = _period_label(period)
    print(f"  Per-Model Breakdown  ({label})\n  {SEP}")
    print(
        f"  {'MODEL':<34} {'REQS':>6} {'RAW AVG':>10} {'COMP AVG':>10} {'SAVED':>10} {'%':>7} {'$ SAVED':>10}"
    )
    print(f"  {'─' * 34} {'─' * 6} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 7} {'─' * 10}")

    for r in rows:
        print(
            f"  {r['model'][:34]:<34}"
            f" {_fmt_n(r['requests']):>6}"
            f" {_fmt_n(r['avg_raw_tokens']):>10}"
            f" {_fmt_n(r['avg_compressed_tokens']):>10}"
            f" {_fmt_n(r['tokens_saved_total']):>10}"
            f" {_fmt_pct(r['reduction_pct']):>7}"
            f" {_fmt_cost(r['est_cost_saved_usd']):>10}"
        )
    print()


# ---------------------------------------------------------------------------
# Main command entry point
# ---------------------------------------------------------------------------


def run_savings_cmd(args) -> None:
    """Dispatch handler for 'tokenpak savings'."""
    period = getattr(args, "period", "24h") or "24h"
    verbose = getattr(args, "verbose", False)
    as_json = getattr(args, "json", False) or getattr(args, "as_json", False)

    data = _query_savings(period=period)

    if "error" in data:
        if as_json:
            print(json.dumps(data, indent=2))
        else:
            print(f"TOKENPAK  |  Efficiency Report\n{SEP}\n")
            print(f"  ✖ No data available  ({data['error']})")
            print(f"    DB path: {data['db']}")
            print()
        return

    if as_json:
        output = {"summary": data}
        if verbose:
            output["by_model"] = _query_by_model(period=period)
        print(json.dumps(output, indent=2))
        return

    _print_summary(data, period)

    if verbose:
        rows = _query_by_model(period=period)
        _print_verbose(rows, period)
