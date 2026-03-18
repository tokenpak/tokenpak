"""Rollup computation, timeseries, and summary query mixin."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from tokenpak.telemetry.storage_base import _row_to_dict


class RollupsMixin:
    """Mixin providing rollup computation, summary, and timeseries query methods."""

    _conn: sqlite3.Connection

    def get_summary(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return aggregate summary statistics.

        Parameters
        ----------
        provider, model, agent_id:
            Optional filters to narrow the summary.

        Returns
        -------
        dict
            Keys: total_requests, total_tokens, total_cost, total_savings,
            by_provider, by_model, by_agent.
        """
        cur = self._conn.cursor()
        conditions: list[str] = []
        params: list[Any] = []

        if provider:
            conditions.append("e.provider = ?")
            params.append(provider)
        if model:
            conditions.append("e.model = ?")
            params.append(model)
        if agent_id:
            conditions.append("e.agent_id = ?")
            params.append(agent_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # Total aggregates
        sql = f"""
            SELECT
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
        """
        cur.execute(sql, params)
        row = cur.fetchone()
        totals = _row_to_dict(cur, row) if row else {}

        # By provider
        sql_provider = f"""
            SELECT e.provider,
                   COUNT(DISTINCT e.trace_id) as requests,
                   COALESCE(SUM(c.cost_total), 0) as cost,
                   COALESCE(SUM(c.savings_total), 0) as savings
            FROM tp_events e
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY e.provider
        """
        cur.execute(sql_provider, params)
        by_provider = [_row_to_dict(cur, r) for r in cur.fetchall()]

        # By model
        sql_model = f"""
            SELECT e.model,
                   COUNT(DISTINCT e.trace_id) as requests,
                   COALESCE(SUM(c.cost_total), 0) as cost,
                   COALESCE(SUM(c.savings_total), 0) as savings
            FROM tp_events e
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY e.model
        """
        cur.execute(sql_model, params)
        by_model = [_row_to_dict(cur, r) for r in cur.fetchall()]

        # By agent
        sql_agent = f"""
            SELECT e.agent_id,
                   COUNT(DISTINCT e.trace_id) as requests,
                   COALESCE(SUM(c.cost_total), 0) as cost,
                   COALESCE(SUM(c.savings_total), 0) as savings
            FROM tp_events e
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY e.agent_id
        """
        cur.execute(sql_agent, params)
        by_agent = [_row_to_dict(cur, r) for r in cur.fetchall()]

        return {
            **totals,
            "by_provider": by_provider,
            "by_model": by_model,
            "by_agent": by_agent,
        }

    def get_timeseries(
        self,
        metric: str = "cost",
        interval: str = "hour",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        since_ts: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Return time-bucketed metric data for charting.

        Parameters
        ----------
        metric:
            One of ``"cost"``, ``"tokens"``, ``"savings"``, ``"requests"``.
        interval:
            Time bucket size: ``"hour"`` or ``"day"``.
        provider, model, agent_id:
            Optional filters.
        since_ts:
            Only include data from this timestamp onwards.

        Returns
        -------
        list[dict]
            Each dict has ``bucket`` (ISO timestamp) and ``value``.
        """
        cur = self._conn.cursor()
        conditions: list[str] = []
        params: list[Any] = []

        if provider:
            conditions.append("e.provider = ?")
            params.append(provider)
        if model:
            conditions.append("e.model = ?")
            params.append(model)
        if agent_id:
            conditions.append("e.agent_id = ?")
            params.append(agent_id)
        if since_ts:
            conditions.append("e.ts >= ?")
            params.append(since_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # Time bucket format
        if interval == "day":
            bucket_expr = "strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch'))"
        else:  # hour
            bucket_expr = "strftime('%Y-%m-%dT%H:00:00', datetime(e.ts, 'unixepoch'))"

        # Metric expression
        metric_map = {
            "cost": "COALESCE(SUM(c.cost_total), 0)",
            "tokens": "COALESCE(SUM(u.input_billed + u.output_billed), 0)",
            "savings": "COALESCE(SUM(c.savings_total), 0)",
            "requests": "COUNT(DISTINCT e.trace_id)",
        }
        metric_expr = metric_map.get(metric, metric_map["cost"])

        sql = f"""
            SELECT {bucket_expr} as bucket, {metric_expr} as value
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY bucket
            ORDER BY bucket ASC
        """
        cur.execute(sql, params)
        return [_row_to_dict(cur, r) for r in cur.fetchall()]

    def compute_rollups(self) -> dict[str, int]:
        """Recompute all daily rollup tables from raw data.

        This is idempotent — can be called repeatedly. Replaces existing
        rollup data with fresh aggregates.

        Returns
        -------
        dict
            Counts of rows written to each rollup table.
        """
        cur = self._conn.cursor()
        counts = {}

        # Rollup by model
        cur.execute("DELETE FROM tp_rollup_daily_model")
        cur.execute("""
            INSERT INTO tp_rollup_daily_model (date, model, total_requests, total_tokens, total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
            WITH seg_agg AS (
                SELECT trace_id,
                    AVG(NULLIF(tokens_raw, 0)) as avg_raw,
                    AVG(NULLIF(tokens_after_tp, 0)) as avg_tp
                FROM tp_segments GROUP BY trace_id
            )
            SELECT
                strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) as date,
                e.model,
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings,
                COALESCE(AVG(NULLIF(sa.avg_raw, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_raw_tokens,
                COALESCE(AVG(NULLIF(sa.avg_tp, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_final_tokens,
                COALESCE(AVG(c.cost_total), 0) as avg_cost
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            LEFT JOIN seg_agg sa ON e.trace_id = sa.trace_id
            GROUP BY date, e.model
        """)
        counts["model"] = cur.rowcount

        # Rollup by provider
        cur.execute("DELETE FROM tp_rollup_daily_provider")
        cur.execute("""
            INSERT INTO tp_rollup_daily_provider (date, provider, total_requests, total_tokens, total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
            WITH seg_agg AS (
                SELECT trace_id,
                    AVG(NULLIF(tokens_raw, 0)) as avg_raw,
                    AVG(NULLIF(tokens_after_tp, 0)) as avg_tp
                FROM tp_segments GROUP BY trace_id
            )
            SELECT
                strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) as date,
                e.provider,
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings,
                COALESCE(AVG(NULLIF(sa.avg_raw, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_raw_tokens,
                COALESCE(AVG(NULLIF(sa.avg_tp, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_final_tokens,
                COALESCE(AVG(c.cost_total), 0) as avg_cost
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            LEFT JOIN seg_agg sa ON e.trace_id = sa.trace_id
            GROUP BY date, e.provider
        """)
        counts["provider"] = cur.rowcount

        # Rollup by agent
        cur.execute("DELETE FROM tp_rollup_daily_agent")
        cur.execute("""
            INSERT INTO tp_rollup_daily_agent (date, agent_id, total_requests, total_tokens, total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
            WITH seg_agg AS (
                SELECT trace_id,
                    AVG(NULLIF(tokens_raw, 0)) as avg_raw,
                    AVG(NULLIF(tokens_after_tp, 0)) as avg_tp
                FROM tp_segments GROUP BY trace_id
            )
            SELECT
                strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) as date,
                e.agent_id,
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings,
                COALESCE(AVG(NULLIF(sa.avg_raw, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_raw_tokens,
                COALESCE(AVG(NULLIF(sa.avg_tp, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_final_tokens,
                COALESCE(AVG(c.cost_total), 0) as avg_cost
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            LEFT JOIN seg_agg sa ON e.trace_id = sa.trace_id
            GROUP BY date, e.agent_id
        """)
        counts["agent"] = cur.rowcount

        self._conn.commit()
        return counts

    def get_rollup_timeseries(
        self,
        entity_type: str = "model",
        metric: str = "cost",
        since_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query rollup tables for fast timeseries data.

        Parameters
        ----------
        entity_type:
            ``"model"``, ``"provider"``, or ``"agent"``.
        metric:
            Column to return: ``"cost"``, ``"tokens"``, ``"savings"``, ``"requests"``.
        since_date:
            ISO date string (YYYY-MM-DD) to filter from.
        """
        table_map = {
            "model": "tp_rollup_daily_model",
            "provider": "tp_rollup_daily_provider",
            "agent": "tp_rollup_daily_agent",
        }
        col_map = {
            "cost": "total_cost",
            "tokens": "total_tokens",
            "savings": "total_savings",
            "requests": "total_requests",
        }
        table = table_map.get(entity_type, "tp_rollup_daily_model")
        col = col_map.get(metric, "total_cost")
        entity_col = (
            "model"
            if entity_type == "model"
            else ("provider" if entity_type == "provider" else "agent_id")
        )

        cur = self._conn.cursor()
        params: list[Any] = []
        where = ""
        if since_date:
            where = "WHERE date >= ?"
            params.append(since_date)

        sql = f"""
            SELECT date, {entity_col} as entity, {col} as value
            FROM {table}
            {where}
            ORDER BY date ASC, entity ASC
        """
        cur.execute(sql, params)
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
