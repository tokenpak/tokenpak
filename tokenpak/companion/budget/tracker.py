# SPDX-License-Identifier: Apache-2.0
"""Rolling cost tracker — accumulates spend across a session and across a day.

The tracker maintains two windows:
    - **Session**: cost since ``tokenpak claude`` launched
    - **Daily**: cost across all sessions today (persisted to SQLite)

Cost is estimated from token counts using the same MODEL_COSTS table as the
proxy.  This is an estimate — the proxy's telemetry DB is the source of truth
for actual billing.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from tokenpak.models import get_rates as _registry_get_rates

from .. import _sqlite as _db


@dataclass
class CostEstimate:
    """Pre-send cost estimate for a single request."""

    input_tokens: int = 0
    cached_tokens: int = 0
    model: str = ""
    estimated_cost_usd: float = 0.0
    session_total_usd: float = 0.0
    daily_total_usd: float = 0.0
    daily_budget_usd: float = 0.0
    budget_remaining_usd: float = 0.0
    over_budget: bool = False


class BudgetTracker:
    """Track and gate costs across a session and day."""

    def __init__(self, db_path: Path, daily_budget: float = 0.0) -> None:
        self._db_path = db_path
        self._daily_budget = daily_budget
        self._session_cost = 0.0
        self._session_requests = 0
        self._write_lock = threading.RLock()
        self._write_conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        # Canonical schema lives in companion._sqlite — shared with the
        # pre-send hook so there is exactly one DDL for companion_costs.
        _db.ensure_costs_schema(conn)
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        """Open the budget DB via the shared companion connection factory
        (busy_timeout is applied before the WAL switch there, so concurrent
        first-openers wait for the conversion instead of failing)."""
        return _db.connect(self._db_path, check_same_thread=False, foreign_keys=True)

    def _writer(self) -> sqlite3.Connection:
        if self._write_conn is None:
            self._write_conn = self._connect()
        return self._write_conn

    def estimate(
        self,
        input_tokens: int,
        cached_tokens: int = 0,
        model: str = "sonnet",
    ) -> CostEstimate:
        """Estimate cost for a request without recording it."""
        rates = _resolve_rates(model)
        fresh_input = max(0, input_tokens - cached_tokens)
        cost = (
            fresh_input * rates["input"] / 1_000_000 + cached_tokens * rates["cached"] / 1_000_000
        )
        # DB already contains all recorded costs (including current session).
        # Do NOT add _session_cost — that would double-count.
        daily_total = self._get_daily_total()
        remaining = (
            max(0.0, self._daily_budget - daily_total) if self._daily_budget > 0 else float("inf")
        )

        return CostEstimate(
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            model=model,
            estimated_cost_usd=round(cost, 6),
            session_total_usd=round(self._session_cost, 4),
            daily_total_usd=round(daily_total, 4),
            daily_budget_usd=self._daily_budget,
            budget_remaining_usd=round(remaining, 4),
            over_budget=self._daily_budget > 0 and daily_total + cost > self._daily_budget,
        )

    def record(
        self,
        input_tokens: int,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        model: str = "sonnet",
        session_id: str = "",
    ) -> None:
        """Record a completed request's cost."""
        rates = _resolve_rates(model)
        fresh_input = max(0, input_tokens - cached_tokens)
        cost = (
            fresh_input * rates["input"] / 1_000_000
            + cached_tokens * rates["cached"] / 1_000_000
            + output_tokens * rates["output"] / 1_000_000
        )

        now = time.time()
        import datetime

        date_str = datetime.date.today().isoformat()

        with self._write_lock:
            self._session_cost += cost
            self._session_requests += 1
            conn = self._writer()
            # kind='actual': this plane reports completed-request usage. The
            # daily gate prefers these rows over the pre-send 'estimate' rows
            # for the same session so a message is never counted twice.
            conn.execute(
                """INSERT INTO companion_costs
                   (timestamp, date, session_id, model, input_tokens, cached_tokens,
                    output_tokens, estimated_cost, kind)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'actual')""",
                (
                    now,
                    date_str,
                    session_id,
                    model,
                    input_tokens,
                    cached_tokens,
                    output_tokens,
                    round(cost, 6),
                ),
            )
            conn.commit()

    def _get_daily_total(self) -> float:
        """Query today's truthful total from the DB.

        Per (session, day): sums actual rows when present, otherwise takes
        the latest estimate — each message is counted exactly once instead
        of summing estimate + actual, or summing a cumulative pre-send
        estimate series (see companion._sqlite.DAILY_SPEND_SQL).
        """
        import datetime

        today = datetime.date.today().isoformat()
        try:
            conn = self._connect()
            row = conn.execute(_db.DAILY_SPEND_SQL, (today,)).fetchone()
            conn.close()
            return float(row[0] or 0.0) if row else 0.0
        except Exception:
            return 0.0

    @property
    def session_cost(self) -> float:
        return self._session_cost

    @property
    def session_requests(self) -> int:
        return self._session_requests


def _resolve_rates(model: str) -> dict[str, float]:
    """Match a model name to its pricing rates.

    Delegates to the dynamic model registry for resolution with family
    inference and provider-aware defaults.
    """
    return _registry_get_rates(model)
