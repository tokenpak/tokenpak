"""Unit tests for TokenPak cost forecasting module."""


import pytest

pytest.importorskip("tokenpak.forecast", reason="module not available in current build")
from datetime import date, datetime, timedelta

import pytest

from tokenpak.forecast import (
    BurnRateAnalysis,
    _calculate_wow_trend,
    format_burn_rate_display,
    get_burn_rate,
)
from tokenpak.telemetry.budget import BudgetTracker


class TestBurnRateCalculation:
    """Test burn rate calculations."""

    def test_empty_data(self):
        """Test with no spend records."""
        tracker = BudgetTracker(db_path=":memory:")
        analysis = get_burn_rate(tracker, window_days=7)

        assert analysis.window_days == 7
        assert analysis.total_cost == 0.0
        assert analysis.daily_avg == 0.0
        assert analysis.weekly_avg == 0.0
        assert analysis.monthly_projection == 0.0
        assert analysis.data_points == 0

    def test_single_day_spend(self):
        """Test with a single day of spend."""
        tracker = BudgetTracker(db_path=":memory:")
        today = date.today()
        ts = datetime.combine(today, datetime.min.time())

        # Record $10.00 spend
        tracker.record_spend(10.0, request_id="req-001", model="claude-sonnet", timestamp=ts)

        analysis = get_burn_rate(tracker, window_days=7)

        assert analysis.data_points == 1
        assert analysis.total_cost == 10.0
        assert analysis.daily_avg == 10.0 / 7  # Divided across 7 days
        assert analysis.weekly_avg == pytest.approx(10.0, rel=0.01)
        assert analysis.monthly_projection == pytest.approx((10.0 / 7) * 30, rel=0.01)

    def test_multiple_days_spend(self):
        """Test with multiple days of spend."""
        tracker = BudgetTracker(db_path=":memory:")
        today = date.today()

        # Record spend over 7 days
        for i in range(7):
            ts = datetime.combine(today - timedelta(days=6-i), datetime.min.time())
            tracker.record_spend(
                5.0,
                request_id=f"req-{i:03d}",
                model="claude-sonnet",
                timestamp=ts
            )

        analysis = get_burn_rate(tracker, window_days=7)

        assert analysis.data_points == 7
        assert analysis.total_cost == 35.0
        assert analysis.daily_avg == 5.0
        assert analysis.weekly_avg == 35.0
        assert analysis.monthly_projection == pytest.approx(150.0, rel=0.01)

    def test_model_breakdown(self):
        """Test cost breakdown by model."""
        tracker = BudgetTracker(db_path=":memory:")
        today = date.today()
        ts = datetime.combine(today, datetime.min.time())

        tracker.record_spend(6.0, model="claude-sonnet", timestamp=ts)
        tracker.record_spend(2.0, model="claude-opus", timestamp=ts)
        tracker.record_spend(2.0, model="claude-haiku", timestamp=ts)

        analysis = get_burn_rate(tracker, window_days=7)

        assert "claude-sonnet" in analysis.by_model
        assert "claude-opus" in analysis.by_model
        assert "claude-haiku" in analysis.by_model
        assert analysis.by_model["claude-sonnet"] == 6.0
        assert analysis.by_model["claude-opus"] == 2.0
        assert analysis.by_model["claude-haiku"] == 2.0


class TestTrendCalculation:
    """Test week-over-week trend detection."""

    def test_wow_trend_insufficient_data(self):
        """Test WoW with less than 7 days."""
        tracker = BudgetTracker(db_path=":memory:")
        trend = _calculate_wow_trend(tracker, current_window=3)
        assert trend is None

    def test_wow_trend_positive_growth(self):
        """Test detecting positive trend."""
        tracker = BudgetTracker(db_path=":memory:")
        today = date.today()

        # Previous 7 days: $10/day = $70 total
        for i in range(7):
            ts = datetime.combine(today - timedelta(days=13-i), datetime.min.time())
            tracker.record_spend(10.0, request_id=f"prev-{i}", timestamp=ts)

        # Current 7 days: $12/day = $84 total (20% growth)
        for i in range(7):
            ts = datetime.combine(today - timedelta(days=6-i), datetime.min.time())
            tracker.record_spend(12.0, request_id=f"curr-{i}", timestamp=ts)

        trend = _calculate_wow_trend(tracker, current_window=7)
        assert trend is not None
        assert pytest.approx(trend, rel=0.05) == 20.0  # ~20% growth

    def test_wow_trend_negative_growth(self):
        """Test detecting negative trend."""
        tracker = BudgetTracker(db_path=":memory:")
        today = date.today()

        # Previous 7 days: $20/day = $140 total
        for i in range(7):
            ts = datetime.combine(today - timedelta(days=13-i), datetime.min.time())
            tracker.record_spend(20.0, request_id=f"prev-{i}", timestamp=ts)

        # Current 7 days: $15/day = $105 total (25% decrease)
        for i in range(7):
            ts = datetime.combine(today - timedelta(days=6-i), datetime.min.time())
            tracker.record_spend(15.0, request_id=f"curr-{i}", timestamp=ts)

        trend = _calculate_wow_trend(tracker, current_window=7)
        assert trend is not None
        assert pytest.approx(trend, rel=0.05) == -25.0  # ~-25% growth


class TestBurnRateDisplay:
    """Test formatting of burn rate display."""

    def test_empty_display(self):
        """Test display with no data."""
        analysis = BurnRateAnalysis(
            window_days=7,
            total_cost=0.0,
            daily_avg=0.0,
            weekly_avg=0.0,
            monthly_projection=0.0,
            data_points=0,
        )

        display = format_burn_rate_display(analysis)
        assert "No spend data" in display

    def test_normal_display(self):
        """Test normal display with data."""
        analysis = BurnRateAnalysis(
            window_days=7,
            total_cost=35.0,
            daily_avg=5.0,
            weekly_avg=35.0,
            monthly_projection=150.0,
            by_model={"claude-sonnet": 25.0, "claude-opus": 10.0},
            by_activity={
                "OpenClaw agent tasks": 21.7,
                "TokenPak CLI": 5.6,
                "Cron jobs": 3.15,
                "Other": 4.55,
            },
            data_points=7,
            start_date=date.today() - timedelta(days=6),
            end_date=date.today(),
        )

        display = format_burn_rate_display(analysis)
        assert "5.00" in display and "/day" in display
        assert "35.00" in display and "/week" in display
        assert "150.00" in display
        assert "claude-sonnet" in display
        assert "OpenClaw agent tasks" in display

    def test_display_with_threshold_alert(self):
        """Test display with threshold alert."""
        analysis = BurnRateAnalysis(
            window_days=7,
            total_cost=35.0,
            daily_avg=5.0,
            weekly_avg=35.0,
            monthly_projection=150.0,
            data_points=7,
        )

        display = format_burn_rate_display(analysis, threshold=100.0)
        assert "Over budget" in display
        assert "+$50.00" in display

    def test_display_within_threshold(self):
        """Test display when within threshold."""
        analysis = BurnRateAnalysis(
            window_days=7,
            total_cost=14.0,
            daily_avg=2.0,
            weekly_avg=14.0,
            monthly_projection=60.0,
            data_points=7,
        )

        display = format_burn_rate_display(analysis, threshold=100.0)
        assert "Over budget" not in display


class TestDifferentWindowSizes:
    """Test forecast with different window sizes."""

    def test_30_day_window(self):
        """Test 30-day burn rate analysis."""
        tracker = BudgetTracker(db_path=":memory:")
        today = date.today()

        # Record $2/day for 30 days
        for i in range(30):
            ts = datetime.combine(today - timedelta(days=29-i), datetime.min.time())
            tracker.record_spend(2.0, request_id=f"req-{i:03d}", timestamp=ts)

        analysis = get_burn_rate(tracker, window_days=30)

        assert analysis.window_days == 30
        assert analysis.total_cost == 60.0
        assert analysis.daily_avg == 2.0
        assert analysis.monthly_projection == pytest.approx(60.0, rel=0.01)

    def test_90_day_window(self):
        """Test 90-day burn rate analysis."""
        tracker = BudgetTracker(db_path=":memory:")
        today = date.today()

        # Record $1/day for 90 days
        for i in range(90):
            ts = datetime.combine(today - timedelta(days=89-i), datetime.min.time())
            tracker.record_spend(1.0, request_id=f"req-{i:03d}", timestamp=ts)

        analysis = get_burn_rate(tracker, window_days=90)

        assert analysis.window_days == 90
        assert analysis.total_cost == 90.0
        assert analysis.daily_avg == 1.0
        assert analysis.monthly_projection == pytest.approx(30.0, rel=0.01)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
