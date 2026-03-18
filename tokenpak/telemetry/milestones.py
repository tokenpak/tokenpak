"""
TokenPak Telemetry — Cumulative Savings & Milestone System

Provides:
- get_savings_summary()     : lifetime/30d/7d savings with trend
- get_savings_history()     : daily cumulative savings for chart
- get_pending_milestones()  : unacknowledged milestones
- acknowledge_milestone()   : mark milestone as seen
- check_and_create_milestones(): detect new milestones after rollup
- _efficiency_score()       : 0-100 composite efficiency metric
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Milestone definitions
# ---------------------------------------------------------------------------

SAVINGS_THRESHOLDS = [100, 500, 1_000, 5_000, 10_000]  # USD
COMPRESSION_IMPROVEMENTS = [5, 10, 25, 50]  # % compression
RETRY_DROP_THRESHOLDS = [5.0, 1.0]  # % retry rate
REQUEST_COUNT_THRESHOLDS = [1_000, 10_000, 100_000]  # total requests

# (milestone_type, threshold, label_template)
_MILESTONE_DEFS = (
    [("savings_usd", t, f"You've saved over ${t:,.0f} with TokenPak") for t in SAVINGS_THRESHOLDS]
    + [("compression_pct", t, f"Compression improved to {t}%+") for t in COMPRESSION_IMPROVEMENTS]
    + [("retry_pct_below", t, f"Retry rate dropped below {t}%") for t in RETRY_DROP_THRESHOLDS]
    + [("request_count", t, f"First {t:,} requests tracked") for t in REQUEST_COUNT_THRESHOLDS]
)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SavingsSummary:
    lifetime_savings: float = 0.0
    savings_30d: float = 0.0
    savings_7d: float = 0.0
    trend_pct: float = 0.0  # % change (30d vs prev 30d)
    efficiency_score: float = 0.0  # 0-100 composite
    compression_pct: float = 0.0
    total_requests: int = 0


@dataclass
class SavingsDayPoint:
    date: str
    daily_savings: float
    cumulative_savings: float


@dataclass
class Milestone:
    id: int
    milestone_type: str
    threshold: float
    label: str
    reached_at: float
    acknowledged: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_conn(db_path: Optional[str]) -> sqlite3.Connection:
    if db_path is None:
        from pathlib import Path as _Path

        db_path = str(_Path(__file__).parent.parent.parent / "telemetry.db")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_milestones_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tp_milestones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            milestone_type  TEXT NOT NULL,
            threshold       REAL NOT NULL,
            label           TEXT NOT NULL,
            reached_at      REAL NOT NULL,
            acknowledged    INTEGER NOT NULL DEFAULT 0,
            UNIQUE(milestone_type, threshold)
        )
    """)
    conn.commit()


def _ts_ago(days: int) -> float:
    return time.time() - days * 86400


def _efficiency_score(compression_pct: float, error_pct: float, retry_pct: float) -> float:
    """Composite 0-100 efficiency score.

    Formula: (compression × 0.40) + (100 - error_pct) × 0.30 + (100 - retry_pct) × 0.30
    Clamp to [0, 100].
    """
    score = (
        min(compression_pct, 100) * 0.40
        + max(0, 100 - error_pct * 100) * 0.30
        + max(0, 100 - retry_pct * 100) * 0.30
    )
    return round(min(100, max(0, score)), 1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_savings_summary(db_path: Optional[str] = None) -> SavingsSummary:
    """Return lifetime/30d/7d savings with trend and efficiency score."""
    conn = _get_conn(db_path)
    try:
        _ensure_milestones_table(conn)
        cur = conn.cursor()

        def _savings_for_window(start_ts: float, end_ts: float) -> float:
            cur.execute(
                """SELECT COALESCE(SUM(c.savings_total), 0) AS sv
                   FROM tp_costs c
                   JOIN tp_events e ON c.trace_id = e.trace_id
                   WHERE e.ts >= ? AND e.ts <= ? AND e.event_type = 'request_end'""",
                (start_ts, end_ts),
            )
            r = cur.fetchone()
            return float(r[0] or 0)

        now = time.time()
        epoch = 0.0  # lifetime = all time

        lifetime = _savings_for_window(epoch, now)
        savings_30d = _savings_for_window(_ts_ago(30), now)
        savings_7d = _savings_for_window(_ts_ago(7), now)
        prev_30d = _savings_for_window(_ts_ago(60), _ts_ago(30))
        trend_pct = ((savings_30d - prev_30d) / prev_30d * 100) if prev_30d > 0 else 0.0

        # Compression ratio
        cur.execute(
            """SELECT
                COALESCE(SUM(u.input_billed), 0) AS billed,
                COALESCE(SUM(u.cache_read), 0) AS cr
               FROM tp_usage u
               JOIN tp_events e ON u.trace_id = e.trace_id
               WHERE e.ts >= ? AND e.event_type = 'request_end'""",
            (_ts_ago(30),),
        )
        ur = cur.fetchone()
        billed = float(ur[0] or 0)
        cache_read = float(ur[1] or 0)
        total_in = billed + cache_read
        compression_pct = (cache_read / total_in * 100) if total_in > 0 else 0.0

        # Error/retry rates (30d)
        cur.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN e.status = 'error' THEN 1 ELSE 0 END) AS errors,
                SUM(CASE WHEN e.status = 'retry' THEN 1 ELSE 0 END) AS retries
               FROM tp_events e
               WHERE e.ts >= ? AND e.event_type = 'request_end'""",
            (_ts_ago(30),),
        )
        er = cur.fetchone()
        total_req = int(er[0] or 0)
        error_pct = float(er[1] or 0) / total_req if total_req else 0.0
        retry_pct = float(er[2] or 0) / total_req if total_req else 0.0

        # Total lifetime requests
        cur.execute("SELECT COUNT(*) FROM tp_events WHERE event_type = 'request_end'")
        total_lifetime = int((cur.fetchone() or [0])[0])

        score = _efficiency_score(compression_pct, error_pct, retry_pct)

        return SavingsSummary(
            lifetime_savings=round(lifetime, 4),
            savings_30d=round(savings_30d, 4),
            savings_7d=round(savings_7d, 4),
            trend_pct=round(trend_pct, 1),
            efficiency_score=score,
            compression_pct=round(compression_pct, 1),
            total_requests=total_lifetime,
        )
    finally:
        conn.close()


def get_savings_history(db_path: Optional[str] = None, days: int = 90) -> list[SavingsDayPoint]:
    """Return daily savings + cumulative sum for chart rendering."""
    conn = _get_conn(db_path)
    try:
        _ensure_milestones_table(conn)
        cur = conn.cursor()
        cur.execute(
            """SELECT DATE(e.ts, 'unixepoch') AS dt,
                      COALESCE(SUM(c.savings_total), 0) AS daily_sv
               FROM tp_costs c
               JOIN tp_events e ON c.trace_id = e.trace_id
               WHERE e.ts >= ? AND e.event_type = 'request_end'
               GROUP BY dt
               ORDER BY dt""",
            (_ts_ago(days),),
        )
        rows = cur.fetchall()
        result = []
        cumulative = 0.0
        for row in rows:
            daily = float(row[1] or 0)
            cumulative += daily
            result.append(
                SavingsDayPoint(
                    date=row[0],
                    daily_savings=round(daily, 4),
                    cumulative_savings=round(cumulative, 4),
                )
            )
        return result
    finally:
        conn.close()


def get_pending_milestones(db_path: Optional[str] = None) -> list[Milestone]:
    """Return milestones that have been reached but not acknowledged."""
    conn = _get_conn(db_path)
    try:
        _ensure_milestones_table(conn)
        cur = conn.cursor()
        cur.execute(
            """SELECT id, milestone_type, threshold, label, reached_at, acknowledged
               FROM tp_milestones
               WHERE acknowledged = 0
               ORDER BY reached_at""",
        )
        return [
            Milestone(
                id=int(r[0]),
                milestone_type=str(r[1]),
                threshold=float(r[2]),
                label=str(r[3]),
                reached_at=float(r[4]),
                acknowledged=bool(r[5]),
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def get_milestone_history(db_path: Optional[str] = None) -> list[Milestone]:
    """Return all milestones (including acknowledged), newest first."""
    conn = _get_conn(db_path)
    try:
        _ensure_milestones_table(conn)
        cur = conn.cursor()
        cur.execute(
            """SELECT id, milestone_type, threshold, label, reached_at, acknowledged
               FROM tp_milestones
               ORDER BY reached_at DESC""",
        )
        return [
            Milestone(
                id=int(r[0]),
                milestone_type=str(r[1]),
                threshold=float(r[2]),
                label=str(r[3]),
                reached_at=float(r[4]),
                acknowledged=bool(r[5]),
            )
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def acknowledge_milestone(milestone_id: int, db_path: Optional[str] = None) -> bool:
    """Mark a milestone as acknowledged. Returns True if updated."""
    conn = _get_conn(db_path)
    try:
        _ensure_milestones_table(conn)
        cur = conn.cursor()
        cur.execute("UPDATE tp_milestones SET acknowledged = 1 WHERE id = ?", (milestone_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def check_and_create_milestones(db_path: Optional[str] = None) -> list[Milestone]:
    """Detect new milestones and insert them. Returns newly created milestones."""
    conn = _get_conn(db_path)
    try:
        _ensure_milestones_table(conn)
        now = time.time()
        cur = conn.cursor()
        new_milestones = []

        # ── Current metric values ──────────────────────────────────────────

        # Lifetime savings
        cur.execute("""SELECT COALESCE(SUM(c.savings_total), 0)
               FROM tp_costs c
               JOIN tp_events e ON c.trace_id = e.trace_id
               WHERE e.event_type = 'request_end'""")
        lifetime_savings = float((cur.fetchone() or [0])[0])

        # 30-day compression %
        cur.execute(
            """SELECT
                COALESCE(SUM(u.input_billed), 0),
                COALESCE(SUM(u.cache_read), 0)
               FROM tp_usage u
               JOIN tp_events e ON u.trace_id = e.trace_id
               WHERE e.ts >= ? AND e.event_type = 'request_end'""",
            (_ts_ago(30),),
        )
        ur = cur.fetchone()
        billed, cache_read = float(ur[0] or 0), float(ur[1] or 0)
        total_in = billed + cache_read
        compression_pct = (cache_read / total_in * 100) if total_in > 0 else 0.0

        # 30-day retry rate %
        cur.execute(
            """SELECT COUNT(*), SUM(CASE WHEN e.status = 'retry' THEN 1 ELSE 0 END)
               FROM tp_events e
               WHERE e.ts >= ? AND e.event_type = 'request_end'""",
            (_ts_ago(30),),
        )
        rr = cur.fetchone()
        total_req_30d = int(rr[0] or 0)
        retry_pct = float(rr[1] or 0) / total_req_30d * 100 if total_req_30d else 100.0

        # Total lifetime requests
        cur.execute("SELECT COUNT(*) FROM tp_events WHERE event_type = 'request_end'")
        total_lifetime = int((cur.fetchone() or [0])[0])

        # ── Check each milestone def ───────────────────────────────────────

        for m_type, threshold, label in _MILESTONE_DEFS:
            # Check if already recorded
            cur.execute(
                "SELECT id FROM tp_milestones WHERE milestone_type=? AND threshold=?",
                (m_type, threshold),
            )
            if cur.fetchone():
                continue  # already exists

            reached = False
            if m_type == "savings_usd":
                reached = lifetime_savings >= threshold
            elif m_type == "compression_pct":
                reached = compression_pct >= threshold
            elif m_type == "retry_pct_below":
                reached = retry_pct < threshold
            elif m_type == "request_count":
                reached = total_lifetime >= threshold

            if reached:
                cur.execute(
                    """INSERT INTO tp_milestones (milestone_type, threshold, label, reached_at, acknowledged)
                       VALUES (?, ?, ?, ?, 0)""",
                    (m_type, threshold, label, now),
                )
                mid = cur.lastrowid
                conn.commit()
                new_milestones.append(
                    Milestone(
                        id=mid,  # type: ignore[arg-type]
                        milestone_type=m_type,
                        threshold=threshold,
                        label=label,
                        reached_at=now,
                        acknowledged=False,
                    )
                )

        return new_milestones
    finally:
        conn.close()
