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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


from tokenpak.models import get_rates as _registry_get_rates


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
        self._init_db()

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companion_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                date TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost REAL NOT NULL DEFAULT 0.0
            )
        """)
        conn.commit()
        conn.close()

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
            fresh_input * rates["input"] / 1_000_000
            + cached_tokens * rates["cached"] / 1_000_000
        )
        # DB already contains all recorded costs (including current session).
        # Do NOT add _session_cost — that would double-count.
        daily_total = self._get_daily_total()
        remaining = max(0.0, self._daily_budget - daily_total) if self._daily_budget > 0 else float("inf")

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
        self._session_cost += cost
        self._session_requests += 1

        now = time.time()
        import datetime
        date_str = datetime.date.today().isoformat()

        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            """INSERT INTO companion_costs
               (timestamp, date, session_id, model, input_tokens, cached_tokens,
                output_tokens, estimated_cost)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, date_str, session_id, model, input_tokens, cached_tokens,
             output_tokens, round(cost, 6)),
        )
        conn.commit()
        conn.close()

    def _get_daily_total(self) -> float:
        """Query today's total from the DB (excludes current session in-memory cost)."""
        import datetime
        today = datetime.date.today().isoformat()
        try:
            conn = sqlite3.connect(str(self._db_path))
            row = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost), 0) FROM companion_costs WHERE date = ?",
                (today,),
            ).fetchone()
            conn.close()
            return row[0] if row else 0.0
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
