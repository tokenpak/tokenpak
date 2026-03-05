"""budget command — spending limits, alerts, and forecasts."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

SEP = "────────────────────────────────────────"

# Reuse monitor DB for spend queries
_MONITOR_DB = os.environ.get(
    "TOKENPAK_DB",
    os.path.expanduser("~/.openclaw/workspace/.ocp/monitor.db"),
)
_BUDGET_CONFIG = Path("~/.tokenpak/budget_config.yaml").expanduser()


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if not _BUDGET_CONFIG.exists():
        return {}
    try:
        import yaml
        with open(_BUDGET_CONFIG) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Fallback: simple key: value parser
        data = {}
        for line in _BUDGET_CONFIG.read_text().splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, _, v = line.partition(":")
                data[k.strip()] = v.strip()
        return data
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    _BUDGET_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
        with open(_BUDGET_CONFIG, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False)
    except ImportError:
        lines = [f"{k}: {v}" for k, v in cfg.items()]
        _BUDGET_CONFIG.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Spend queries (from monitor DB)
# ---------------------------------------------------------------------------

def _connect() -> Optional[sqlite3.Connection]:
    db = Path(_MONITOR_DB)
    if not db.exists():
        return None
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def _get_spent(period: str) -> float:
    conn = _connect()
    if not conn:
        return 0.0
    today = date.today()
    if period == "daily":
        where, params = "date(timestamp) = ?", [today.isoformat()]
    elif period == "monthly":
        where, params = "strftime('%Y-%m', timestamp) = ?", [today.strftime("%Y-%m")]
    else:
        return 0.0
    row = conn.execute(
        f"SELECT COALESCE(SUM(estimated_cost), 0.0) FROM requests WHERE {where}", params
    ).fetchone()
    conn.close()
    return float(row[0])


def _fmt_cost(c: float) -> str:
    if c < 0.01:
        return f"${c:.4f}"
    return f"${c:.2f}"


# ---------------------------------------------------------------------------
# Budget history
# ---------------------------------------------------------------------------

def _budget_history(days: int = 30) -> list[dict]:
    """Return daily spend for the past N days."""
    conn = _connect()
    if not conn:
        return []
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        """
        SELECT date(timestamp) AS day,
               COUNT(*) AS requests,
               COALESCE(SUM(estimated_cost), 0.0) AS cost_usd
        FROM requests
        WHERE date(timestamp) >= ?
        GROUP BY day
        ORDER BY day
        """,
        (since,),
    ).fetchall()
    conn.close()
    return [{"day": r["day"], "requests": r["requests"], "cost_usd": round(float(r["cost_usd"]), 6)} for r in rows]


def _budget_forecast(period: str = "monthly") -> dict:
    """Project remaining spend based on recent daily average."""
    history = _budget_history(days=7)
    if not history:
        return {
            "period": period,
            "daily_avg_usd": 0.0,
            "basis_days": 0,
            "days_remaining": 0,
            "already_spent_usd": 0.0,
            "projected_additional_usd": 0.0,
            "projected_total_usd": 0.0,
        }
    avg = sum(r["cost_usd"] for r in history) / len(history)
    today = date.today()
    if period == "monthly":
        # Days remaining in month (excluding today)
        import calendar
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        days_remaining = days_in_month - today.day
        already_spent = _get_spent("monthly")
    else:
        days_remaining = 0
        already_spent = _get_spent("daily")
    projected_additional = avg * days_remaining
    return {
        "period": period,
        "daily_avg_usd": round(avg, 6),
        "basis_days": len(history),
        "days_remaining": days_remaining,
        "already_spent_usd": round(already_spent, 6),
        "projected_additional_usd": round(projected_additional, 6),
        "projected_total_usd": round(already_spent + projected_additional, 6),
    }


# ---------------------------------------------------------------------------
# Display functions
# ---------------------------------------------------------------------------

def print_budget_status(raw: bool = False) -> None:
    """Show current budget vs. spend."""
    cfg = _load_config()
    daily_limit = cfg.get("daily_limit_usd")
    monthly_limit = cfg.get("monthly_limit_usd")
    alert_pct = float(cfg.get("alert_at_percent", 80.0))

    daily_spent = _get_spent("daily")
    monthly_spent = _get_spent("monthly")

    data = {
        "daily": {
            "limit_usd": daily_limit,
            "spent_usd": round(daily_spent, 6),
            "remaining_usd": round(float(daily_limit) - daily_spent, 4) if daily_limit else None,
            "percent_used": round(daily_spent / float(daily_limit) * 100, 1) if daily_limit else None,
            "alert_at_percent": alert_pct,
        },
        "monthly": {
            "limit_usd": monthly_limit,
            "spent_usd": round(monthly_spent, 6),
            "remaining_usd": round(float(monthly_limit) - monthly_spent, 4) if monthly_limit else None,
            "percent_used": round(monthly_spent / float(monthly_limit) * 100, 1) if monthly_limit else None,
            "alert_at_percent": alert_pct,
        },
    }

    if raw:
        print(json.dumps(data, indent=2))
        return

    print(f"TOKENPAK  |  Budget Status")
    print(SEP)
    for period_name, info in data.items():
        label = period_name.title()
        spent = info["spent_usd"]
        limit = info["limit_usd"]

        if limit is None:
            print(f"  {label} Limit:        not set  (spent: {_fmt_cost(spent)})")
        else:
            limit_f = float(limit)
            pct = info["percent_used"]
            remaining = info["remaining_usd"]
            bar_filled = int(pct / 5) if pct is not None else 0
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            alert = "⚠ ALERT" if pct is not None and pct >= alert_pct else ""
            print(f"  {label}:")
            print(f"    Limit:        {_fmt_cost(limit_f)}")
            print(f"    Spent:        {_fmt_cost(spent)}")
            print(f"    Remaining:    {_fmt_cost(remaining)}")
            print(f"    Progress:     [{bar}] {pct:.1f}% {alert}")
        print()


def print_budget_history(days: int = 30, raw: bool = False) -> None:
    """Show daily spend history vs budget."""
    cfg = _load_config()
    daily_limit = cfg.get("daily_limit_usd")
    history = _budget_history(days=days)

    if raw:
        print(json.dumps(history, indent=2))
        return

    print(f"TOKENPAK  |  Budget History — Last {days} Days")
    print(SEP)
    if not history:
        print("  No data available.")
        print()
        return
    print(f"  {'Date':<14}{'Requests':>10}{'Spent':>12}{'vs Limit':>12}")
    print(f"  {'-'*14}{'-'*10}{'-'*12}{'-'*12}")
    for r in history:
        if daily_limit:
            pct = r["cost_usd"] / float(daily_limit) * 100
            vs = f"{pct:.0f}%"
        else:
            vs = "—"
        print(f"  {r['day']:<14}{r['requests']:>10}{_fmt_cost(r['cost_usd']):>12}{vs:>12}")
    total = sum(r["cost_usd"] for r in history)
    print(f"  {'TOTAL':<14}{'':>10}{_fmt_cost(total):>12}")
    print()


def print_budget_forecast(raw: bool = False) -> None:
    """Show projected spend for this month."""
    data = _budget_forecast("monthly")
    cfg = _load_config()
    monthly_limit = cfg.get("monthly_limit_usd")

    if raw:
        if monthly_limit:
            data["monthly_limit_usd"] = float(monthly_limit)
        print(json.dumps(data, indent=2))
        return

    print(f"TOKENPAK  |  Spend Forecast")
    print(SEP)
    print(f"  {'Daily Avg (7d):':<28}{_fmt_cost(data['daily_avg_usd'])}")
    print(f"  {'Basis:':<28}{data['basis_days']} days")
    print(f"  {'Days Remaining (month):':<28}{data['days_remaining']}")
    print(f"  {'Already Spent:':<28}{_fmt_cost(data['already_spent_usd'])}")
    print(f"  {'Projected Additional:':<28}{_fmt_cost(data['projected_additional_usd'])}")
    print(f"  {'Projected Total:':<28}{_fmt_cost(data['projected_total_usd'])}")
    if monthly_limit:
        limit_f = float(monthly_limit)
        pct = data["projected_total_usd"] / limit_f * 100 if limit_f else 0
        status = "⚠ OVER BUDGET" if pct > 100 else ("⚠ CLOSE" if pct > 80 else "✓ OK")
        print(f"  {'Monthly Limit:':<28}{_fmt_cost(limit_f)}")
        print(f"  {'Forecast vs Limit:':<28}{pct:.1f}% {status}")
    print()


# ---------------------------------------------------------------------------
# Argparse dispatch (wired into main.py)
# ---------------------------------------------------------------------------

def run_budget_cmd(args) -> None:
    """Dispatch handler for 'tokenpak budget' from main.py argparse."""
    raw = getattr(args, "raw", False)
    budget_cmd = getattr(args, "budget_cmd", None)

    if budget_cmd == "set":
        cfg = _load_config()
        changed = []
        daily = getattr(args, "daily", None)
        monthly = getattr(args, "monthly", None)
        if daily is not None:
            cfg["daily_limit_usd"] = daily
            changed.append(f"Daily limit: {_fmt_cost(daily)}")
        if monthly is not None:
            cfg["monthly_limit_usd"] = monthly
            changed.append(f"Monthly limit: {_fmt_cost(monthly)}")
        if not changed:
            print("Usage: tokenpak budget set --daily N --monthly N")
            return
        _save_config(cfg)
        for c in changed:
            print(f"✓ Set {c}")
        return

    if budget_cmd == "alert":
        threshold = getattr(args, "at", None)
        if threshold is None:
            print("Usage: tokenpak budget alert --at N")
            return
        cfg = _load_config()
        cfg["alert_at_percent"] = threshold
        _save_config(cfg)
        print(f"✓ Alert threshold set to {threshold}%")
        return

    if budget_cmd == "history":
        days = getattr(args, "days", 30) or 30
        print_budget_history(days=days, raw=raw)
        return

    if budget_cmd == "forecast":
        print_budget_forecast(raw=raw)
        return

    # Default: show status
    print_budget_status(raw=raw)
