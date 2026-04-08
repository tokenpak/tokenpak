"""cost command — token usage and cost reporting."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_MONITOR_DB = os.environ.get(
    "TOKENPAK_DB",
    os.path.expanduser("~/.openclaw/workspace/.ocp/monitor.db"),
)

SEP = "────────────────────────────────────────"


def _connect() -> Optional[sqlite3.Connection]:
    db = Path(_MONITOR_DB)
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _period_clause(period: str) -> tuple[str, list]:
    """Return (WHERE clause, params) for named periods."""
    today = date.today()
    if period == "today":
        return "date(timestamp) = ?", [today.isoformat()]
    if period == "yesterday":
        return "date(timestamp) = ?", [(today - timedelta(days=1)).isoformat()]
    if period == "week":
        since = (today - timedelta(days=6)).isoformat()
        return "date(timestamp) >= ?", [since]
    if period == "month":
        return "strftime('%Y-%m', timestamp) = ?", [today.strftime("%Y-%m")]
    # default: all time
    return "1=1", []


def _period_params(period: str) -> list:
    """Return bound parameters for the given period (no WHERE fragment)."""
    today = date.today()
    if period == "today":
        return [today.isoformat()]
    if period == "yesterday":
        return [(today - timedelta(days=1)).isoformat()]
    if period == "week":
        return [(today - timedelta(days=6)).isoformat()]
    if period == "month":
        return [today.strftime("%Y-%m")]
    return []


# ---------------------------------------------------------------------------
# Pre-built parameterized SQL — indexed by period.
# Using a dict lookup (not f-strings) keeps bandit B608 clean while
# keeping all user-supplied values bound via ? placeholders.
# ---------------------------------------------------------------------------

_SUMMARY_SQL: dict[str, str] = {
    "today": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE date(timestamp) = ?"
    ),
    "yesterday": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE date(timestamp) = ?"
    ),
    "week": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE date(timestamp) >= ?"
    ),
    "month": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE strftime('%Y-%m', timestamp) = ?"
    ),
}
_SUMMARY_SQL_DEFAULT = (
    "SELECT COUNT(*) AS requests,"
    " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
    " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
    " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
    " FROM requests"
)
_SUMMARY_SQL_MODEL: dict[str, str] = {
    "today": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE date(timestamp) = ? AND model = ?"
    ),
    "yesterday": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE date(timestamp) = ? AND model = ?"
    ),
    "week": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE date(timestamp) >= ? AND model = ?"
    ),
    "month": (
        "SELECT COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
        " FROM requests WHERE strftime('%Y-%m', timestamp) = ? AND model = ?"
    ),
}
_SUMMARY_SQL_MODEL_DEFAULT = (
    "SELECT COUNT(*) AS requests,"
    " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
    " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
    " COALESCE(SUM(estimated_cost), 0.0) AS total_cost"
    " FROM requests WHERE model = ?"
)

_BY_MODEL_SQL: dict[str, str] = {
    "today": (
        "SELECT model, COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE date(timestamp) = ?"
        " GROUP BY model ORDER BY cost_usd DESC"
    ),
    "yesterday": (
        "SELECT model, COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE date(timestamp) = ?"
        " GROUP BY model ORDER BY cost_usd DESC"
    ),
    "week": (
        "SELECT model, COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE date(timestamp) >= ?"
        " GROUP BY model ORDER BY cost_usd DESC"
    ),
    "month": (
        "SELECT model, COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE strftime('%Y-%m', timestamp) = ?"
        " GROUP BY model ORDER BY cost_usd DESC"
    ),
}
_BY_MODEL_SQL_DEFAULT = (
    "SELECT model, COUNT(*) AS requests,"
    " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
    " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
    " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
    " FROM requests GROUP BY model ORDER BY cost_usd DESC"
)

_BY_AGENT_SQL: dict[str, str] = {
    "today": (
        "SELECT COALESCE(NULLIF(endpoint, ''), 'unknown') AS agent,"
        " COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE date(timestamp) = ?"
        " GROUP BY agent ORDER BY cost_usd DESC"
    ),
    "yesterday": (
        "SELECT COALESCE(NULLIF(endpoint, ''), 'unknown') AS agent,"
        " COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE date(timestamp) = ?"
        " GROUP BY agent ORDER BY cost_usd DESC"
    ),
    "week": (
        "SELECT COALESCE(NULLIF(endpoint, ''), 'unknown') AS agent,"
        " COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE date(timestamp) >= ?"
        " GROUP BY agent ORDER BY cost_usd DESC"
    ),
    "month": (
        "SELECT COALESCE(NULLIF(endpoint, ''), 'unknown') AS agent,"
        " COUNT(*) AS requests,"
        " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
        " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
        " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
        " FROM requests WHERE strftime('%Y-%m', timestamp) = ?"
        " GROUP BY agent ORDER BY cost_usd DESC"
    ),
}
_BY_AGENT_SQL_DEFAULT = (
    "SELECT COALESCE(NULLIF(endpoint, ''), 'unknown') AS agent,"
    " COUNT(*) AS requests,"
    " COALESCE(SUM(input_tokens), 0) AS input_tokens,"
    " COALESCE(SUM(output_tokens), 0) AS output_tokens,"
    " COALESCE(SUM(estimated_cost), 0.0) AS cost_usd"
    " FROM requests GROUP BY agent ORDER BY cost_usd DESC"
)

_EXPORT_SQL: dict[str, str] = {
    "today": (
        "SELECT timestamp, model, input_tokens, output_tokens, estimated_cost"
        " FROM requests WHERE date(timestamp) = ? ORDER BY timestamp"
    ),
    "yesterday": (
        "SELECT timestamp, model, input_tokens, output_tokens, estimated_cost"
        " FROM requests WHERE date(timestamp) = ? ORDER BY timestamp"
    ),
    "week": (
        "SELECT timestamp, model, input_tokens, output_tokens, estimated_cost"
        " FROM requests WHERE date(timestamp) >= ? ORDER BY timestamp"
    ),
    "month": (
        "SELECT timestamp, model, input_tokens, output_tokens, estimated_cost"
        " FROM requests WHERE strftime('%Y-%m', timestamp) = ? ORDER BY timestamp"
    ),
}
_EXPORT_SQL_DEFAULT = (
    "SELECT timestamp, model, input_tokens, output_tokens, estimated_cost"
    " FROM requests ORDER BY timestamp"
)


def _fmt_cost(c: float) -> str:
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"


def _fmt_n(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------------------
# Core query functions
# ---------------------------------------------------------------------------


def query_summary(period: str = "today", model: Optional[str] = None) -> dict:
    """Return aggregated cost summary for the period.

    Args:
        period: Time period (today, yesterday, week, month)
        model: Optional model name filter
    """
    conn = _connect()
    if not conn:
        return {"error": "DB not found", "db": _MONITOR_DB}
    if model:
        sql = _SUMMARY_SQL_MODEL.get(period, _SUMMARY_SQL_MODEL_DEFAULT)
        params = _period_params(period) + [model]
    else:
        sql = _SUMMARY_SQL.get(period, _SUMMARY_SQL_DEFAULT)
        params = _period_params(period)
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return {
        "period": period,
        "requests": row["requests"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "total_tokens": row["input_tokens"] + row["output_tokens"],
        "total_cost_usd": round(float(row["total_cost"]), 6),
        "model_filter": model,
    }


def query_by_model(period: str = "today") -> list[dict]:
    """Return per-model breakdown for the period."""
    conn = _connect()
    if not conn:
        return []
    sql = _BY_MODEL_SQL.get(period, _BY_MODEL_SQL_DEFAULT)
    params = _period_params(period)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {
            "model": r["model"],
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "total_tokens": r["input_tokens"] + r["output_tokens"],
            "cost_usd": round(float(r["cost_usd"]), 6),
        }
        for r in rows
    ]


def query_by_agent(period: str = "today") -> list[dict]:
    """Return per-agent breakdown using session_id field if available."""
    # The monitor DB doesn't have an explicit 'agent' column; we use endpoint
    # as a proxy, or fall back to compilation_mode grouping.
    conn = _connect()
    if not conn:
        return []
    sql = _BY_AGENT_SQL.get(period, _BY_AGENT_SQL_DEFAULT)
    params = _period_params(period)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        {
            "agent": r["agent"],
            "requests": r["requests"],
            "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"],
            "total_tokens": r["input_tokens"] + r["output_tokens"],
            "cost_usd": round(float(r["cost_usd"]), 6),
        }
        for r in rows
    ]


def export_csv_data(period: str = "today") -> str:
    """Return CSV string of all requests for the period."""
    conn = _connect()
    if not conn:
        return "timestamp,model,input_tokens,output_tokens,estimated_cost\n"
    sql = _EXPORT_SQL.get(period, _EXPORT_SQL_DEFAULT)
    params = _period_params(period)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "model", "input_tokens", "output_tokens", "estimated_cost"])
    for r in rows:
        w.writerow(
            [r["timestamp"], r["model"], r["input_tokens"], r["output_tokens"], r["estimated_cost"]]
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Display functions
# ---------------------------------------------------------------------------


def _period_label(period: str) -> str:
    labels = {
        "today": "Today",
        "yesterday": "Yesterday",
        "week": "Last 7 Days",
        "month": "This Month",
    }
    return labels.get(period, period.title())


def print_summary(period: str = "today", raw: bool = False) -> None:
    """Print cost summary for the period."""
    data = query_summary(period)
    if "error" in data:
        print(f"✖ {data['error']}: {data.get('db', '')}")
        return
    if raw:
        print(json.dumps(data, indent=2))
        return
    label = _period_label(period)
    print(f"TOKENPAK  |  Cost — {label}")
    print(SEP)
    print(f"  {'Requests:':<24}{_fmt_n(data['requests'])}")
    print(f"  {'Input Tokens:':<24}{_fmt_n(data['input_tokens'])}")
    print(f"  {'Output Tokens:':<24}{_fmt_n(data['output_tokens'])}")
    print(f"  {'Total Tokens:':<24}{_fmt_n(data['total_tokens'])}")
    print(f"  {'Total Cost:':<24}{_fmt_cost(data['total_cost_usd'])}")
    print()


def print_by_model(period: str = "today", raw: bool = False) -> None:
    """Print per-model cost breakdown."""
    rows = query_by_model(period)
    if raw:
        print(json.dumps(rows, indent=2))
        return
    label = _period_label(period)
    print(f"TOKENPAK  |  Cost by Model — {label}")
    print(SEP)
    if not rows:
        print("  No data for this period.")
        print()
        return
    print(f"  {'Model':<32}{'Requests':>10}{'Tokens':>12}{'Cost':>12}")
    print(f"  {'-'*32}{'-'*10}{'-'*12}{'-'*12}")
    for r in rows:
        print(
            f"  {r['model']:<32}{_fmt_n(r['requests']):>10}{_fmt_n(r['total_tokens']):>12}{_fmt_cost(r['cost_usd']):>12}"
        )
    total_cost = sum(r["cost_usd"] for r in rows)
    print(f"  {'':32}{'':10}{'':12}{_fmt_cost(total_cost):>12}")
    print()


def print_by_agent(period: str = "today", raw: bool = False) -> None:
    """Print per-agent (endpoint) cost breakdown."""
    rows = query_by_agent(period)
    if raw:
        print(json.dumps(rows, indent=2))
        return
    label = _period_label(period)
    print(f"TOKENPAK  |  Cost by Agent — {label}")
    print(SEP)
    if not rows:
        print("  No data for this period.")
        print()
        return
    print(f"  {'Agent/Endpoint':<36}{'Requests':>10}{'Cost':>12}")
    print(f"  {'-'*36}{'-'*10}{'-'*12}")
    for r in rows:
        print(f"  {r['agent']:<36}{_fmt_n(r['requests']):>10}{_fmt_cost(r['cost_usd']):>12}")
    print()


# ---------------------------------------------------------------------------
# CLI (argparse-based, wired into main.py)
# ---------------------------------------------------------------------------


def run_cost_cmd(args) -> None:
    """Dispatch handler for 'tokenpak cost' from main.py argparse."""
    raw = getattr(args, "raw", False)
    export = getattr(args, "export", None)
    model_filter = getattr(args, "model", None)

    # Period selection
    if getattr(args, "yesterday", False):
        period = "yesterday"
    elif getattr(args, "week", False):
        period = "week"
    elif getattr(args, "month", False):
        period = "month"
    else:
        period = "today"

    if export == "csv":
        print(export_csv_data(period), end="")
        return

    if getattr(args, "by_model", False):
        print_by_model(period, raw=raw)
        return

    if getattr(args, "by_agent", False):
        print_by_agent(period, raw=raw)
        return

    # Default: summary (with optional model filter)
    if model_filter:
        data = query_summary(period, model=model_filter)
        if raw:
            import json

            print(json.dumps(data, indent=2))
        else:
            label = _period_label(period)
            print(f"TOKENPAK  |  Cost — {label} (model: {model_filter})")
            print(SEP)
            if "error" in data:
                print(f"  ✖ {data['error']}: {data.get('db', '')}")
            else:
                print(f"  {'Requests:':<24}{_fmt_n(data['requests'])}")
                print(f"  {'Input Tokens:':<24}{_fmt_n(data['input_tokens'])}")
                print(f"  {'Output Tokens:':<24}{_fmt_n(data['output_tokens'])}")
                print(f"  {'Total Tokens:':<24}{_fmt_n(data['total_tokens'])}")
                print(f"  {'Total Cost:':<24}{_fmt_cost(data['total_cost_usd'])}")
                print()
        return

    print_summary(period, raw=raw)
    if not raw:
        print_by_model(period, raw=False)


# ---------------------------------------------------------------------------
# Click interface (optional, for future Click-based CLI)
# ---------------------------------------------------------------------------

try:
    import click

    @click.group("cost")
    def cost_group():
        """Show token usage and cost reports."""
        pass

    @cost_group.command("today")
    @click.option("--by-model", is_flag=True, help="Break down by model")
    @click.option("--by-agent", is_flag=True, help="Break down by agent/endpoint")
    @click.option("--export", type=click.Choice(["csv"]), default=None)
    @click.option("--raw", is_flag=True)
    def cost_today(by_model, by_agent, export, raw):
        """Today's spend."""
        _dispatch("today", by_model, by_agent, export, raw)

    @cost_group.command("yesterday")
    @click.option("--by-model", is_flag=True)
    @click.option("--raw", is_flag=True)
    def cost_yesterday(by_model, raw):
        """Yesterday's spend."""
        _dispatch("yesterday", by_model, False, None, raw)

    @cost_group.command("week")
    @click.option("--by-model", is_flag=True)
    @click.option("--by-agent", is_flag=True)
    @click.option("--export", type=click.Choice(["csv"]), default=None)
    @click.option("--raw", is_flag=True)
    def cost_week(by_model, by_agent, export, raw):
        """Last 7 days spend."""
        _dispatch("week", by_model, by_agent, export, raw)

    @cost_group.command("month")
    @click.option("--by-model", is_flag=True)
    @click.option("--by-agent", is_flag=True)
    @click.option("--export", type=click.Choice(["csv"]), default=None)
    @click.option("--raw", is_flag=True)
    def cost_month(by_model, by_agent, export, raw):
        """This month's spend."""
        _dispatch("month", by_model, by_agent, export, raw)

    def _dispatch(period, by_model, by_agent, export, raw):
        if export == "csv":
            print(export_csv_data(period), end="")
            return
        if by_model:
            print_by_model(period, raw=raw)
            return
        if by_agent:
            print_by_agent(period, raw=raw)
            return
        print_summary(period, raw=raw)
        if not raw:
            print_by_model(period, raw=False)

except ImportError:
    pass
