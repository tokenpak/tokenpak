"""Rollup queries and utilities for TokenPak telemetry.

This module provides query interfaces over the rollup tables created by
storage.py. The tables are:

- `tp_rollup_daily_model` — aggregated by date + model
- `tp_rollup_daily_provider` — aggregated by date + provider
- `tp_rollup_daily_agent` — aggregated by date + agent_id

Rollups are refreshed via TelemetryDB.refresh_rollups().

Usage::

    from tokenpak.telemetry.rollups import RollupEngine
    from tokenpak.telemetry.storage import TelemetryDB

    db = TelemetryDB("telemetry.db")
    engine = RollupEngine(db)

    # Refresh rollups (calls db.refresh_rollups())
    engine.refresh_all()

    # Query rollups
    summary = engine.get_summary(days=30)
    timeseries = engine.get_timeseries(metric="cost", interval="day", days=7)
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Optional

from tokenpak.telemetry.storage import TelemetryDB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    """Return current Unix timestamp."""
    return time.time()


def _ts_to_date(ts: float) -> str:
    """Convert Unix timestamp to YYYY-MM-DD date string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _ts_to_hour(ts: float) -> str:
    """Convert Unix timestamp to ISO hour string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:00:00")


def _row_to_dict(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    """Convert sqlite3.Row to plain dict."""
    return dict(zip([col[0] for col in cursor.description], row))


# ---------------------------------------------------------------------------
# State table (for tracking refresh times)
# ---------------------------------------------------------------------------

_STATE_DDL = """
CREATE TABLE IF NOT EXISTS tp_rollup_state (
    key             TEXT NOT NULL PRIMARY KEY,
    value           TEXT NOT NULL DEFAULT '',
    updated_at      REAL NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Rollup Engine
# ---------------------------------------------------------------------------


class RollupEngine:
    """Manages rollup queries and refresh operations.

    The rollup tables are created by TelemetryDB when it initializes.
    This class provides query interfaces and delegates refresh to the DB.

    Parameters
    ----------
    db:
        TelemetryDB instance to query rollups from.
    """

    def __init__(self, db: TelemetryDB) -> None:
        self._db = db
        self._conn = db._conn

    def ensure_tables(self) -> None:
        """Create state table if it doesn't exist.

        Note: Rollup tables are created by TelemetryDB._apply_ddl().
        This only creates the state tracking table.
        """
        self._conn.executescript(_STATE_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Rollup computation (delegates to storage)
    # ------------------------------------------------------------------

    def refresh_all(self, days: int = 7) -> dict[str, int]:
        """Refresh all rollup tables.

        Delegates to TelemetryDB.compute_rollups() which does a full
        rebuild of all rollup tables.

        Returns dict with counts of rows written per table.
        """
        counts = self._db.compute_rollups()
        self._set_state("last_refresh", str(_now()))
        return counts

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_daily_model_rollups(
        self, days: int = 30, model: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Return daily model rollups for the last N days."""
        cutoff = _ts_to_date(_now() - days * 86400)
        cur = self._conn.cursor()

        if model:
            cur.execute(
                "SELECT * FROM tp_rollup_daily_model WHERE date >= ? AND model = ? ORDER BY date DESC",
                (cutoff, model),
            )
        else:
            cur.execute(
                "SELECT * FROM tp_rollup_daily_model WHERE date >= ? ORDER BY date DESC",
                (cutoff,),
            )
        return [_row_to_dict(cur, r) for r in cur.fetchall()]

    def get_daily_provider_rollups(
        self, days: int = 30, provider: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Return daily provider rollups for the last N days."""
        cutoff = _ts_to_date(_now() - days * 86400)
        cur = self._conn.cursor()

        if provider:
            cur.execute(
                "SELECT * FROM tp_rollup_daily_provider WHERE date >= ? AND provider = ? ORDER BY date DESC",
                (cutoff, provider),
            )
        else:
            cur.execute(
                "SELECT * FROM tp_rollup_daily_provider WHERE date >= ? ORDER BY date DESC",
                (cutoff,),
            )
        return [_row_to_dict(cur, r) for r in cur.fetchall()]

    def get_daily_agent_rollups(
        self, days: int = 30, agent_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Return daily agent rollups for the last N days."""
        cutoff = _ts_to_date(_now() - days * 86400)
        cur = self._conn.cursor()

        if agent_id:
            cur.execute(
                "SELECT * FROM tp_rollup_daily_agent WHERE date >= ? AND agent_id = ? ORDER BY date DESC",
                (cutoff, agent_id),
            )
        else:
            cur.execute(
                "SELECT * FROM tp_rollup_daily_agent WHERE date >= ? ORDER BY date DESC",
                (cutoff,),
            )
        return [_row_to_dict(cur, r) for r in cur.fetchall()]

    def get_timeseries(
        self,
        metric: str = "cost",
        interval: str = "day",
        days: int = 30,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return timeseries data for charting.

        Parameters
        ----------
        metric:
            One of: cost, tokens, savings, requests
        interval:
            One of: hour, day
        days:
            Number of days to look back
        provider, model, agent_id:
            Optional filters
        """
        if interval == "hour":
            # Query raw events for hourly granularity
            return self._get_hourly_timeseries(metric, days, provider, model, agent_id)
        else:
            # Use rollups for daily
            return self._get_daily_timeseries(metric, days, provider, model, agent_id)

    def _get_hourly_timeseries(
        self,
        metric: str,
        days: int,
        provider: Optional[str],
        model: Optional[str],
        agent_id: Optional[str],
    ) -> list[dict[str, Any]]:
        """Generate hourly timeseries from raw events."""
        cutoff_ts = _now() - days * 86400
        conditions = ["e.ts >= ?"]
        params: list[Any] = [cutoff_ts]

        if provider:
            conditions.append("e.provider = ?")
            params.append(provider)
        if model:
            conditions.append("e.model = ?")
            params.append(model)
        if agent_id:
            conditions.append("e.agent_id = ?")
            params.append(agent_id)

        where = " AND ".join(conditions)

        # Choose aggregation based on metric
        if metric == "cost":
            agg = "COALESCE(SUM(c.cost_total), 0)"
        elif metric == "savings":
            agg = "COALESCE(SUM(c.savings_total), 0)"
        elif metric == "tokens":
            agg = "COALESCE(SUM(u.input_billed + u.output_billed), 0)"
        else:  # requests
            agg = "COUNT(DISTINCT e.trace_id)"

        sql = f"""
        SELECT
            strftime('%Y-%m-%dT%H:00:00', datetime(e.ts, 'unixepoch')) as bucket,
            {agg} as value
        FROM tp_events e
        LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
        LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
        WHERE {where}
        GROUP BY bucket
        ORDER BY bucket
        """

        cur = self._conn.cursor()
        cur.execute(sql, params)
        return [{"bucket": r[0], "value": r[1]} for r in cur.fetchall()]

    def _get_daily_timeseries(
        self,
        metric: str,
        days: int,
        provider: Optional[str],
        model: Optional[str],
        agent_id: Optional[str],
    ) -> list[dict[str, Any]]:
        """Generate daily timeseries from rollups."""
        cutoff = _ts_to_date(_now() - days * 86400)

        # Map metric to column name (using existing schema)
        col_map = {
            "cost": "total_cost",
            "tokens": "total_tokens",
            "savings": "total_savings",
            "requests": "total_requests",
        }
        col = col_map.get(metric, "total_cost")

        # Choose which rollup table based on filters
        if model:
            table = "tp_rollup_daily_model"
            conditions = ["date >= ?", "model = ?"]
            params: list[Any] = [cutoff, model]
        elif provider:
            table = "tp_rollup_daily_provider"
            conditions = ["date >= ?", "provider = ?"]
            params = [cutoff, provider]
        elif agent_id:
            table = "tp_rollup_daily_agent"
            conditions = ["date >= ?", "agent_id = ?"]
            params = [cutoff, agent_id]
        else:
            # Aggregate across all providers
            table = "tp_rollup_daily_provider"
            conditions = ["date >= ?"]
            params = [cutoff]

        where = " AND ".join(conditions)

        sql = f"""
        SELECT date as bucket, SUM({col}) as value
        FROM {table}
        WHERE {where}
        GROUP BY date
        ORDER BY date
        """

        cur = self._conn.cursor()
        cur.execute(sql, params)
        return [{"bucket": r[0], "value": r[1]} for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_summary(
        self,
        days: int = 30,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return aggregated summary statistics."""
        cutoff = _ts_to_date(_now() - days * 86400)
        cur = self._conn.cursor()

        # Total across all dimensions
        cur.execute(
            """
            SELECT
                SUM(total_requests) as total_requests,
                SUM(total_tokens) as total_tokens,
                SUM(total_cost) as total_cost,
                SUM(total_savings) as total_savings,
                CASE WHEN SUM(total_requests) > 0
                    THEN SUM(avg_raw_tokens * total_requests) / SUM(total_requests)
                    ELSE 0 END as avg_raw_tokens,
                CASE WHEN SUM(total_requests) > 0
                    THEN SUM(avg_final_tokens * total_requests) / SUM(total_requests)
                    ELSE 0 END as avg_final_tokens,
                CASE WHEN SUM(total_requests) > 0
                    THEN SUM(avg_cost * total_requests) / SUM(total_requests)
                    ELSE 0 END as avg_cost
            FROM tp_rollup_daily_provider
            WHERE date >= ?
            """,
            (cutoff,),
        )
        row = cur.fetchone()
        totals = {
            "total_requests": row[0] or 0,
            "total_tokens": row[1] or 0,
            "total_actual_cost": row[2] or 0,
            "total_savings": row[3] or 0,
            "avg_raw_tokens": row[4] or 0,
            "avg_final_tokens": row[5] or 0,
            "avg_cost": row[6] or 0,
        }

        # By provider
        cur.execute(
            """
            SELECT provider, SUM(total_requests) as requests,
                   SUM(total_cost) as cost, SUM(total_savings) as savings,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_raw_tokens * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_raw_tokens,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_final_tokens * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_final_tokens,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_cost * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_cost
            FROM tp_rollup_daily_provider
            WHERE date >= ?
            GROUP BY provider
            ORDER BY cost DESC
            """,
            (cutoff,),
        )
        by_provider = [_row_to_dict(cur, r) for r in cur.fetchall()]

        # By model
        cur.execute(
            """
            SELECT model, SUM(total_requests) as requests,
                   SUM(total_cost) as cost, SUM(total_savings) as savings,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_raw_tokens * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_raw_tokens,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_final_tokens * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_final_tokens,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_cost * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_cost
            FROM tp_rollup_daily_model
            WHERE date >= ?
            GROUP BY model
            ORDER BY cost DESC
            LIMIT 20
            """,
            (cutoff,),
        )
        by_model = [_row_to_dict(cur, r) for r in cur.fetchall()]

        # By agent
        cur.execute(
            """
            SELECT agent_id, SUM(total_requests) as requests,
                   SUM(total_cost) as cost, SUM(total_savings) as savings,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_raw_tokens * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_raw_tokens,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_final_tokens * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_final_tokens,
                   CASE WHEN SUM(total_requests) > 0
                       THEN SUM(avg_cost * total_requests) / SUM(total_requests)
                       ELSE 0 END as avg_cost
            FROM tp_rollup_daily_agent
            WHERE date >= ? AND agent_id != ''
            GROUP BY agent_id
            ORDER BY cost DESC
            LIMIT 20
            """,
            (cutoff,),
        )
        by_agent = [_row_to_dict(cur, r) for r in cur.fetchall()]

        return {
            "period_days": days,
            "totals": totals,
            "by_provider": by_provider,
            "by_model": by_model,
            "by_agent": by_agent,
        }

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _set_state(self, key: str, value: str) -> None:
        """Set a state value."""
        self._conn.execute(
            "INSERT OR REPLACE INTO tp_rollup_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, _now()),
        )
        self._conn.commit()

    def _get_state(self, key: str) -> Optional[str]:
        """Get a state value."""
        cur = self._conn.cursor()
        cur.execute("SELECT value FROM tp_rollup_state WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def get_last_refresh(self) -> Optional[float]:
        """Return timestamp of last rollup refresh."""
        val = self._get_state("last_refresh")
        return float(val) if val else None

    def get_cost_components(self, days: int = 30) -> dict[str, float]:
        """Return cost breakdown by component."""
        cutoff = _now() - days * 86400
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(SUM(c.cost_input),0), COALESCE(SUM(c.cost_output),0),
                   COALESCE(SUM(c.cost_cache_read),0), COALESCE(SUM(c.cost_cache_write),0),
                   COALESCE(SUM(c.cost_total),0)
            FROM tp_costs c JOIN tp_events e ON c.trace_id=e.trace_id
            WHERE e.ts >= ? AND e.event_type='request'
        """,
            (cutoff,),
        )
        r = cur.fetchone()
        return {
            "cost_input": r[0],
            "cost_output": r[1],
            "cost_cache_read": r[2],
            "cost_cache_write": r[3],
            "cost_total": r[4],
        }

    def get_cache_stats(self, days: int = 30) -> dict[str, float]:
        """Return cache efficiency stats."""
        cutoff = _now() - days * 86400
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(SUM(u.cache_read),0), COALESCE(SUM(u.input_billed),0)
            FROM tp_usage u JOIN tp_events e ON u.trace_id=e.trace_id
            WHERE e.ts >= ? AND e.event_type='request'
        """,
            (cutoff,),
        )
        r = cur.fetchone()
        cr, inp = r[0] or 0, r[1] or 0
        total = cr + inp
        return {
            "cache_read_tokens": cr,
            "input_tokens": inp,
            "cache_hit_rate": (cr / total * 100) if total else 0,
        }

    # ------------------------------------------------------------------
    # Date-targeted rollup computation (Phase 7H)
    # ------------------------------------------------------------------

    def compute_daily_rollups(self, date) -> int:
        """Compute rollups for a specific calendar date. Idempotent."""
        date_str = date.isoformat() if hasattr(date, "isoformat") else str(date)
        cur = self._conn.cursor()
        total = 0
        for table, group_field in [
            ("tp_rollup_daily_model", "model"),
            ("tp_rollup_daily_provider", "provider"),
            ("tp_rollup_daily_agent", "agent_id"),
        ]:
            cur.execute(f"DELETE FROM {table} WHERE date = ?", (date_str,))
            cur.execute(
                f"""
                INSERT INTO {table} (date, {group_field}, total_requests, total_tokens,
                    total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
                SELECT
                    strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) as date,
                    e.{group_field},
                    COUNT(DISTINCT e.trace_id),
                    COALESCE(SUM(u.input_billed + u.output_billed), 0),
                    COALESCE(SUM(c.cost_total), 0),
                    COALESCE(SUM(c.savings_total), 0),
                    COALESCE(AVG(s.tokens_raw), 0),
                    COALESCE(AVG(s.tokens_after_tp), 0),
                    COALESCE(AVG(c.cost_total), 0)
                FROM tp_events e
                LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
                LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
                LEFT JOIN tp_segments s ON e.trace_id = s.trace_id
                WHERE strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) = ?
                GROUP BY date, e.{group_field}
            """,
                (date_str,),
            )
            total += cur.rowcount
        self._conn.commit()
        self._set_state("last_refresh", str(_now()))
        return total

    def compute_hourly_rollups(self, date) -> int:
        """Compute hourly rollups for a specific date. Idempotent."""
        date_str = date.isoformat() if hasattr(date, "isoformat") else str(date)
        cur = self._conn.cursor()
        cur.execute("DELETE FROM tp_rollup_daily_model WHERE date LIKE ?", (f"{date_str}T%",))
        cur.execute(
            """
            INSERT INTO tp_rollup_daily_model
                (date, model, total_requests, total_tokens,
                 total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
            SELECT
                strftime('%Y-%m-%dT%H:00:00', datetime(e.ts, 'unixepoch')) as date,
                e.model,
                COUNT(DISTINCT e.trace_id),
                COALESCE(SUM(u.input_billed + u.output_billed), 0),
                COALESCE(SUM(c.cost_total), 0),
                COALESCE(SUM(c.savings_total), 0),
                COALESCE(AVG(s.tokens_raw), 0),
                COALESCE(AVG(s.tokens_after_tp), 0),
                COALESCE(AVG(c.cost_total), 0)
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            LEFT JOIN tp_segments s ON e.trace_id = s.trace_id
            WHERE strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) = ?
            GROUP BY date, e.model
        """,
            (date_str,),
        )
        total = cur.rowcount
        self._conn.commit()
        return total

    def rebuild_all_rollups(self, from_date, to_date) -> dict:
        """Rebuild daily rollups for a date range. Returns {dates_processed, total_rows}."""
        from datetime import timedelta

        current = from_date
        total_rows = 0
        dates_processed = 0
        while current <= to_date:
            total_rows += self.compute_daily_rollups(current)
            current = current + timedelta(days=1)
            dates_processed += 1
        return {"dates_processed": dates_processed, "total_rows": total_rows}

    def check_consistency(self, days: int = 7) -> dict:
        """Verify rollup totals match raw event aggregates."""
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        cutoff_date = (_dt.now(tz=_tz.utc) - _td(days=days)).strftime("%Y-%m-%d")
        cur = self._conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT e.trace_id), COALESCE(SUM(c.cost_total),0) "
            "FROM tp_events e LEFT JOIN tp_costs c ON e.trace_id = c.trace_id "
            "WHERE strftime('%Y-%m-%d', datetime(e.ts,'unixepoch')) >= ?",
            (cutoff_date,),
        )
        raw_req, raw_cost = cur.fetchone()
        cur.execute(
            "SELECT COALESCE(SUM(total_requests),0), COALESCE(SUM(total_cost),0) "
            "FROM tp_rollup_daily_provider WHERE date >= ?",
            (cutoff_date,),
        )
        rollup_req, rollup_cost = cur.fetchone()
        delta_cost = abs((raw_cost or 0) - (rollup_cost or 0))
        delta_req = abs((raw_req or 0) - (rollup_req or 0))
        tol = max((raw_cost or 0) * 0.01, 0.0001)
        discrepancies = []
        if delta_cost > tol:
            discrepancies.append(
                f"Cost mismatch: raw={raw_cost:.6f}, rollup={rollup_cost:.6f}, delta={delta_cost:.6f}"
            )
        if delta_req != 0:
            discrepancies.append(f"Request count mismatch: raw={raw_req}, rollup={rollup_req}")
        return {
            "ok": not discrepancies,
            "raw_total_cost": raw_cost,
            "rollup_total_cost": rollup_cost,
            "raw_total_requests": raw_req,
            "rollup_total_requests": rollup_req,
            "delta_cost": delta_cost,
            "delta_requests": delta_req,
            "discrepancies": discrepancies,
            "days_checked": days,
            "cutoff_date": cutoff_date,
        }
