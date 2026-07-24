"""
TokenPak Budget Tracker

Real-time budget threshold alerts for cost management.

Provides:
  - load_budget_config(): Load daily/weekly budget limits from config
  - check_spending_vs_limit(): Compare current spend to configured limits
  - should_alert(): Determine if threshold alerts should fire
  - Track alert history to avoid duplicate notifications

Alert Thresholds:
  - 80%: Warning — approaching limit
  - 100%: Critical — at or over daily limit
  - 110%: Overage — exceeds limit significantly
"""

from __future__ import annotations

__all__ = (
    "AlertLevel",
    "BudgetAlert",
    "BudgetConfig",
    "BudgetTracker",
)


import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional, TypedDict

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels"""

    WARNING = 80  # 80% of limit
    CRITICAL = 100  # At/over daily limit
    OVERAGE = 110  # 10%+ over limit


@dataclass
class BudgetAlert:
    """Alert fired when spending reaches a threshold"""

    level: AlertLevel
    threshold_pct: int
    current_spend: float
    limit: float
    limit_type: str  # "daily" or "weekly"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    message: str = ""

    def __post_init__(self) -> None:
        if not self.message:
            self.message = self._format_message()

    def _format_message(self) -> str:
        pct_formatted = f"{(self.current_spend / self.limit * 100):.1f}%"
        return (
            f"[{self.level.name}] {self.limit_type.upper()} budget at {pct_formatted} "
            f"(${self.current_spend:.2f} / ${self.limit:.2f})"
        )

    def __str__(self) -> str:
        return self.message


@dataclass
class BudgetConfig:
    """Budget configuration"""

    daily_limit: Optional[float] = None
    weekly_limit: Optional[float] = None
    enabled: bool = True


class BudgetConfigInput(TypedDict, total=False):
    """Supported budget configuration keys."""

    daily_limit: Optional[float]
    weekly_limit: Optional[float]
    enabled: bool


class BudgetSummary(TypedDict):
    """Serializable budget-tracker state."""

    enabled: bool
    daily_limit: Optional[float]
    weekly_limit: Optional[float]
    alert_cooldown_minutes: float
    last_alerts: dict[str, str]


class BudgetTracker:
    """Track spending against configurable budget thresholds"""

    def __init__(self, config: Optional[BudgetConfigInput] = None):
        """
        Initialize budget tracker.

        Args:
            config: Dict with optional 'daily_limit' and 'weekly_limit' keys
        """
        self.config = self._parse_config(config or {})
        self.alert_history: Dict[str, datetime] = {}  # threshold_key -> last_alert_time
        self.alert_cooldown = timedelta(minutes=5)  # Avoid duplicate alerts

    def _parse_config(self, config: BudgetConfigInput) -> BudgetConfig:
        """Parse budget config from dict"""
        return BudgetConfig(
            daily_limit=config.get("daily_limit"),
            weekly_limit=config.get("weekly_limit"),
            enabled=config.get("enabled", True),
        )

    def load_budget_config(self, config_dict: BudgetConfigInput) -> None:
        """Load budget configuration from dict"""
        self.config = self._parse_config(config_dict)
        logger.info(
            f"Loaded budget config: daily={self.config.daily_limit}, "
            f"weekly={self.config.weekly_limit}"
        )

    def check_spending_vs_limit(
        self,
        current_spend: float,
        limit_type: str = "daily",
    ) -> tuple[bool, Optional[float]]:
        """
        Check if spending exceeds limit.

        Args:
            current_spend: Current spending amount in USD
            limit_type: "daily" or "weekly"

        Returns:
            (is_over_limit, limit_value)
        """
        if limit_type == "daily":
            limit = self.config.daily_limit
        elif limit_type == "weekly":
            limit = self.config.weekly_limit
        else:
            raise ValueError(f"Unknown limit_type: {limit_type}")

        if limit is None:
            return False, None

        return current_spend > limit, limit

    def should_alert(
        self,
        current_spend: float,
        limit: float,
        limit_type: str = "daily",
    ) -> Optional[BudgetAlert]:
        """
        Check if alert should fire based on spending level.

        Args:
            current_spend: Current spending amount in USD
            limit: Budget limit in USD
            limit_type: "daily" or "weekly"

        Returns:
            BudgetAlert if threshold reached and cooldown elapsed, else None
        """
        if not self.config.enabled or limit is None:
            return None

        pct = (current_spend / limit) * 100

        # Determine alert level based on percentage
        if pct >= 110:
            alert_level = AlertLevel.OVERAGE
        elif pct >= 100:
            alert_level = AlertLevel.CRITICAL
        elif pct >= 80:
            alert_level = AlertLevel.WARNING
        else:
            return None  # Below warning threshold

        # Check cooldown to avoid duplicate alerts
        alert_key = f"{limit_type}_{alert_level.name}"
        now = datetime.now(timezone.utc)
        last_alert = self.alert_history.get(alert_key)

        if last_alert and (now - last_alert) < self.alert_cooldown:
            logger.debug(f"Alert {alert_key} in cooldown, skipping")
            return None

        # Create and record alert
        alert = BudgetAlert(
            level=alert_level,
            threshold_pct=alert_level.value,
            current_spend=current_spend,
            limit=limit,
            limit_type=limit_type,
            timestamp=now,
        )

        self.alert_history[alert_key] = now
        logger.warning(f"Budget alert: {alert}")

        return alert

    def get_budget_summary(self) -> BudgetSummary:
        """Get human-readable budget summary"""
        return {
            "enabled": self.config.enabled,
            "daily_limit": self.config.daily_limit,
            "weekly_limit": self.config.weekly_limit,
            "alert_cooldown_minutes": self.alert_cooldown.total_seconds() / 60,
            "last_alerts": {k: v.isoformat() for k, v in self.alert_history.items()},
        }

    def format_budget_display(
        self,
        current_spend: float,
        limit: float,
        limit_type: str = "daily",
    ) -> str:
        """
        Format budget progress bar for display.

        Example:
            [████░░░░] 73% of daily budget ($73.00 / $100.00)
        """
        if limit is None:
            return "No budget limit configured"

        pct = min(100, (current_spend / limit) * 100)
        filled = int(pct / 10)
        empty = 10 - filled
        bar = "█" * filled + "░" * empty

        return f"[{bar}] {pct:.0f}% of {limit_type} budget (${current_spend:.2f} / ${limit:.2f})"

    def reset_alert_history(self) -> None:
        """Clear alert history (useful for testing)"""
        self.alert_history.clear()
        logger.debug("Alert history cleared")
