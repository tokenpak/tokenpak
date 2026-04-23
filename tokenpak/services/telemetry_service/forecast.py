"""Cost forecasting from request history.

Reads ``monitor.db`` + projects spend over the next N days using a
simple linear extrapolation of the last M days of daily totals. Good
enough to answer "if today's trend holds, what's monthly spend look
like?" without pretending to be a statistical forecaster.

Returned data structure is JSON-serialisable; exposed via
``GET /v1/forecast`` on the proxy.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CostForecast:
    days_of_history: int
    history_total_usd: float
    avg_daily_usd: float
    projected_next_7_days_usd: float
    projected_next_30_days_usd: float
    trend: str  # "rising" | "flat" | "falling" | "insufficient_data"

    def as_dict(self) -> dict:
        return {
            "days_of_history": self.days_of_history,
            "history_total_usd": round(self.history_total_usd, 4),
            "avg_daily_usd": round(self.avg_daily_usd, 4),
            "projected_next_7_days_usd": round(self.projected_next_7_days_usd, 4),
            "projected_next_30_days_usd": round(self.projected_next_30_days_usd, 4),
            "trend": self.trend,
        }


def _db_path() -> Path:
    return Path(
        os.environ.get(
            "TOKENPAK_DB", os.path.expanduser("~/.tokenpak/monitor.db")
        )
    )


def forecast(days_of_history: int = 14) -> CostForecast:
    """Compute a forecast over the last ``days_of_history`` days."""
    path = _db_path()
    if not path.exists():
        return CostForecast(
            days_of_history=0,
            history_total_usd=0.0,
            avg_daily_usd=0.0,
            projected_next_7_days_usd=0.0,
            projected_next_30_days_usd=0.0,
            trend="insufficient_data",
        )

    since = (datetime.utcnow() - timedelta(days=days_of_history)).isoformat()
    try:
        conn = sqlite3.connect(str(path))
        rows = list(
            conn.execute(
                """
                SELECT DATE(timestamp) AS day, SUM(estimated_cost) AS cost
                FROM requests
                WHERE timestamp >= ?
                GROUP BY day
                ORDER BY day
                """,
                (since,),
            )
        )
        conn.close()
    except sqlite3.OperationalError as exc:
        logger.debug("forecast: query failed (%s)", exc)
        return CostForecast(
            days_of_history=0,
            history_total_usd=0.0,
            avg_daily_usd=0.0,
            projected_next_7_days_usd=0.0,
            projected_next_30_days_usd=0.0,
            trend="insufficient_data",
        )

    if not rows:
        return CostForecast(
            days_of_history=0,
            history_total_usd=0.0,
            avg_daily_usd=0.0,
            projected_next_7_days_usd=0.0,
            projected_next_30_days_usd=0.0,
            trend="insufficient_data",
        )

    daily_totals: list[float] = [float(r[1] or 0.0) for r in rows]
    total = sum(daily_totals)
    avg = total / len(daily_totals)

    # Trend heuristic: compare the last third vs the first third.
    n = len(daily_totals)
    if n < 6:
        trend = "insufficient_data"
    else:
        third = n // 3
        early = sum(daily_totals[:third]) / max(third, 1)
        late = sum(daily_totals[-third:]) / max(third, 1)
        delta = (late - early) / max(early, 1e-9)
        if delta > 0.15:
            trend = "rising"
        elif delta < -0.15:
            trend = "falling"
        else:
            trend = "flat"

    return CostForecast(
        days_of_history=len(daily_totals),
        history_total_usd=total,
        avg_daily_usd=avg,
        projected_next_7_days_usd=avg * 7,
        projected_next_30_days_usd=avg * 30,
        trend=trend,
    )


__all__ = ["forecast", "CostForecast"]
