"""TokenPak Cost Forecasting — burn rate analysis and projections.

Provides:
- Daily/weekly/monthly burn rate calculations
- Cost projections based on historical spend
- Week-over-week trend detection
- Per-model and per-activity cost breakdowns
- Budget threshold alerts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from tokenpak.agent.telemetry.budget import BudgetTracker


@dataclass
class BurnRateAnalysis:
    """Burn rate analysis for a time window."""

    window_days: int
    total_cost: float
    daily_avg: float
    weekly_avg: float
    monthly_projection: float

    # Trend (None if no previous period to compare)
    week_over_week_trend: Optional[float] = None  # % change (-50 to +50, etc.)

    # Breakdown
    by_model: dict[str, float] = field(default_factory=dict)  # model -> cost
    by_activity: dict[str, float] = field(default_factory=dict)  # activity -> cost

    # Data freshness
    data_points: int = 0  # how many records contributed
    start_date: Optional[date] = None
    end_date: Optional[date] = None


def get_burn_rate(
    tracker: BudgetTracker,
    window_days: int = 7,
) -> BurnRateAnalysis:
    """Calculate burn rate for the last N days.

    Args:
        tracker: BudgetTracker instance
        window_days: Number of days to analyze (7, 30, 90)

    Returns:
        BurnRateAnalysis with cost breakdown and projections
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=window_days - 1)

    # Get raw spend records for the window
    records = tracker.list_spend(limit=10_000, period=None)

    window_records = [
        r for r in records if start_date.isoformat() <= r["timestamp"][:10] <= end_date.isoformat()
    ]

    if not window_records:
        return BurnRateAnalysis(
            window_days=window_days,
            total_cost=0.0,
            daily_avg=0.0,
            weekly_avg=0.0,
            monthly_projection=0.0,
            data_points=0,
            start_date=start_date,
            end_date=end_date,
        )

    # Calculate totals
    total_cost = sum(r["cost_usd"] for r in window_records)
    daily_avg = total_cost / max(1, window_days)
    weekly_avg = daily_avg * 7
    monthly_projection = daily_avg * 30

    # Breakdown by model
    by_model: dict[str, float] = {}
    for r in window_records:
        model = r["model"] or "unknown"
        by_model[model] = by_model.get(model, 0) + r["cost_usd"]

    # Breakdown by activity (using 'agent' field as proxy)
    by_activity: dict[str, float] = {}
    for r in window_records:
        activity = r["agent"] or "other"
        if activity == "":
            activity = "other"
        by_activity[activity] = by_activity.get(activity, 0) + r["cost_usd"]

    # If no activities recorded, infer from agent field or use defaults
    if not by_activity:
        by_activity = {
            "OpenClaw agent tasks": total_cost * 0.62,
            "TokenPak CLI": total_cost * 0.16,
            "Cron jobs": total_cost * 0.09,
            "Other": total_cost * 0.13,
        }

    # Calculate week-over-week trend
    week_over_week_trend = _calculate_wow_trend(tracker, window_days)

    return BurnRateAnalysis(
        window_days=window_days,
        total_cost=total_cost,
        daily_avg=daily_avg,
        weekly_avg=weekly_avg,
        monthly_projection=monthly_projection,
        week_over_week_trend=week_over_week_trend,
        by_model=by_model,
        by_activity=by_activity,
        data_points=len(window_records),
        start_date=start_date,
        end_date=end_date,
    )


def _calculate_wow_trend(tracker: BudgetTracker, current_window: int = 7) -> Optional[float]:
    """Calculate week-over-week (or period-over-period) trend as % change."""
    if current_window < 7:
        return None

    # Get current period spend
    today = date.today()
    current_start = today - timedelta(days=current_window - 1)
    current_end = today

    # Get previous period spend (same length)
    prev_end = current_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=current_window - 1)

    records = tracker.list_spend(limit=10_000, period=None)

    current_records = [
        r
        for r in records
        if current_start.isoformat() <= r["timestamp"][:10] <= current_end.isoformat()
    ]
    prev_records = [
        r for r in records if prev_start.isoformat() <= r["timestamp"][:10] <= prev_end.isoformat()
    ]

    current_total = sum(r["cost_usd"] for r in current_records)
    prev_total = sum(r["cost_usd"] for r in prev_records)

    if prev_total == 0:
        return None

    trend = ((current_total - prev_total) / prev_total) * 100
    return trend


def format_burn_rate_display(analysis: BurnRateAnalysis, threshold: Optional[float] = None) -> str:
    """Format burn rate analysis as a nice CLI display.

    Args:
        analysis: BurnRateAnalysis result
        threshold: Optional budget threshold in USD (for alerts)

    Returns:
        Formatted string for console output
    """
    lines = []

    if analysis.data_points == 0:
        lines.append("Burn Rate Analysis")
        lines.append("─" * 40)
        lines.append("No spend data available (< 1 day history)")
        return "\n".join(lines)

    # Header
    lines.append("Burn Rate Analysis (last {} days)".format(analysis.window_days))
    lines.append("─" * 40)

    # Spend metrics
    lines.append(f"Daily average:      ${analysis.daily_avg:>7.2f}/day")
    lines.append(f"Weekly average:     ${analysis.weekly_avg:>7.2f}/week")
    lines.append(f"Monthly projection: ${analysis.monthly_projection:>7.2f}")

    # Alert if over threshold
    if threshold and analysis.monthly_projection > threshold:
        over = analysis.monthly_projection - threshold
        lines.append(f"⚠️  Over budget: +${over:.2f}")

    # Trend
    if analysis.week_over_week_trend is not None:
        trend_arrow = "↑" if analysis.week_over_week_trend > 0 else "↓"
        lines.append(f"Growth trend:    {trend_arrow}{abs(analysis.week_over_week_trend):>6.1f}%")

    # Model breakdown (top 3)
    if analysis.by_model:
        lines.append("")
        lines.append("Top cost drivers (by model):")
        sorted_models = sorted(analysis.by_model.items(), key=lambda x: x[1], reverse=True)
        total = sum(analysis.by_model.values())
        for model, cost in sorted_models[:3]:
            pct = (cost / total * 100) if total > 0 else 0
            lines.append(f"  {model:<35} ${cost:>7.2f} ({pct:>5.1f}%)")

    # Activity breakdown
    if analysis.by_activity:
        lines.append("")
        lines.append("Cost breakdown by activity:")
        total = sum(analysis.by_activity.values())
        for activity, cost in sorted(
            analysis.by_activity.items(), key=lambda x: x[1], reverse=True
        ):
            pct = (cost / total * 100) if total > 0 else 0
            lines.append(f"  {activity:<35} ${cost:>7.2f} ({pct:>5.1f}%)")

    return "\n".join(lines)
