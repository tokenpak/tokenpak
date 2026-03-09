"""
TokenPak Insight & Decision Support Engine.

Generates automatic insights from telemetry rollup data so users don't
have to manually analyze their usage — the dashboard tells them what matters.

Usage:
    engine = InsightEngine(db_path="telemetry.db")
    insights = engine.generate_insights(days=7)
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

# ---------------------------------------------------------------------------
# Thresholds (configurable via subclass or constructor kwargs)
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS = {
    "savings_change_pct_warn": -10.0,  # warn if savings drop > 10%
    "savings_change_pct_good": 5.0,  # celebrate if savings rise > 5%
    "cost_spike_multiplier": 1.5,  # alert if daily cost > 1.5× avg
    "error_rate_warn": 0.05,  # warn if error rate > 5%
    "error_rate_alert": 0.10,  # alert if error rate > 10%
    "model_dominance_pct": 0.75,  # warn if one model > 75% of cost
    "compression_bypass_pct": 0.20,  # warn if > 20% requests bypass compression
    "min_requests": 5,  # minimum requests to generate insights
    "max_insights": 7,  # max insights to return
    "cache_ttl_seconds": 300,  # 5-minute insight cache
}

# Severity ordering for sorting
_SEVERITY_ORDER = {"alert": 0, "warning": 1, "success": 2, "info": 3}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Insight:
    """A single insight with optional action suggestion."""

    type: str  # info | success | warning | alert
    title: str  # Short headline
    description: str  # Explanation
    metric: str  # Related metric name
    delta: Optional[float] = None  # Change value (e.g. 0.12 = +12%)
    action: Optional[str] = None  # Suggested action

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "metric": self.metric,
            "delta": self.delta,
            "action": self.action,
        }

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_ORDER.get(self.type, 99)

    @property
    def delta_magnitude(self) -> float:
        return abs(self.delta) if self.delta is not None else 0.0


# ---------------------------------------------------------------------------
# Insight Engine
# ---------------------------------------------------------------------------
class InsightEngine:
    """
    Reads from telemetry rollup tables and generates actionable insights.

    Args:
        db_path: Path to telemetry SQLite database.
        thresholds: Override default thresholds dict.
    """

    def __init__(self, db_path: str = "telemetry.db", thresholds: Optional[dict] = None):
        self.db_path = db_path
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._cache: Optional[List[Insight]] = None
        self._cache_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate_insights(self, days: int = 7) -> List[Insight]:
        """
        Generate insights from the last `days` of data.
        Results are cached for CACHE_TTL_SECONDS.

        Returns:
            List of Insight objects sorted by severity then delta magnitude,
            capped at max_insights.
        """
        now = time.time()
        ttl = self.thresholds["cache_ttl_seconds"]
        if self._cache is not None and (now - self._cache_at) < ttl:
            return self._cache

        insights: List[Insight] = []
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            current = self._fetch_period(conn, days, offset_days=0)
            previous = self._fetch_period(conn, days, offset_days=days)

            # Only generate insights if there's enough data
            if current["total_requests"] < self.thresholds["min_requests"]:
                conn.close()
                return []

            insights.extend(self._savings_insights(current, previous))
            insights.extend(self._cost_insights(conn, current, previous, days))
            insights.extend(self._efficiency_insights(current, previous))
            insights.extend(self._error_insights(conn, current, days))
            insights.extend(self._decision_support(current, previous))

            conn.close()
        except sqlite3.Error:
            return []

        # Sort: alert → warning → success → info, then by delta magnitude desc
        insights.sort(key=lambda i: (i.severity_rank, -i.delta_magnitude))

        # Cap at max_insights
        max_i = self.thresholds["max_insights"]
        result = insights[:max_i]

        self._cache = result
        self._cache_at = now
        return result

    def invalidate_cache(self) -> None:
        """Force next call to regenerate insights."""
        self._cache = None
        self._cache_at = 0.0

    # ------------------------------------------------------------------
    # Internal: data fetching
    # ------------------------------------------------------------------
    def _fetch_period(self, conn: sqlite3.Connection, days: int, offset_days: int) -> dict:
        """Aggregate rollup data for a time window."""
        end_date = (datetime.now(timezone.utc) - timedelta(days=offset_days)).date()
        start_date = (datetime.now(timezone.utc) - timedelta(days=offset_days + days)).date()

        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(total_requests), 0)   AS total_requests,
                COALESCE(SUM(total_tokens), 0)     AS total_tokens,
                COALESCE(SUM(total_cost), 0)       AS total_cost,
                COALESCE(SUM(total_savings), 0)    AS total_savings,
                COALESCE(AVG(avg_raw_tokens), 0)   AS avg_raw_tokens,
                COALESCE(AVG(avg_final_tokens), 0) AS avg_final_tokens
            FROM tp_rollup_daily_model
            WHERE date > ? AND date <= ?
        """,
            (str(start_date), str(end_date)),
        ).fetchone()

        return (
            dict(row)
            if row
            else {
                "total_requests": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "total_savings": 0.0,
                "avg_raw_tokens": 0.0,
                "avg_final_tokens": 0.0,
            }
        )

    def _fetch_model_breakdown(self, conn: sqlite3.Connection, days: int) -> list:
        """Per-model cost breakdown for current period."""
        end_date = datetime.now(timezone.utc).date()
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        rows = conn.execute(
            """
            SELECT model, SUM(total_cost) AS cost, SUM(total_requests) AS requests
            FROM tp_rollup_daily_model
            WHERE date > ? AND date <= ?
            GROUP BY model ORDER BY cost DESC
        """,
            (str(start_date), str(end_date)),
        ).fetchall()
        return [dict(r) for r in rows]

    def _fetch_daily_costs(self, conn: sqlite3.Connection, days: int) -> list:
        """Daily total cost for spike detection."""
        end_date = datetime.now(timezone.utc).date()
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        rows = conn.execute(
            """
            SELECT date, SUM(total_cost) AS daily_cost
            FROM tp_rollup_daily_model
            WHERE date > ? AND date <= ?
            GROUP BY date ORDER BY date
        """,
            (str(start_date), str(end_date)),
        ).fetchall()
        return [dict(r) for r in rows]

    def _fetch_error_rate(self, conn: sqlite3.Connection, days: int) -> dict:
        """Compute error rate from tp_events."""
        end_ts = datetime.now(timezone.utc).isoformat()
        start_ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
                FROM tp_events
                WHERE ts > ? AND ts <= ?
            """,
                (start_ts, end_ts),
            ).fetchone()
            if row and row["total"] > 0:
                return {
                    "total": row["total"],
                    "errors": row["errors"],
                    "rate": row["errors"] / row["total"],
                }
        except sqlite3.OperationalError:
            pass
        return {"total": 0, "errors": 0, "rate": 0.0}

    # ------------------------------------------------------------------
    # Internal: insight generators
    # ------------------------------------------------------------------
    def _savings_insights(self, current: dict, previous: dict) -> List[Insight]:
        insights = []
        cur_savings = current["total_savings"]
        prev_savings = previous["total_savings"]

        # Month total (approximate from current window)
        if cur_savings > 0:
            insights.append(
                Insight(
                    type="info",
                    title=f"${cur_savings:.2f} saved this period",
                    description=(
                        f"Token compression saved ${cur_savings:.2f} in the last reporting period "
                        f"across {int(current['total_requests'])} requests."
                    ),
                    metric="total_savings",
                    delta=None,
                )
            )

        # Week-over-week change
        if prev_savings > 0:
            delta = (cur_savings - prev_savings) / prev_savings
            if delta >= self.thresholds["savings_change_pct_good"] / 100:
                pct = delta * 100
                insights.append(
                    Insight(
                        type="success",
                        title=f"Savings up {pct:.0f}%",
                        description=(
                            f"Token compression saved ${cur_savings - prev_savings:.2f} more "
                            f"this period compared to the previous period."
                        ),
                        metric="weekly_savings",
                        delta=delta,
                    )
                )
            elif delta <= self.thresholds["savings_change_pct_warn"] / 100:
                pct = abs(delta) * 100
                insights.append(
                    Insight(
                        type="warning",
                        title=f"Savings dropped {pct:.0f}%",
                        description=(
                            f"Compression efficiency decreased — savings fell "
                            f"${prev_savings - cur_savings:.2f} vs the previous period."
                        ),
                        metric="weekly_savings",
                        delta=delta,
                        action="Check if recent requests contained more protected or uncompressible content.",
                    )
                )

        return insights

    def _cost_insights(
        self, conn: sqlite3.Connection, current: dict, previous: dict, days: int
    ) -> List[Insight]:
        insights: List[Insight] = []
        models = self._fetch_model_breakdown(conn, days)
        daily_costs = self._fetch_daily_costs(conn, days)

        if not models:
            return insights

        total_cost = current["total_cost"]

        # Most expensive model
        top_model = models[0]
        insights.append(
            Insight(
                type="info",
                title=f"{top_model['model']} is your most-used model",
                description=(
                    f"{top_model['model']} accounts for ${top_model['cost']:.2f} "
                    f"({top_model['requests']} requests) this period."
                ),
                metric="model_cost_top",
                delta=None,
            )
        )

        # Model dominance warning
        if (
            total_cost > 0
            and top_model["cost"] / total_cost > self.thresholds["model_dominance_pct"]
        ):
            share_pct = top_model["cost"] / total_cost * 100
            insights.append(
                Insight(
                    type="warning",
                    title=f"{top_model['model']} dominates cost",
                    description=(
                        f"{top_model['model']} accounts for {share_pct:.0f}% of total spend "
                        f"(${top_model['cost']:.2f} of ${total_cost:.2f})."
                    ),
                    metric="model_cost_share",
                    delta=top_model["cost"] / total_cost,
                    action=(
                        f"Consider routing simpler queries to a cheaper model "
                        f"to reduce costs by up to ${top_model['cost'] * 0.3:.2f}/period."
                    ),
                )
            )

        # Cost spike detection
        if len(daily_costs) >= 3:
            avg_cost = sum(d["daily_cost"] for d in daily_costs[:-1]) / (len(daily_costs) - 1)
            last_day = daily_costs[-1]
            if (
                avg_cost > 0
                and last_day["daily_cost"] > avg_cost * self.thresholds["cost_spike_multiplier"]
            ):
                multiplier = last_day["daily_cost"] / avg_cost
                insights.append(
                    Insight(
                        type="alert",
                        title=f"Cost spike on {last_day['date']}",
                        description=(
                            f"Daily cost was {multiplier:.1f}× higher than average "
                            f"(${last_day['daily_cost']:.2f} vs avg ${avg_cost:.2f})."
                        ),
                        metric="cost_spike",
                        delta=multiplier - 1,
                        action="Review requests from this date for unusually large payloads.",
                    )
                )

        return insights

    def _efficiency_insights(self, current: dict, previous: dict) -> List[Insight]:
        insights = []
        avg_raw = current["avg_raw_tokens"]
        avg_final = current["avg_final_tokens"]

        if avg_raw > 0 and avg_final > 0:
            ratio = avg_raw / avg_final
            insights.append(
                Insight(
                    type="info",
                    title=f"{ratio:.1f}:1 average compression ratio",
                    description=(
                        f"On average, requests are compressed from {avg_raw:.0f} to "
                        f"{avg_final:.0f} tokens ({ratio:.1f}× reduction)."
                    ),
                    metric="compression_ratio",
                    delta=None,
                )
            )

        # Compression bypass check
        total_req = current["total_requests"]
        if avg_raw > 0 and avg_final >= avg_raw * 0.98 and total_req > 0:
            # final tokens ≈ raw tokens → compression mostly bypassed
            insights.append(
                Insight(
                    type="warning",
                    title="Compression appears inactive",
                    description=(
                        "Average token reduction is near zero — compression may be bypassed "
                        "for most requests."
                    ),
                    metric="compression_bypass",
                    delta=None,
                    action="Check TOKENPAK_COMPACT env var and compilation mode setting.",
                )
            )

        # Efficiency regression vs previous period
        if previous["avg_raw_tokens"] > 0 and previous["avg_final_tokens"] > 0:
            prev_ratio = previous["avg_raw_tokens"] / previous["avg_final_tokens"]
            cur_ratio = avg_raw / avg_final if avg_final > 0 else 0
            if prev_ratio > 0 and cur_ratio < prev_ratio * 0.85:
                drop_pct = (prev_ratio - cur_ratio) / prev_ratio * 100
                insights.append(
                    Insight(
                        type="warning",
                        title=f"Compression efficiency dropped {drop_pct:.0f}%",
                        description=(
                            f"Compression ratio fell from {prev_ratio:.1f}:1 to {cur_ratio:.1f}:1 "
                            f"compared to the previous period."
                        ),
                        metric="compression_efficiency",
                        delta=-(drop_pct / 100),
                        action="Review recent content types — more code or protected content reduces compressibility.",
                    )
                )

        return insights

    def _error_insights(self, conn: sqlite3.Connection, current: dict, days: int) -> List[Insight]:
        insights = []
        err_data = self._fetch_error_rate(conn, days)
        rate = err_data["rate"]

        if rate >= self.thresholds["error_rate_alert"]:
            insights.append(
                Insight(
                    type="alert",
                    title=f"High error rate: {rate * 100:.1f}%",
                    description=(
                        f"{err_data['errors']} of {err_data['total']} requests failed "
                        f"({rate * 100:.1f}% error rate) — above critical threshold."
                    ),
                    metric="error_rate",
                    delta=rate,
                    action="Review error logs and check provider API status.",
                )
            )
        elif rate >= self.thresholds["error_rate_warn"]:
            insights.append(
                Insight(
                    type="warning",
                    title=f"Elevated error rate: {rate * 100:.1f}%",
                    description=(
                        f"{err_data['errors']} of {err_data['total']} requests failed — "
                        f"above normal threshold."
                    ),
                    metric="error_rate",
                    delta=rate,
                    action="Monitor for continued errors; may indicate provider instability.",
                )
            )

        return insights

    def _decision_support(self, current: dict, previous: dict) -> List[Insight]:
        """Actionable suggestions when specific conditions are detected."""
        insights = []
        total_cost = current["total_cost"]
        total_savings = current["total_savings"]

        # Low savings relative to cost
        if total_cost > 0 and total_savings / total_cost < 0.1 and total_savings >= 0:
            weekly_potential = total_cost * 0.25
            insights.append(
                Insight(
                    type="warning",
                    title="Savings opportunity detected",
                    description=(
                        f"Current savings are only {total_savings / total_cost * 100:.1f}% of total cost. "
                        f"Higher compression mode could yield more savings."
                    ),
                    metric="savings_opportunity",
                    delta=None,
                    action=(
                        f"Consider switching to 'hybrid' or 'aggressive' compression mode "
                        f"to save an estimated ${weekly_potential:.2f} per period."
                    ),
                )
            )

        return insights


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------
def generate_insights(db_path: str = "telemetry.db", days: int = 7) -> List[Insight]:
    """
    Generate insights from telemetry data.

    Args:
        db_path: Path to telemetry SQLite database.
        days: Number of days to analyze (default: 7).

    Returns:
        Sorted list of Insight objects, capped at max_insights.
    """
    engine = InsightEngine(db_path=db_path)
    return engine.generate_insights(days=days)
