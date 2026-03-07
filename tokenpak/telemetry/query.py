"""Query DSL parser and query engine for TokenPak telemetry API."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from tokenpak.telemetry.query_models import CostSummary, DailyTrend, ModelUsage, SavingsReport


@dataclass
class QueryFilter:
    """Filter parameters for telemetry database queries."""

    provider: Optional[str] = None
    model: Optional[str] = None
    agent: Optional[str] = None
    status: Optional[str] = None
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this query result to a plain dict."""
        result: dict[str, Any] = {}
        if self.provider:
            result["provider"] = self.provider
        if self.model:
            result["model"] = self.model
        if self.agent:
            result["agent_id"] = self.agent
        if self.status:
            result["status"] = self.status
        if self.since_ts:
            result["since_ts"] = self.since_ts
        if self.until_ts:
            result["until_ts"] = self.until_ts
        result.update(self.extra)
        return result

    def is_empty(self) -> bool:
        """Return True if this filter has no active constraints."""
        return not any(
            [
                self.provider,
                self.model,
                self.agent,
                self.status,
                self.since_ts,
                self.until_ts,
                self.extra,
            ]
        )


def parse_filter(dsl: Optional[str]) -> QueryFilter:
    """Parse raw query-string params into a QueryFilter instance."""
    result = QueryFilter()
    if not dsl or not dsl.strip():
        return result
    for part in dsl.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            if not result.model:
                result.model = part
            continue
        key, _, value = part.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not value:
            continue
        if key == "provider":
            result.provider = value
        elif key == "model":
            result.model = value
        elif key in ("agent", "agent_id"):
            result.agent = value
        elif key == "status":
            result.status = value
    return result


def build_sql_where(
    qf: QueryFilter, table_alias: str = "e", base_conditions: Optional[list[str]] = None
) -> tuple[str, list[Any]]:
    """Build a SQL WHERE clause from a QueryFilter; return (clause, params)."""
    conditions = list(base_conditions) if base_conditions else []
    params: list[Any] = []
    if qf.provider:
        conditions.append(f"{table_alias}.provider = ?")
        params.append(qf.provider)
    if qf.model:
        conditions.append(f"{table_alias}.model = ?")
        params.append(qf.model)
    if qf.agent:
        conditions.append(f"{table_alias}.agent_id = ?")
        params.append(qf.agent)
    if qf.status:
        conditions.append(f"{table_alias}.status = ?")
        params.append(qf.status)
    return ("WHERE " + " AND ".join(conditions), params) if conditions else ("", params)


DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "telemetry.db"


def _get_conn(db_path=None):
    conn = sqlite3.connect(str(db_path or DEFAULT_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ts_range(days):
    end = time.time()
    return end - days * 86400, end


def get_cost_summary(db_path=None, days=30) -> CostSummary:
    """Query aggregated cost summary from the telemetry DB."""
    conn = _get_conn(db_path)
    try:
        s, e = _ts_range(days)
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(c.actual_cost),0) FROM tp_costs c JOIN tp_events e ON c.trace_id=e.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end'",
            (s, e),
        )
        total = cur.fetchone()[0] or 0.0
        cur.execute(
            "SELECT e.model, COALESCE(SUM(c.actual_cost),0) as cost FROM tp_costs c JOIN tp_events e ON c.trace_id=e.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end' GROUP BY e.model",
            (s, e),
        )
        by_model = {r["model"]: r["cost"] for r in cur.fetchall()}
        cur.execute(
            "SELECT e.provider, COALESCE(SUM(c.actual_cost),0) as cost FROM tp_costs c JOIN tp_events e ON c.trace_id=e.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end' GROUP BY e.provider",
            (s, e),
        )
        by_prov = {r["provider"]: r["cost"] for r in cur.fetchall()}
        cur.execute(
            "SELECT DATE(e.ts,'unixepoch') as date, COALESCE(SUM(c.actual_cost),0) as cost FROM tp_costs c JOIN tp_events e ON c.trace_id=e.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end' GROUP BY date ORDER BY date",
            (s, e),
        )
        daily = [{"date": r["date"], "cost": r["cost"]} for r in cur.fetchall()]
        return CostSummary(
            total_cost=total, by_model=by_model, by_provider=by_prov, daily=daily, period_days=days
        )
    finally:
        conn.close()


def get_model_usage(db_path=None, days=30) -> list[ModelUsage]:
    """Query per-model token usage from the telemetry DB."""
    conn = _get_conn(db_path)
    try:
        s, e = _ts_range(days)
        cur = conn.cursor()
        cur.execute(
            "SELECT e.model, e.provider, COUNT(*) as cnt, COALESCE(SUM(u.input_billed),0) as inp, COALESCE(SUM(u.output_billed),0) as outp FROM tp_events e LEFT JOIN tp_usage u ON e.trace_id=u.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end' GROUP BY e.model,e.provider ORDER BY cnt DESC",
            (s, e),
        )
        return [
            ModelUsage(
                model=r["model"],
                provider=r["provider"],
                request_count=r["cnt"],
                total_input_tokens=r["inp"],
                total_output_tokens=r["outp"],
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def get_savings_report(db_path=None, days=30) -> SavingsReport:
    """Query token savings (raw vs compressed) from the telemetry DB."""
    conn = _get_conn(db_path)
    try:
        s, e = _ts_range(days)
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(c.actual_cost),0) as tc, COALESCE(SUM(c.baseline_cost),0) as bc, COALESCE(SUM(c.savings_total),0) as sv FROM tp_costs c JOIN tp_events e ON c.trace_id=e.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end'",
            (s, e),
        )
        r = cur.fetchone()
        tc, bc, sv = r["tc"] or 0, r["bc"] or 0, r["sv"] or 0
        cur.execute(
            "SELECT COALESCE(SUM(u.cache_read),0) as cr, COALESCE(SUM(u.input_billed+u.cache_read),0) as ti FROM tp_usage u JOIN tp_events e ON u.trace_id=e.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end'",
            (s, e),
        )
        cr = cur.fetchone()
        cache_read, total_in = cr["cr"] or 0, cr["ti"] or 0
        return SavingsReport(
            total_cost=tc,
            estimated_without_compression=bc,
            savings_amount=sv,
            savings_pct=(sv / bc * 100 if bc else 0),
            cache_hit_rate=(cache_read / total_in if total_in else 0),
        )
    finally:
        conn.close()


def get_recent_events(db_path=None, limit=50) -> list[dict]:
    """Fetch the most recent telemetry events up to limit."""
    conn = _get_conn(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT e.trace_id,e.request_id,e.event_type,e.ts,e.provider,e.model,e.agent_id,e.status,e.error_class,u.input_billed,u.output_billed,c.actual_cost FROM tp_events e LEFT JOIN tp_usage u ON e.trace_id=u.trace_id LEFT JOIN tp_costs c ON e.trace_id=c.trace_id WHERE e.event_type='request_end' ORDER BY e.ts DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "trace_id": r["trace_id"],
                "request_id": r["request_id"],
                "event_type": r["event_type"],
                "ts": r["ts"],
                "provider": r["provider"],
                "model": r["model"],
                "agent_id": r["agent_id"],
                "status": r["status"],
                "error_class": r["error_class"],
                "input_tokens": r["input_billed"],
                "output_tokens": r["output_billed"],
                "cost": r["actual_cost"],
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def get_daily_trend(db_path=None, days=30) -> list[DailyTrend]:
    """Fetch daily aggregated usage for trend charts."""
    conn = _get_conn(db_path)
    try:
        s, e = _ts_range(days)
        cur = conn.cursor()
        cur.execute(
            "SELECT DATE(e.ts,'unixepoch') as dt, COALESCE(SUM(c.actual_cost),0) as cost, COALESCE(SUM(u.input_billed),0) as inp, COALESCE(SUM(u.output_billed),0) as outp, COUNT(*) as cnt FROM tp_events e LEFT JOIN tp_usage u ON e.trace_id=u.trace_id LEFT JOIN tp_costs c ON e.trace_id=c.trace_id WHERE e.ts>=? AND e.ts<=? AND e.event_type='request_end' GROUP BY dt ORDER BY dt",
            (s, e),
        )
        return [
            DailyTrend(
                date=r["dt"],
                cost=r["cost"],
                input_tokens=r["inp"],
                output_tokens=r["outp"],
                request_count=r["cnt"],
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()
