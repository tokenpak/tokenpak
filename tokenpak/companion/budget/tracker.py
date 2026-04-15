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


# Model costs (USD per 1M tokens).
# Anthropic: cache-read discount = 10% of input price.
# OpenAI: cached input = 50% of input price.
_MODEL_COSTS: dict[str, dict[str, float]] = {
    # ── Anthropic ──────────────────────────────────────────────
    # Claude Opus — $15 input / $75 output / $1.50 cache-read
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "cached": 1.50},
    "claude-opus-4-5": {"input": 15.0, "output": 75.0, "cached": 1.50},
    # Claude Sonnet — $3 input / $15 output / $0.30 cache-read
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cached": 0.30},
    "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cached": 0.30},
    # Claude Haiku — $0.80 input / $4 output / $0.08 cache-read
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0, "cached": 0.08},
    "claude-haiku-3-5": {"input": 0.80, "output": 4.0, "cached": 0.08},
    # Short-form / legacy keys (matched by substring in _resolve_rates)
    "opus":   {"input": 15.0, "output": 75.0, "cached": 1.50},
    "sonnet": {"input": 3.0,  "output": 15.0, "cached": 0.30},
    "haiku":  {"input": 0.80, "output": 4.0,  "cached": 0.08},

    # ── OpenAI ─────────────────────────────────────────────────
    # GPT-4o — $2.50 input / $10 output / $1.25 cached
    "gpt-4o":      {"input": 2.50, "output": 10.0, "cached": 1.25},
    # GPT-4o mini — $0.15 input / $0.60 output / $0.075 cached
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cached": 0.075},
    # o3 — $10 input / $40 output / $5 cached
    "o3":          {"input": 10.0, "output": 40.0, "cached": 5.0},
    # o3-mini — $1.10 input / $4.40 output / $0.55 cached
    "o3-mini":     {"input": 1.10, "output": 4.40, "cached": 0.55},
    # o4-mini — $1.10 input / $4.40 output / $0.55 cached
    "o4-mini":     {"input": 1.10, "output": 4.40, "cached": 0.55},
    # o1 — $15 input / $60 output / $7.50 cached
    "o1":          {"input": 15.0, "output": 60.0, "cached": 7.50},
    # o1-mini — $1.10 input / $4.40 output / $0.55 cached
    "o1-mini":     {"input": 1.10, "output": 4.40, "cached": 0.55},
    # GPT-4.1 — $2.00 input / $8.00 output / $0.50 cached
    "gpt-4.1":     {"input": 2.0, "output": 8.0, "cached": 0.50},
    # GPT-4.1 mini — $0.40 input / $1.60 output / $0.10 cached
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cached": 0.10},
    # GPT-4.1 nano — $0.10 input / $0.40 output / $0.025 cached
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40, "cached": 0.025},
}


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

    Resolution order:
    1. Exact match (e.g. "claude-sonnet-4-6", "gpt-4o")
    2. Prefix match for versioned names (e.g. "gpt-4o-2024-08-06")
    3. Substring match for short-form names (e.g. "sonnet", "4o")
    4. Default: sonnet rates (Anthropic) or gpt-4o rates (OpenAI)

    Provider detection: models starting with "gpt-", "o1", "o3", or "o4"
    default to gpt-4o rates; all others default to sonnet.
    """
    if model in _MODEL_COSTS:
        return _MODEL_COSTS[model]
    model_lower = model.lower()
    # Prefix match first (longer keys win — sort descending by length)
    for key in sorted(_MODEL_COSTS, key=len, reverse=True):
        if model_lower.startswith(key) or key in model_lower:
            return _MODEL_COSTS[key]
    # Provider-aware default
    if model_lower.startswith(("gpt-", "o1", "o3", "o4")):
        return _MODEL_COSTS["gpt-4o"]
    return _MODEL_COSTS["sonnet"]
