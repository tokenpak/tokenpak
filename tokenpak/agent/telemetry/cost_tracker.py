"""TokenPak Cost Tracker — per-request cost logging with SQLite persistence.

Implements:
- Per-request cost recording (model, tokens, cost_usd, timestamp)
- Period summaries: day, week, month, all
- Per-model breakdowns
- Singleton accessor for proxy integration
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Model pricing table (per 1M tokens, USD)
# ---------------------------------------------------------------------------

MODEL_COSTS: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-3-5": {"input": 0.25, "output": 1.25},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gemini-2-flash": {"input": 0.075, "output": 0.30},
    "codex": {"input": 3.00, "output": 12.00},
    # Generic fallback (used when model not in table)
    "_fallback": {"input": 1.00, "output": 3.00},
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return estimated cost in USD for the given model and token counts."""
    # Exact match
    costs = MODEL_COSTS.get(model)
    if costs is None:
        # Prefix/substring match (e.g. "claude-sonnet-4-5-20241022" → claude-sonnet-4-5)
        for key, c in MODEL_COSTS.items():
            if key != "_fallback" and (model.startswith(key) or key in model):
                costs = c
                break
    if costs is None:
        costs = MODEL_COSTS["_fallback"]
    return (prompt_tokens * costs["input"] + completion_tokens * costs["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS cost_requests (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    model            TEXT    NOT NULL DEFAULT '',
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL    NOT NULL DEFAULT 0,
    session_id       TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_cost_ts ON cost_requests(timestamp);
CREATE INDEX IF NOT EXISTS idx_cost_model ON cost_requests(model);
"""


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Track per-request LLM cost with SQLite persistence.

    Usage::

        tracker = CostTracker("~/.tokenpak/cost.db")
        cost = tracker.record_request("claude-sonnet-4-5", 1000, 250)
        summary = tracker.get_summary("day")
    """

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = str(Path(db_path).expanduser()) if db_path != ":memory:" else db_path
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._conn()
        conn.executescript(_DDL)
        conn.commit()

    @staticmethod
    def _period_clause(period: str) -> tuple[str, list]:
        """Return (WHERE clause fragment, params) for the given period."""
        today = date.today()
        if period == "day":
            return "date(timestamp) = ?", [today.isoformat()]
        if period == "week":
            since = (today - timedelta(days=6)).isoformat()
            return "date(timestamp) >= ?", [since]
        if period == "month":
            return "strftime('%Y-%m', timestamp) = ?", [today.strftime("%Y-%m")]
        # "all" or anything else
        return "1=1", []

    # -----------------------------------------------------------------------
    # Write
    # -----------------------------------------------------------------------

    def record_request(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        session_id: str = "",
        timestamp: Optional[str] = None,
    ) -> float:
        """Record a completed request and return the estimated cost_usd."""
        from datetime import datetime as _dt

        cost = estimate_cost(model, prompt_tokens, completion_tokens)
        ts = timestamp or _dt.now().isoformat(timespec="seconds")

        with self._lock:
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO cost_requests
                    (timestamp, model, prompt_tokens, completion_tokens, cost_usd, session_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, model, prompt_tokens, completion_tokens, cost, session_id),
            )
            conn.commit()
        return cost

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    def get_summary(self, period: str = "day") -> dict:
        """Return summary dict for the given period.

        Returns:
            {
                "period": str,
                "total_requests": int,
                "total_tokens": int,
                "total_cost_usd": float,
                "prompt_tokens": int,
                "completion_tokens": int,
            }
        """
        where, params = self._period_clause(period)
        row = (
            self._conn()
            .execute(
                f"""
            SELECT
                COUNT(*)                         AS total_requests,
                COALESCE(SUM(prompt_tokens), 0)  AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                COALESCE(SUM(cost_usd), 0)       AS total_cost_usd
            FROM cost_requests
            WHERE {where}
            """,
                params,
            )
            .fetchone()
        )
        return {
            "period": period,
            "total_requests": row["total_requests"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["prompt_tokens"] + row["completion_tokens"],
            "total_cost_usd": round(float(row["total_cost_usd"]), 6),
        }

    def get_by_model(self, period: str = "day") -> list[dict]:
        """Return per-model breakdown for the given period.

        Returns list of dicts, each with:
            model, requests, prompt_tokens, completion_tokens, total_tokens, cost_usd
        """
        where, params = self._period_clause(period)
        rows = (
            self._conn()
            .execute(
                f"""
            SELECT
                model,
                COUNT(*)                              AS requests,
                COALESCE(SUM(prompt_tokens), 0)       AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)   AS completion_tokens,
                COALESCE(SUM(cost_usd), 0)            AS cost_usd
            FROM cost_requests
            WHERE {where}
            GROUP BY model
            ORDER BY cost_usd DESC
            """,
                params,
            )
            .fetchall()
        )
        return [
            {
                "model": r["model"],
                "requests": r["requests"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
                "total_tokens": r["prompt_tokens"] + r["completion_tokens"],
                "cost_usd": round(float(r["cost_usd"]), 6),
            }
            for r in rows
        ]

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_tracker: Optional[CostTracker] = None
_tracker_lock = threading.Lock()


def get_cost_tracker(db_path: str = "~/.tokenpak/cost.db") -> CostTracker:
    """Return the process-level singleton CostTracker."""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = CostTracker(db_path)
    return _tracker
