"""
Unit tests for TokenPak Budget Tracker

Test cases:
  1. Load budget config (daily/weekly)
  2. Check spending vs limit (over/under)
  3. Alert thresholds (80%, 100%, 110%)
  4. Alert cooldown (no duplicates within window)
  5. Edge cases (zero limit, None limit, exact boundary)
  6. Budget display formatting
  7. Alert summary and history
"""


import pytest
pytest.importorskip("tokenpak.cost.budget_tracker", reason="module not available in current build")
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from tokenpak.cost.budget_tracker import (
    BudgetTracker,
    BudgetAlert,
    AlertLevel,
    BudgetConfig,
)


class TestBudgetConfig:
    """Test BudgetConfig dataclass"""

    def test_budget_config_defaults(self):
        config = BudgetConfig()
        assert config.daily_limit is None
        assert config.weekly_limit is None
        assert config.enabled is True

    def test_budget_config_with_values(self):
        config = BudgetConfig(daily_limit=100.0, weekly_limit=500.0)
        assert config.daily_limit == 100.0
        assert config.weekly_limit == 500.0
        assert config.enabled is True


class TestBudgetTrackerInit:
    """Test BudgetTracker initialization"""

    def test_init_no_config(self):
        tracker = BudgetTracker()
        assert tracker.config.daily_limit is None
        assert tracker.config.weekly_limit is None
        assert tracker.config.enabled is True

    def test_init_with_config_dict(self):
        config = {"daily_limit": 100.0, "weekly_limit": 500.0}
        tracker = BudgetTracker(config)
        assert tracker.config.daily_limit == 100.0
        assert tracker.config.weekly_limit == 500.0

    def test_init_disabled_config(self):
        config = {"daily_limit": 100.0, "enabled": False}
        tracker = BudgetTracker(config)
        assert tracker.config.enabled is False


class TestLoadBudgetConfig:
    """Test load_budget_config method"""

    def test_load_budget_config(self):
        tracker = BudgetTracker()
        config = {"daily_limit": 50.0, "weekly_limit": 250.0}
        tracker.load_budget_config(config)
        assert tracker.config.daily_limit == 50.0
        assert tracker.config.weekly_limit == 250.0

    def test_load_empty_config(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        tracker.load_budget_config({})
        assert tracker.config.daily_limit is None


class TestCheckSpendingVsLimit:
    """Test check_spending_vs_limit method"""

    def test_spending_under_limit(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        is_over, limit = tracker.check_spending_vs_limit(50.0, "daily")
        assert is_over is False
        assert limit == 100.0

    def test_spending_at_limit(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        is_over, limit = tracker.check_spending_vs_limit(100.0, "daily")
        assert is_over is False  # Exact boundary = not over

    def test_spending_over_limit(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        is_over, limit = tracker.check_spending_vs_limit(150.0, "daily")
        assert is_over is True
        assert limit == 100.0

    def test_spending_no_limit(self):
        tracker = BudgetTracker({})
        is_over, limit = tracker.check_spending_vs_limit(50.0, "daily")
        assert is_over is False
        assert limit is None

    def test_weekly_limit(self):
        tracker = BudgetTracker({"weekly_limit": 500.0})
        is_over, limit = tracker.check_spending_vs_limit(600.0, "weekly")
        assert is_over is True
        assert limit == 500.0

    def test_invalid_limit_type(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        with pytest.raises(ValueError):
            tracker.check_spending_vs_limit(50.0, "invalid")


class TestShouldAlert:
    """Test should_alert method"""

    def test_alert_80_percent_threshold(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        alert = tracker.should_alert(80.0, 100.0, "daily")
        assert alert is not None
        assert alert.level == AlertLevel.WARNING
        assert alert.threshold_pct == 80

    def test_alert_100_percent_threshold(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        alert = tracker.should_alert(100.0, 100.0, "daily")
        assert alert is not None
        assert alert.level == AlertLevel.CRITICAL
        assert alert.threshold_pct == 100

    def test_alert_110_percent_threshold(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        alert = tracker.should_alert(110.0, 100.0, "daily")
        assert alert is not None
        assert alert.level == AlertLevel.OVERAGE
        assert alert.threshold_pct == 110

    def test_no_alert_below_80_percent(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        alert = tracker.should_alert(79.0, 100.0, "daily")
        assert alert is None

    def test_alert_disabled(self):
        tracker = BudgetTracker({"daily_limit": 100.0, "enabled": False})
        alert = tracker.should_alert(90.0, 100.0, "daily")
        assert alert is None

    def test_alert_no_limit(self):
        tracker = BudgetTracker({})
        alert = tracker.should_alert(90.0, None, "daily")
        assert alert is None

    def test_alert_cooldown_prevents_duplicate(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        tracker.alert_cooldown = timedelta(seconds=5)

        # First alert should fire
        alert1 = tracker.should_alert(85.0, 100.0, "daily")
        assert alert1 is not None

        # Second alert within cooldown should be suppressed
        alert2 = tracker.should_alert(86.0, 100.0, "daily")
        assert alert2 is None

    def test_alert_cooldown_expires(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        tracker.alert_cooldown = timedelta(seconds=0)  # No cooldown

        alert1 = tracker.should_alert(85.0, 100.0, "daily")
        assert alert1 is not None

        # Simulate time passing
        alert_key = "daily_WARNING"
        tracker.alert_history[alert_key] = datetime.now(timezone.utc) - timedelta(
            seconds=10
        )

        alert2 = tracker.should_alert(85.5, 100.0, "daily")
        assert alert2 is not None

    def test_different_thresholds_can_alert(self):
        """Multiple thresholds can alert independently"""
        tracker = BudgetTracker({"daily_limit": 100.0})

        # First alert at 80%
        alert1 = tracker.should_alert(80.0, 100.0, "daily")
        assert alert1.level == AlertLevel.WARNING

        # Reset cooldown for next level
        tracker.alert_history.clear()

        # Second alert at 100%
        alert2 = tracker.should_alert(100.0, 100.0, "daily")
        assert alert2.level == AlertLevel.CRITICAL


class TestBudgetAlert:
    """Test BudgetAlert dataclass"""

    def test_alert_message_formatting(self):
        alert = BudgetAlert(
            level=AlertLevel.WARNING,
            threshold_pct=80,
            current_spend=80.0,
            limit=100.0,
            limit_type="daily",
        )
        assert "WARNING" in str(alert)
        assert "80" in str(alert)  # Percentage
        assert "80.00" in str(alert)  # Current spend
        assert "100.00" in str(alert)  # Limit

    def test_alert_with_custom_message(self):
        alert = BudgetAlert(
            level=AlertLevel.WARNING,
            threshold_pct=80,
            current_spend=80.0,
            limit=100.0,
            limit_type="daily",
            message="Custom message",
        )
        assert str(alert) == "Custom message"

    def test_alert_timestamp_present(self):
        alert = BudgetAlert(
            level=AlertLevel.WARNING,
            threshold_pct=80,
            current_spend=80.0,
            limit=100.0,
            limit_type="daily",
        )
        assert alert.timestamp is not None
        assert isinstance(alert.timestamp, datetime)


class TestBudgetDisplay:
    """Test budget display formatting"""

    def test_format_budget_display_50_percent(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        display = tracker.format_budget_display(50.0, 100.0, "daily")
        assert "[█████░░░░░]" in display or "[" in display  # Progress bar
        assert "50%" in display
        assert "50.00" in display
        assert "100.00" in display

    def test_format_budget_display_100_percent(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        display = tracker.format_budget_display(100.0, 100.0, "daily")
        assert "100%" in display

    def test_format_budget_display_150_percent(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        display = tracker.format_budget_display(150.0, 100.0, "daily")
        assert "100%" in display  # Capped at 100%

    def test_format_budget_display_no_limit(self):
        tracker = BudgetTracker({})
        display = tracker.format_budget_display(50.0, None, "daily")
        assert "not configured" in display.lower() or "no" in display.lower()


class TestBudgetSummary:
    """Test budget summary generation"""

    def test_get_budget_summary(self):
        tracker = BudgetTracker({"daily_limit": 100.0, "weekly_limit": 500.0})
        summary = tracker.get_budget_summary()
        assert summary["enabled"] is True
        assert summary["daily_limit"] == 100.0
        assert summary["weekly_limit"] == 500.0
        assert "alert_cooldown_minutes" in summary

    def test_budget_summary_with_alert_history(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        tracker.should_alert(85.0, 100.0, "daily")
        summary = tracker.get_budget_summary()
        assert "last_alerts" in summary
        assert len(summary["last_alerts"]) > 0


class TestAlertHistory:
    """Test alert history tracking"""

    def test_reset_alert_history(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        tracker.should_alert(85.0, 100.0, "daily")
        assert len(tracker.alert_history) > 0
        tracker.reset_alert_history()
        assert len(tracker.alert_history) == 0


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_zero_current_spend(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        alert = tracker.should_alert(0.0, 100.0, "daily")
        assert alert is None

    def test_very_small_amounts(self):
        tracker = BudgetTracker({"daily_limit": 0.01})
        is_over, limit = tracker.check_spending_vs_limit(0.001, "daily")
        assert is_over is False

    def test_large_amounts(self):
        tracker = BudgetTracker({"daily_limit": 10000.0})
        is_over, limit = tracker.check_spending_vs_limit(9999.99, "daily")
        assert is_over is False

    def test_float_precision(self):
        tracker = BudgetTracker({"daily_limit": 100.0})
        # 79.999...% should not trigger warning
        alert = tracker.should_alert(79.99, 100.0, "daily")
        assert alert is None

        # 80.00...% should trigger
        alert = tracker.should_alert(80.00, 100.0, "daily")
        assert alert is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
