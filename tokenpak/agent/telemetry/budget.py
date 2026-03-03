"""TokenPak Agent Budget Tracker — Phase 1 stub.

Local cost tracking and budget enforcement for the proxy.
Used by `tokenpak cost` and `tokenpak budget` CLI commands.

THIS IS A STUB. Full implementation arrives in Phase 1 (task 1.8).
The interface is defined here so Phase 0 modules can import it safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class BudgetConfig:
    """User-configured budget limits."""
    daily_limit_usd: Optional[float] = None
    monthly_limit_usd: Optional[float] = None
    alert_at_percent: float = 80.0   # Alert when this % of budget is consumed
    hard_stop: bool = False          # If True, block requests when budget exceeded


@dataclass
class BudgetStatus:
    """Current budget consumption snapshot."""
    period: str          # "daily" | "monthly"
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
            "spent_usd": self.spent_usd,
            "remaining_usd": self.remaining_usd,
            "percent_used": self.percent_used,
            "alert_triggered": self.alert_triggered,
            "as_of": self.as_of.isoformat(),
        }


class BudgetTracker:
    """STUB: Track spend against configured budget limits.

    Phase 1 will implement:
    - SQLite-backed spend ledger
    - Daily / monthly rollups
    - Alert callbacks
    - Hard-stop middleware integration
    - `tokenpak cost` and `tokenpak budget set/status/reset` commands
    """

    def __init__(self, config: Optional[BudgetConfig] = None):
        self.config = config or BudgetConfig()

    def record_spend(self, cost_usd: float, request_id: str = "") -> None:
        """Record spend for a completed request. No-op in stub."""
        pass

    def get_status(self, period: str = "daily") -> Optional[BudgetStatus]:
        """Return current budget status. Returns None in stub."""
        return None

    def is_budget_exceeded(self) -> bool:
        """Return True if any configured limit is exceeded. Always False in stub."""
        return False


_tracker: Optional[BudgetTracker] = None


def get_budget_tracker(config: Optional[BudgetConfig] = None) -> BudgetTracker:
    """Return the process-level singleton budget tracker."""
    global _tracker
    if _tracker is None:
        _tracker = BudgetTracker(config)
    return _tracker
