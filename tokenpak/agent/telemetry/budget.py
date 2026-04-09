"""TokenPak Agent Budget Tracker — local cost tracking and budget enforcement.

Implements:
- SQLite-backed spend ledger (actual API cost, not just savings)
- Daily / monthly rollup queries
- Budget limit configuration (YAML)
- Alert threshold checks
- `tokenpak cost` / `tokenpak budget` CLI backend
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BudgetConfig:
    """User-configured budget limits."""

    daily_limit_usd: Optional[float] = None
    monthly_limit_usd: Optional[float] = None
    alert_at_percent: float = 80.0  # Alert when this % of budget is consumed
    hard_stop: bool = False  # If True, block requests when budget exceeded

    def to_dict(self) -> dict:
        return {
            "daily_limit_usd": self.daily_limit_usd,
            "monthly_limit_usd": self.monthly_limit_usd,
            "alert_at_percent": self.alert_at_percent,
            "hard_stop": self.hard_stop,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BudgetConfig":
        return cls(
            daily_limit_usd=d.get("daily_limit_usd"),
            monthly_limit_usd=d.get("monthly_limit_usd"),
            alert_at_percent=float(d.get("alert_at_percent", 80.0)),
            hard_stop=bool(d.get("hard_stop", False)),
        )


@dataclass
class SpendRecord:
    """One logged spend event."""

    request_id: str
    timestamp: datetime
    model: str
    cost_usd: float
    tokens_input: int = 0
    tokens_output: int = 0
    agent: str = ""


@dataclass
class BudgetStatus:
    """Current budget consumption snapshot."""

    period: str  # "daily" | "monthly"
    limit_usd: float
    spent_usd: float
    remaining_usd: float
    percent_used: float
    alert_triggered: bool
    as_of: datetime

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "limit_usd": self.limit_usd,
            "spent_usd": round(self.spent_usd, 4),
            "remaining_usd": round(self.remaining_usd, 4),
            "percent_used": round(self.percent_used, 1),
            "alert_triggered": self.alert_triggered,
            "as_of": self.as_of.isoformat(),
        }


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tp_spend (
    request_id   TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    cost_usd     REAL NOT NULL DEFAULT 0,
    tokens_input  INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    agent        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_spend_ts ON tp_spend(timestamp);
"""


# ---------------------------------------------------------------------------
# BudgetTracker
# ---------------------------------------------------------------------------


class BudgetTracker:
    """Track actual API spend against configured budget limits.

    Usage::

        tracker = BudgetTracker(db_path="~/.tokenpak/budget.db")
        tracker.record_spend(0.012, request_id="req-001", model="claude-sonnet")
        status = tracker.get_status("daily")
        print(status.to_dict())
    """

    def __init__(
        self,
        config: Optional[BudgetConfig] = None,
        db_path: str = ":memory:",
    ):
        self.config = config or BudgetConfig()
        self._db_path = str(Path(db_path).expanduser()) if db_path != ":memory:" else db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    # -----------------------------------------------------------------------
    # DB helpers
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

    # -----------------------------------------------------------------------
    # Write
    # -----------------------------------------------------------------------

    def record_spend(
        self,
        cost_usd: float,
        *,
        request_id: str = "",
        model: str = "",
        tokens_input: int = 0,
        tokens_output: int = 0,
        agent: str = "",
        timestamp: Optional[datetime] = None,
    ) -> SpendRecord:
        """Record spend for a completed request."""
        ts = timestamp or datetime.now()
        rid = request_id or f"req-{ts.strftime('%Y%m%d%H%M%S%f')}"
        record = SpendRecord(
            request_id=rid,
            timestamp=ts,
            model=model,
            cost_usd=cost_usd,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            agent=agent,
        )
        with self._lock:
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO tp_spend
                    (request_id, timestamp, model, cost_usd, tokens_input, tokens_output, agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (rid, ts.isoformat(), model, cost_usd, tokens_input, tokens_output, agent),
            )
            conn.commit()
        return record

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    def total_spent(self, period: str = "daily") -> float:
        """Return total spend for the given period ('daily' or 'monthly')."""
        conn = self._conn()
        if period == "daily":
            since = date.today().isoformat()
            fmt = "date(timestamp) >= ?"
        elif period == "monthly":
            since = date.today().strftime("%Y-%m")
            fmt = "strftime('%Y-%m', timestamp) >= ?"
        elif period == "weekly":
            since = (date.today() - timedelta(days=6)).isoformat()
            fmt = "date(timestamp) >= ?"
        else:
            # all-time
            row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM tp_spend").fetchone()
            return float(row[0])

        row = conn.execute(
            f"SELECT COALESCE(SUM(cost_usd), 0) FROM tp_spend WHERE {fmt}", (since,)
        ).fetchone()
        return float(row[0])

    def get_status(self, period: str = "daily") -> Optional[BudgetStatus]:
        """Return BudgetStatus for the period, or None if no limit is configured."""
        limit = self.config.daily_limit_usd if period == "daily" else self.config.monthly_limit_usd
        if limit is None:
            return None
        spent = self.total_spent(period)
        remaining = max(0.0, limit - spent)
        pct = (spent / limit * 100) if limit > 0 else 0.0
        alert = pct >= self.config.alert_at_percent
        return BudgetStatus(
            period=period,
            limit_usd=limit,
            spent_usd=spent,
            remaining_usd=remaining,
            percent_used=pct,
            alert_triggered=alert,
            as_of=datetime.now(),
        )

    def is_budget_exceeded(self) -> bool:
        """Return True if any configured limit is exceeded."""
        for period, limit in [
            ("daily", self.config.daily_limit_usd),
            ("monthly", self.config.monthly_limit_usd),
        ]:
            if limit is not None and self.total_spent(period) >= limit:
                return True
        return False

    def list_spend(
        self,
        limit: int = 50,
        period: Optional[str] = None,
        model: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> list[dict]:
        """List spend records with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []

        if period == "daily":
            conditions.append("date(timestamp) = ?")
            params.append(date.today().isoformat())
        elif period == "monthly":
            conditions.append("strftime('%Y-%m', timestamp) = ?")
            params.append(date.today().strftime("%Y-%m"))
        elif period == "weekly":
            conditions.append("date(timestamp) >= ?")
            params.append((date.today() - timedelta(days=6)).isoformat())

        if model:
            conditions.append("model = ?")
            params.append(model)
        if agent:
            conditions.append("agent = ?")
            params.append(agent)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        rows = (
            self._conn()
            .execute(f"SELECT * FROM tp_spend {where} ORDER BY timestamp DESC LIMIT ?", params)
            .fetchall()
        )
        return [dict(r) for r in rows]

    def by_model_summary(self, period: Optional[str] = None) -> list[dict]:
        """Return spend grouped by model."""
        conditions: list[str] = []
        params: list[Any] = []
        if period == "daily":
            conditions.append("date(timestamp) = ?")
            params.append(date.today().isoformat())
        elif period == "monthly":
            conditions.append("strftime('%Y-%m', timestamp) = ?")
            params.append(date.today().strftime("%Y-%m"))

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = (
            self._conn()
            .execute(
                f"""
            SELECT model,
                   COUNT(*) AS requests,
                   SUM(tokens_input) AS tokens_input,
                   SUM(tokens_output) AS tokens_output,
                   SUM(cost_usd) AS cost_usd
            FROM tp_spend {where}
            GROUP BY model
            ORDER BY cost_usd DESC
            """,
                params,
            )
            .fetchall()
        )
        return [dict(r) for r in rows]

    def export_csv(self, period: Optional[str] = None) -> str:
        """Return CSV string of spend records."""
        import csv
        import io

        rows = self.list_spend(limit=100_000, period=period)
        if not rows:
            return "request_id,timestamp,model,cost_usd,tokens_input,tokens_output,agent\n"
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue()

    def prune(self, days: int = 90) -> int:
        """Delete spend records older than N days."""
        conn = self._conn()
        cur = conn.execute(
            "DELETE FROM tp_spend WHERE timestamp < datetime('now', ?)", (f"-{days} days",)
        )
        conn.commit()
        return cur.rowcount

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


def _budget_config_path() -> Path:
    return Path("~/.tokenpak/budget_config.yaml").expanduser()


def load_budget_config() -> BudgetConfig:
    p = _budget_config_path()
    if not p.exists():
        return BudgetConfig()
    try:
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        return BudgetConfig.from_dict(data)
    except Exception:
        return BudgetConfig()


def save_budget_config(cfg: BudgetConfig) -> None:
    p = _budget_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(cfg.to_dict(), f, default_flow_style=False)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_tracker: Optional[BudgetTracker] = None


def get_budget_tracker() -> BudgetTracker:
    """Return process-level singleton budget tracker."""
    global _tracker
    if _tracker is None:
        cfg = load_budget_config()
        db = Path("~/.tokenpak/budget.db").expanduser()
        db.parent.mkdir(parents=True, exist_ok=True)
        _tracker = BudgetTracker(config=cfg, db_path=str(db))
    return _tracker
