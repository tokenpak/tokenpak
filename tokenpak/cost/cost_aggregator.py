"""TokenPak Cost Aggregator — daily/weekly/monthly cost roll-ups with CSV export.

Builds on CostTracker (SQLite) to provide:
- Daily summaries grouped by date and model
- Multi-day aggregation windows (e.g. last 7 / 30 days)
- CSV export (date, model, requests, tokens, cost_usd)
- Budget burn-rate alarm: warn when any day exceeds 20% of monthly budget
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta
from typing import Optional

from tokenpak.telemetry.cost_tracker import CostTracker, get_cost_tracker

logger = logging.getLogger(__name__)

_DEFAULT_DAILY_ALARM_PCT = 20.0  # warn if daily > 20% of monthly budget


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class DailySummary:
    """Cost totals for a single day, optionally broken down by model."""

    def __init__(
        self,
        day: str,
        total_cost_usd: float,
        total_requests: int,
        total_tokens: int,
        by_model: Optional[list[dict]] = None,
    ):
        self.day = day  # ISO date string YYYY-MM-DD
        self.total_cost_usd = total_cost_usd
        self.total_requests = total_requests
        self.total_tokens = total_tokens
        self.by_model: list[dict] = by_model or []

    def __repr__(self) -> str:
        return (
            f"DailySummary(day={self.day!r}, cost=${self.total_cost_usd:.4f}, "
            f"requests={self.total_requests})"
        )


class BurnRateAlarm:
    """Fired when daily spend exceeds a % threshold of the monthly budget."""

    def __init__(
        self,
        day: str,
        daily_cost_usd: float,
        monthly_budget_usd: float,
        threshold_pct: float,
        actual_pct: float,
    ):
        self.day = day
        self.daily_cost_usd = daily_cost_usd
        self.monthly_budget_usd = monthly_budget_usd
        self.threshold_pct = threshold_pct
        self.actual_pct = actual_pct

    @property
    def message(self) -> str:
        return (
            f"⚠️  Burn-rate alarm [{self.day}]: "
            f"${self.daily_cost_usd:.4f} is {self.actual_pct:.1f}% of monthly budget "
            f"${self.monthly_budget_usd:.2f} (threshold: {self.threshold_pct:.0f}%)"
        )

    def __repr__(self) -> str:
        return f"BurnRateAlarm({self.day!r}, {self.actual_pct:.1f}%)"


# ---------------------------------------------------------------------------
# CostAggregator
# ---------------------------------------------------------------------------


class CostAggregator:
    """Aggregate cost data from CostTracker into daily/multi-day summaries.

    Usage::

        agg = CostAggregator()
        summaries = agg.daily_summaries(days=7)
        csv_text = agg.export_csv(days=7)
        alarms = agg.check_burn_rate(monthly_budget_usd=100.0)
    """

    def __init__(self, tracker: Optional[CostTracker] = None):
        self._tracker = tracker or get_cost_tracker()

    # -----------------------------------------------------------------------
    # Aggregation
    # -----------------------------------------------------------------------

    def daily_summaries(
        self, days: int = 7, *, end_date: Optional[date] = None
    ) -> list[DailySummary]:
        """Return list of DailySummary objects for the last *days* days.

        Args:
            days: Number of days to look back (inclusive of today).
            end_date: Last date in range (default: today).

        Returns:
            List ordered oldest → newest, one entry per day that has data.
            Days with no requests are omitted.
        """
        end = end_date or date.today()
        start = end - timedelta(days=days - 1)

        conn = self._tracker._conn()
        rows = conn.execute(
            """
            SELECT
                date(timestamp)                        AS day,
                model,
                COUNT(*)                               AS requests,
                COALESCE(SUM(prompt_tokens), 0)        AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)    AS completion_tokens,
                COALESCE(SUM(cost_usd), 0)             AS cost_usd
            FROM cost_requests
            WHERE date(timestamp) BETWEEN ? AND ?
            GROUP BY day, model
            ORDER BY day ASC, cost_usd DESC
            """,
            [start.isoformat(), end.isoformat()],
        ).fetchall()

        # Group by day
        by_day: dict[str, list[dict]] = {}
        for r in rows:
            day = r["day"]
            by_day.setdefault(day, []).append(
                {
                    "model": r["model"],
                    "requests": r["requests"],
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "total_tokens": r["prompt_tokens"] + r["completion_tokens"],
                    "cost_usd": round(float(r["cost_usd"]), 6),
                }
            )

        summaries = []
        for day_str, model_rows in sorted(by_day.items()):
            total_cost = sum(m["cost_usd"] for m in model_rows)
            total_requests = sum(m["requests"] for m in model_rows)
            total_tokens = sum(m["total_tokens"] for m in model_rows)
            summaries.append(
                DailySummary(
                    day=day_str,
                    total_cost_usd=round(total_cost, 6),
                    total_requests=total_requests,
                    total_tokens=total_tokens,
                    by_model=model_rows,
                )
            )
        return summaries

    def aggregate(self, days: int = 30) -> dict:
        """Return aggregate totals across the last *days* days.

        Returns:
            {
                "days": int,
                "total_cost_usd": float,
                "total_requests": int,
                "total_tokens": int,
                "avg_daily_cost_usd": float,
                "by_model": [{"model", "requests", "total_tokens", "cost_usd"}, ...],
            }
        """
        summaries = self.daily_summaries(days=days)
        if not summaries:
            return {
                "days": days,
                "total_cost_usd": 0.0,
                "total_requests": 0,
                "total_tokens": 0,
                "avg_daily_cost_usd": 0.0,
                "by_model": [],
            }

        total_cost = sum(s.total_cost_usd for s in summaries)
        total_requests = sum(s.total_requests for s in summaries)
        total_tokens = sum(s.total_tokens for s in summaries)

        # Aggregate by_model across all days
        model_totals: dict[str, dict] = {}
        for s in summaries:
            for m in s.by_model:
                key = m["model"]
                if key not in model_totals:
                    model_totals[key] = {
                        "model": key,
                        "requests": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                    }
                model_totals[key]["requests"] += m["requests"]
                model_totals[key]["total_tokens"] += m["total_tokens"]
                model_totals[key]["cost_usd"] = round(
                    model_totals[key]["cost_usd"] + m["cost_usd"], 6
                )

        by_model = sorted(model_totals.values(), key=lambda x: x["cost_usd"], reverse=True)
        active_days = len(summaries)

        return {
            "days": days,
            "total_cost_usd": round(total_cost, 6),
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "avg_daily_cost_usd": round(total_cost / active_days, 6) if active_days else 0.0,
            "by_model": by_model,
        }

    # -----------------------------------------------------------------------
    # Export
    # -----------------------------------------------------------------------

    def export_csv(self, days: int = 7, *, by_model: bool = True) -> str:
        """Return a CSV string of daily cost data.

        Columns when by_model=True:
            date, model, requests, total_tokens, cost_usd

        Columns when by_model=False:
            date, requests, total_tokens, cost_usd
        """
        summaries = self.daily_summaries(days=days)
        buf = io.StringIO()

        if by_model:
            writer = csv.writer(buf)
            writer.writerow(["date", "model", "requests", "total_tokens", "cost_usd"])
            for s in summaries:
                for m in s.by_model:
                    writer.writerow(
                        [s.day, m["model"], m["requests"], m["total_tokens"], f"{m['cost_usd']:.6f}"]
                    )
        else:
            writer = csv.writer(buf)
            writer.writerow(["date", "requests", "total_tokens", "cost_usd"])
            for s in summaries:
                writer.writerow(
                    [s.day, s.total_requests, s.total_tokens, f"{s.total_cost_usd:.6f}"]
                )

        return buf.getvalue()

    # -----------------------------------------------------------------------
    # Burn-rate alarms
    # -----------------------------------------------------------------------

    def check_burn_rate(
        self,
        monthly_budget_usd: float,
        *,
        threshold_pct: float = _DEFAULT_DAILY_ALARM_PCT,
        days: int = 30,
    ) -> list[BurnRateAlarm]:
        """Return alarms for days where daily spend exceeds threshold_pct of monthly budget.

        Args:
            monthly_budget_usd: Total monthly budget in USD.
            threshold_pct: Percentage of monthly budget; default 20% triggers an alarm.
            days: How many past days to inspect.

        Returns:
            List of BurnRateAlarm objects (one per offending day). Empty if no alarms.
        """
        if monthly_budget_usd <= 0:
            return []

        limit = monthly_budget_usd * (threshold_pct / 100.0)
        alarms = []
        for s in self.daily_summaries(days=days):
            if s.total_cost_usd > limit:
                actual_pct = (s.total_cost_usd / monthly_budget_usd) * 100.0
                alarms.append(
                    BurnRateAlarm(
                        day=s.day,
                        daily_cost_usd=s.total_cost_usd,
                        monthly_budget_usd=monthly_budget_usd,
                        threshold_pct=threshold_pct,
                        actual_pct=actual_pct,
                    )
                )
        return alarms
