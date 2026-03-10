"""budget command — spending limits, alerts, and forecasts."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, timedelta
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
    return [
        {"day": r["day"], "requests": r["requests"], "cost_usd": round(float(r["cost_usd"]), 6)}
        for r in rows
    ]


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
            "percent_used": (
                round(daily_spent / float(daily_limit) * 100, 1) if daily_limit else None
            ),
            "alert_at_percent": alert_pct,
        },
        "monthly": {
            "limit_usd": monthly_limit,
            "spent_usd": round(monthly_spent, 6),
            "remaining_usd": (
                round(float(monthly_limit) - monthly_spent, 4) if monthly_limit else None
            ),
            "percent_used": (
                round(monthly_spent / float(monthly_limit) * 100, 1) if monthly_limit else None
            ),
            "alert_at_percent": alert_pct,
        },
    }

    if raw:
        print(json.dumps(data, indent=2))
        return

    hard_stop = cfg.get("hard_stop", False)
    print("TOKENPAK  |  Budget Status")
    print(SEP)
    if hard_stop:
        print("  ⚠ Hard-stop ENABLED — requests will be blocked when limit exceeded")
        print()
    for period_name, info in data.items():
        label = period_name.title()
        spent = info["spent_usd"]
        limit = info["limit_usd"]

        if limit is None:
            print(f"  {label} Limit:        not set  (spent: {_fmt_cost(spent)})")  # type: ignore
        else:
            limit_f = float(limit)
            pct = info["percent_used"]
            remaining = info["remaining_usd"]
            bar_filled = int(pct / 5) if pct is not None else 0
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            warn_pct = float(cfg.get("warn_at_percent", 95.0))
            alert = (
                "🔴 OVER BUDGET"
                if pct is not None and pct >= 100
                else (
                    "🟠 ALERT"
                    if pct is not None and pct >= warn_pct
                    else ("⚠ WARNING" if pct is not None and pct >= alert_pct else "")
                )
            )
            print(f"  {label}:")
            print(f"    Limit:        {_fmt_cost(limit_f)}")  # type: ignore
            print(f"    Spent:        {_fmt_cost(spent)}")  # type: ignore
            print(f"    Remaining:    {_fmt_cost(remaining)}")  # type: ignore
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

    print("TOKENPAK  |  Spend Forecast")
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
        hard_stop = getattr(args, "hard_stop", None)
        if daily is not None:
            cfg["daily_limit_usd"] = daily
            changed.append(f"Daily limit: {_fmt_cost(daily)}")
        if monthly is not None:
            cfg["monthly_limit_usd"] = monthly
            changed.append(f"Monthly limit: {_fmt_cost(monthly)}")
        if hard_stop is not None:
            cfg["hard_stop"] = hard_stop
            changed.append(f"Hard-stop: {'enabled' if hard_stop else 'disabled'}")
        if not changed:
            print("Usage: tokenpak budget set --daily N --monthly N [--hard-stop]")
            return
        _save_config(cfg)
        for c in changed:
            print(f"✓ Set {c}")
        return

    if budget_cmd == "clear":
        cfg = _load_config()
        target = getattr(args, "target", None)
        if target == "daily":
            cfg.pop("daily_limit_usd", None)
            _save_config(cfg)
            print("✓ Cleared daily budget limit")
        elif target == "monthly":
            cfg.pop("monthly_limit_usd", None)
            _save_config(cfg)
            print("✓ Cleared monthly budget limit")
        else:
            # Clear all
            cfg.pop("daily_limit_usd", None)
            cfg.pop("monthly_limit_usd", None)
            cfg.pop("hard_stop", None)
            _save_config(cfg)
            print("✓ Cleared all budget limits")
        return

    if budget_cmd == "set-hard-stop":
        enabled = getattr(args, "enabled", True)
        cfg = _load_config()
        cfg["hard_stop"] = enabled
        _save_config(cfg)
        print(f"✓ Hard-stop {'enabled' if enabled else 'disabled'}")
        return

    if budget_cmd == "alert":
        threshold = getattr(args, "at", None)
        warn_threshold = getattr(args, "warn_at", None)
        if threshold is None and warn_threshold is None:
            cfg = _load_config()
            print(f"  Warning threshold (level 1): {cfg.get('alert_at_percent', 80.0):.0f}%")
            print(f"  Alert threshold (level 2):   {cfg.get('warn_at_percent', 95.0):.0f}%")
            return
        cfg = _load_config()
        if threshold is not None:
            cfg["alert_at_percent"] = threshold
            print(f"✓ Warning threshold set to {threshold}%")
        if warn_threshold is not None:
            cfg["warn_at_percent"] = warn_threshold
            print(f"✓ Alert threshold set to {warn_threshold}%")
        _save_config(cfg)
        return

    if budget_cmd == "history":
        days = getattr(args, "days", 30) or 30
        print_budget_history(days=days, raw=raw)
        return

    if budget_cmd == "forecast":
        print_budget_forecast(raw=raw)
        return

    if budget_cmd == "intelligence":
        print_budget_intelligence(raw=raw)
        return

    # Default: show status
    print_budget_status(raw=raw)


# ---------------------------------------------------------------------------
# Budget Intelligence (Pro+)
# ---------------------------------------------------------------------------

_MODEL_TIER_MAP: dict[str, str] = {
    # Expensive → mid → cheap ordering
    "claude-opus": "expensive",
    "claude-opus-4": "expensive",
    "claude-sonnet": "mid",
    "claude-sonnet-4": "mid",
    "gpt-4": "expensive",
    "gpt-4o": "mid",
    "gemini-pro": "mid",
    "claude-haiku": "cheap",
    "claude-haiku-4": "cheap",
    "gpt-3.5": "cheap",
    "gpt-4o-mini": "cheap",
    "gemini-flash": "cheap",
}

_CHEAPER_MODEL_MAP: dict[str, str] = {
    "claude-opus": "claude-sonnet",
    "claude-opus-4": "claude-sonnet-4",
    "claude-sonnet": "claude-haiku",
    "claude-sonnet-4": "claude-haiku-4",
    "gpt-4": "gpt-4o-mini",
    "gpt-4o": "gpt-4o-mini",
    "gemini-pro": "gemini-flash",
}

_COST_REDUCTION_ESTIMATE: dict[str, float] = {
    # Fraction of cost saved when switching to cheaper model
    "claude-opus": 0.80,
    "claude-opus-4": 0.80,
    "claude-sonnet": 0.75,
    "claude-sonnet-4": 0.75,
    "gpt-4": 0.85,
    "gpt-4o": 0.75,
    "gemini-pro": 0.70,
}


def _get_model_daily_avg(days: int = 7) -> list[dict]:
    """Return avg daily cost per model over the past N days."""
    conn = _connect()
    if not conn:
        return []
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    rows = conn.execute(
        """
        SELECT model,
               COUNT(*) AS requests,
               COALESCE(SUM(estimated_cost), 0.0) AS total_cost
        FROM requests
        WHERE date(timestamp) >= ?
        GROUP BY model
        ORDER BY total_cost DESC
        """,
        (since,),
    ).fetchall()
    conn.close()
    return [
        {
            "model": r["model"] or "unknown",
            "requests": r["requests"],
            "total_cost": round(float(r["total_cost"]), 6),
            "daily_avg": round(float(r["total_cost"]) / days, 6),
        }
        for r in rows
    ]


def _calc_burn_rate() -> dict:
    """Calculate daily/weekly/monthly burn rate from history."""
    h7 = _budget_history(days=7)
    h30 = _budget_history(days=30)
    today_cost = next((r["cost_usd"] for r in h7 if r["day"] == date.today().isoformat()), 0.0)
    daily_avg_7d = sum(r["cost_usd"] for r in h7) / max(len(h7), 1)
    weekly_avg = daily_avg_7d * 7
    monthly_projection = daily_avg_7d * 30
    # 7-day trend: compare last 7 days vs prior 7 days
    last7 = sum(r["cost_usd"] for r in h7)
    # Prior 7 days = days 8-14 ago (pull from h30)
    prior7_start = (date.today() - timedelta(days=14)).isoformat()
    prior7_end = (date.today() - timedelta(days=8)).isoformat()
    prior7 = sum(
        r["cost_usd"] for r in h30 if prior7_start <= r["day"] <= prior7_end
    )
    if prior7 > 0:
        trend_pct = (last7 - prior7) / prior7 * 100
    else:
        trend_pct = 0.0
    return {
        "today_usd": round(today_cost, 6),
        "daily_avg_7d": round(daily_avg_7d, 6),
        "weekly_avg": round(weekly_avg, 6),
        "monthly_projection": round(monthly_projection, 6),
        "trend_7d_pct": round(trend_pct, 1),
        "last7_total": round(last7, 6),
        "prior7_total": round(prior7, 6),
    }


def _calc_depletion_eta(monthly_limit: float | None, burn: dict) -> dict | None:
    """Calculate when budget will be depleted based on burn rate."""
    if not monthly_limit or monthly_limit <= 0:
        return None
    spent = _get_spent("monthly")
    remaining = monthly_limit - spent
    daily_avg = burn["daily_avg_7d"]
    if daily_avg <= 0:
        return {"days_remaining": None, "eta_date": None, "remaining_usd": round(remaining, 4)}
    days_to_depletion = remaining / daily_avg
    eta_date = date.today() + timedelta(days=days_to_depletion)
    return {
        "days_remaining": round(days_to_depletion, 1),
        "eta_date": eta_date.strftime("%b %-d"),
        "eta_iso": eta_date.isoformat(),
        "remaining_usd": round(remaining, 4),
    }


def _generate_suggestions(burn: dict, model_breakdown: list[dict]) -> list[str]:
    """Generate actionable throttle suggestions based on usage patterns."""
    suggestions: list[str] = []
    daily_avg = burn["daily_avg_7d"]
    # Suggestion 1: Switch expensive models to cheaper alternatives
    for entry in model_breakdown:
        model_key = entry["model"].lower()
        # Find matching tier key
        match = None
        for k in _CHEAPER_MODEL_MAP:
            if k in model_key:
                match = k
                break
        if match and entry["daily_avg"] > 0.01:
            cheaper = _CHEAPER_MODEL_MAP[match]
            reduction = _COST_REDUCTION_ESTIMATE.get(match, 0.70)
            savings = entry["daily_avg"] * reduction
            suggestions.append(
                f"Switch {entry['model']} → {cheaper} on low-priority queries "
                f"(-{_fmt_cost(savings)}/day)"
            )
    # Suggestion 2: Enable compression if not already implied
    if daily_avg > 0.05 and not suggestions:
        suggestions.append(
            f"Enable aggressive compression on high-volume agents "
            f"(est. -{_fmt_cost(daily_avg * 0.25)}/day)"
        )
    # Suggestion 3: Trend-based warning
    if burn["trend_7d_pct"] > 20:
        suggestions.append(
            f"Spend is up {burn['trend_7d_pct']:.1f}% vs last week — review recent agent activity"
        )
    # Limit to top 3
    return suggestions[:3]


def print_budget_intelligence(raw: bool = False) -> None:
    """Show Pro-tier budget intelligence: burn rate, ETA, trend, suggestions."""
    from tokenpak.agent.license.activation import is_pro

    if not is_pro():
        print("⚠ Budget Intelligence requires a Pro (or higher) license.")
        print("  Run: tokenpak license activate <key>")
        return

    cfg = _load_config()
    monthly_limit = cfg.get("monthly_limit_usd")
    monthly_limit_f = float(monthly_limit) if monthly_limit else None
    monthly_spent = _get_spent("monthly")

    burn = _calc_burn_rate()
    eta = _calc_depletion_eta(monthly_limit_f, burn)
    model_breakdown = _get_model_daily_avg(days=7)
    suggestions = _generate_suggestions(burn, model_breakdown)

    # Trend arrow
    trend_pct = burn["trend_7d_pct"]
    if abs(trend_pct) < 1.0:
        trend_str = f"→ flat"
    elif trend_pct > 0:
        trend_str = f"▲ {trend_pct:.1f}% vs last week"
    else:
        trend_str = f"▼ {abs(trend_pct):.1f}% vs last week"

    if raw:
        output = {
            "monthly_budget_usd": monthly_limit_f,
            "spent_mtd_usd": round(monthly_spent, 4),
            "remaining_usd": round(monthly_limit_f - monthly_spent, 4) if monthly_limit_f else None,
            "burn_rate": {
                "daily_avg_7d": burn["daily_avg_7d"],
                "weekly_avg": burn["weekly_avg"],
                "monthly_projection": burn["monthly_projection"],
            },
            "depletion_eta": eta,
            "trend_7d_pct": trend_pct,
            "suggestions": suggestions,
        }
        print(json.dumps(output, indent=2))
        return

    print("TOKENPAK  |  Budget Intelligence")
    print(SEP)
    print()
    # Budget overview
    if monthly_limit_f:
        remaining = monthly_limit_f - monthly_spent
        print(f"  {'Monthly Budget:':<26}{_fmt_cost(monthly_limit_f)}")
        print(f"  {'Spent (MTD):':<26}{_fmt_cost(monthly_spent)}")
        print(f"  {'Remaining:':<26}{_fmt_cost(remaining)}")
    else:
        print(f"  {'Spent (MTD):':<26}{_fmt_cost(monthly_spent)}")
        print(f"  {'Monthly Budget:':<26}not set")
    print()
    # Burn rate + ETA
    print(f"  {'Burn Rate:':<26}{_fmt_cost(burn['daily_avg_7d'])}/day")
    if eta and eta.get("days_remaining") is not None:
        print(
            f"  {'Budget Depletion ETA:':<26}"
            f"{eta['days_remaining']:.1f} days ({eta['eta_date']})"
        )
    elif monthly_limit_f:
        print(f"  {'Budget Depletion ETA:':<26}N/A (burn rate too low)")
    print()
    print(f"  {'Trend (7d):':<26}{trend_str}")
    print()
    # Suggestions
    if suggestions:
        print("  Suggestions:")
        for i, s in enumerate(suggestions, 1):
            print(f"  {i}. {s}")
    else:
        print("  No suggestions — usage looks optimal.")
    print()
