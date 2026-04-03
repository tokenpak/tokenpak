"""Unit tests for tokenpak.forecast module.

Tests cover:
- BurnRateAnalysis dataclass construction
- get_burn_rate() with various datasets
- _calculate_wow_trend() trend detection
- format_burn_rate_display() output formatting
"""

import pytest
from datetime import date, timedelta
from unittest import mock
from typing import Dict, Any, List

from tokenpak.forecast import (
    BurnRateAnalysis,
    get_burn_rate,
    _calculate_wow_trend,
    format_burn_rate_display,
)
from tokenpak.agent.telemetry.budget import BudgetTracker


# ---------------------------------------------------------------------------
# Fixtures — Mock spend data
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_budget_tracker():
    """Mock BudgetTracker instance."""
    return mock.MagicMock(spec=BudgetTracker)


@pytest.fixture
def spend_records_7days() -> List[Dict[str, Any]]:
    """Spend records for 7 days (typical usage)."""
    today = date.today()
    records = []
    base_cost = 10.0
    
    for day_offset in range(7):
        current_date = today - timedelta(days=day_offset)
        date_str = current_date.isoformat()
        
        # 2-3 transactions per day
        records.append({
            "timestamp": f"{date_str}T08:00:00Z",
            "cost_usd": base_cost + (day_offset * 0.5),
            "model": "claude-3-sonnet",
            "agent": "OpenClaw agent tasks",
        })
        records.append({
            "timestamp": f"{date_str}T14:00:00Z",
            "cost_usd": base_cost * 0.5 + (day_offset * 0.2),
            "model": "gpt-4",
            "agent": "TokenPak CLI",
        })
        if day_offset % 2 == 0:
            records.append({
                "timestamp": f"{date_str}T20:00:00Z",
                "cost_usd": base_cost * 0.3,
                "model": "claude-3-haiku",
                "agent": "Cron jobs",
            })
    
    return records


@pytest.fixture
def spend_records_30days() -> List[Dict[str, Any]]:
    """Spend records for 30 days (monthly projection)."""
    today = date.today()
    records = []
    
    for day_offset in range(30):
        current_date = today - timedelta(days=day_offset)
        date_str = current_date.isoformat()
        
        daily_base = 10.0 + (day_offset // 7) * 2  # Trending up slightly
        records.append({
            "timestamp": f"{date_str}T10:00:00Z",
            "cost_usd": daily_base,
            "model": "claude-3-sonnet",
            "agent": "OpenClaw agent tasks",
        })
        records.append({
            "timestamp": f"{date_str}T15:00:00Z",
            "cost_usd": daily_base * 0.4,
            "model": "gpt-4",
            "agent": "other",
        })
    
    return records


@pytest.fixture
def empty_spend_records() -> List[Dict[str, Any]]:
    """Empty spend records (no transactions)."""
    return []


@pytest.fixture
def single_day_record() -> List[Dict[str, Any]]:
    """Single day of spending."""
    today = date.today()
    date_str = today.isoformat()
    return [
        {
            "timestamp": f"{date_str}T10:00:00Z",
            "cost_usd": 25.50,
            "model": "claude-3-opus",
            "agent": "OpenClaw agent tasks",
        }
    ]


# ---------------------------------------------------------------------------
# Test: BurnRateAnalysis dataclass
# ---------------------------------------------------------------------------


def test_burn_rate_analysis_construction() -> None:
    """Test BurnRateAnalysis dataclass creation with defaults."""
    analysis = BurnRateAnalysis(
        window_days=7,
        total_cost=100.0,
        daily_avg=14.29,
        weekly_avg=100.0,
        monthly_projection=429.0,
    )
    
    assert analysis.window_days == 7
    assert analysis.total_cost == 100.0
    assert analysis.daily_avg == 14.29
    assert analysis.weekly_avg == 100.0
    assert analysis.monthly_projection == 429.0
    assert analysis.by_model == {}
    assert analysis.by_activity == {}
    assert analysis.data_points == 0
    assert analysis.week_over_week_trend is None


def test_burn_rate_analysis_with_breakdown() -> None:
    """Test BurnRateAnalysis with model and activity breakdown."""
    analysis = BurnRateAnalysis(
        window_days=7,
        total_cost=100.0,
        daily_avg=14.29,
        weekly_avg=100.0,
        monthly_projection=429.0,
        week_over_week_trend=15.5,
        by_model={"claude-3-sonnet": 60.0, "gpt-4": 40.0},
        by_activity={"OpenClaw": 62.0, "TokenPak CLI": 16.0, "Cron": 22.0},
        data_points=21,
        start_date=date(2026, 3, 21),
        end_date=date(2026, 3, 27),
    )
    
    assert analysis.by_model["claude-3-sonnet"] == 60.0
    assert analysis.by_activity["OpenClaw"] == 62.0
    assert analysis.week_over_week_trend == 15.5
    assert analysis.data_points == 21


# ---------------------------------------------------------------------------
# Test 1: get_burn_rate with 7-day window
# ---------------------------------------------------------------------------


def test_get_burn_rate_7days(mock_budget_tracker, spend_records_7days) -> None:
    """Test 1: get_burn_rate with 7-day window calculates burn correctly."""
    mock_budget_tracker.list_spend.return_value = spend_records_7days
    
    analysis = get_burn_rate(mock_budget_tracker, window_days=7)
    
    assert analysis.window_days == 7
    assert analysis.data_points == len(spend_records_7days)
    assert analysis.total_cost > 0
    assert analysis.daily_avg > 0
    assert analysis.weekly_avg == analysis.daily_avg * 7
    assert analysis.monthly_projection == analysis.daily_avg * 30
    # Should have model breakdown
    assert "claude-3-sonnet" in analysis.by_model
    assert "gpt-4" in analysis.by_model
    # Should have activity breakdown
    assert "OpenClaw agent tasks" in analysis.by_activity or "Cron jobs" in analysis.by_activity


# ---------------------------------------------------------------------------
# Test 2: get_burn_rate with empty data (graceful degradation)
# ---------------------------------------------------------------------------


def test_get_burn_rate_empty_data(mock_budget_tracker, empty_spend_records) -> None:
    """Test 2: get_burn_rate handles empty spend records gracefully."""
    mock_budget_tracker.list_spend.return_value = empty_spend_records
    
    analysis = get_burn_rate(mock_budget_tracker, window_days=7)
    
    assert analysis.window_days == 7
    assert analysis.total_cost == 0.0
    assert analysis.daily_avg == 0.0
    assert analysis.weekly_avg == 0.0
    assert analysis.monthly_projection == 0.0
    assert analysis.data_points == 0
    assert analysis.by_model == {}
    assert analysis.by_activity == {}


# ---------------------------------------------------------------------------
# Test 3: get_burn_rate with single day (edge case)
# ---------------------------------------------------------------------------


def test_get_burn_rate_single_day(mock_budget_tracker, single_day_record) -> None:
    """Test 3: get_burn_rate with only one day of data."""
    mock_budget_tracker.list_spend.return_value = single_day_record
    
    analysis = get_burn_rate(mock_budget_tracker, window_days=1)
    
    assert analysis.window_days == 1
    assert analysis.total_cost == 25.50
    assert analysis.daily_avg == 25.50
    assert analysis.weekly_avg == 25.50 * 7
    assert analysis.monthly_projection == 25.50 * 30
    assert analysis.data_points == 1


# ---------------------------------------------------------------------------
# Test 4: get_burn_rate with 30-day window (monthly projection)
# ---------------------------------------------------------------------------


def test_get_burn_rate_30days(mock_budget_tracker, spend_records_30days) -> None:
    """Test 4: get_burn_rate with 30-day window for monthly projection."""
    mock_budget_tracker.list_spend.return_value = spend_records_30days
    
    analysis = get_burn_rate(mock_budget_tracker, window_days=30)
    
    assert analysis.window_days == 30
    assert analysis.data_points == len(spend_records_30days)
    assert analysis.total_cost > 0
    assert analysis.monthly_projection > 0
    # Monthly projection should be total_cost (30 days = 1 month)
    assert abs(analysis.monthly_projection - analysis.total_cost) < 0.01


# ---------------------------------------------------------------------------
# Test 5: _calculate_wow_trend detects positive trend
# ---------------------------------------------------------------------------


def test_calculate_wow_trend_positive(mock_budget_tracker) -> None:
    """Test 5: _calculate_wow_trend detects week-over-week positive trend."""
    today = date.today()
    week_1_start = today - timedelta(days=13)
    week_1_end = today - timedelta(days=7)
    week_2_start = today - timedelta(days=6)
    week_2_end = today
    
    # Current week: higher spend
    current_records = [
        {
            "timestamp": f"{week_2_start.isoformat()}T10:00:00Z",
            "cost_usd": 100.0,
            "model": "claude-3-sonnet",
            "agent": "task",
        },
        {
            "timestamp": f"{week_2_end.isoformat()}T15:00:00Z",
            "cost_usd": 150.0,
            "model": "gpt-4",
            "agent": "task",
        }
    ]
    
    # Previous week: lower spend
    prev_records = [
        {
            "timestamp": f"{week_1_start.isoformat()}T10:00:00Z",
            "cost_usd": 50.0,
            "model": "claude-3-sonnet",
            "agent": "task",
        },
        {
            "timestamp": f"{week_1_end.isoformat()}T15:00:00Z",
            "cost_usd": 50.0,
            "model": "gpt-4",
            "agent": "task",
        }
    ]
    
    all_records = current_records + prev_records
    mock_budget_tracker.list_spend.return_value = all_records
    
    trend = _calculate_wow_trend(mock_budget_tracker, current_window=7)
    
    # Current: 250, Previous: 100 → ((250-100)/100)*100 = 150%
    assert trend is not None
    assert trend > 0  # Positive trend


# ---------------------------------------------------------------------------
# Test 6: _calculate_wow_trend detects negative trend
# ---------------------------------------------------------------------------


def test_calculate_wow_trend_negative(mock_budget_tracker) -> None:
    """Test 6: _calculate_wow_trend detects week-over-week negative trend."""
    today = date.today()
    week_1_start = today - timedelta(days=13)
    week_1_end = today - timedelta(days=7)
    week_2_start = today - timedelta(days=6)
    week_2_end = today
    
    # Current week: lower spend
    current_records = [
        {
            "timestamp": f"{week_2_start.isoformat()}T10:00:00Z",
            "cost_usd": 50.0,
            "model": "claude-3-sonnet",
            "agent": "task",
        }
    ]
    
    # Previous week: higher spend
    prev_records = [
        {
            "timestamp": f"{week_1_start.isoformat()}T10:00:00Z",
            "cost_usd": 100.0,
            "model": "gpt-4",
            "agent": "task",
        },
        {
            "timestamp": f"{week_1_end.isoformat()}T15:00:00Z",
            "cost_usd": 100.0,
            "model": "gpt-4",
            "agent": "task",
        }
    ]
    
    all_records = current_records + prev_records
    mock_budget_tracker.list_spend.return_value = all_records
    
    trend = _calculate_wow_trend(mock_budget_tracker, current_window=7)
    
    # Current: 50, Previous: 200 → ((50-200)/200)*100 = -75%
    assert trend is not None
    assert trend < 0  # Negative trend


# ---------------------------------------------------------------------------
# Test 7: _calculate_wow_trend returns None for window < 7
# ---------------------------------------------------------------------------


def test_calculate_wow_trend_insufficient_window(mock_budget_tracker, single_day_record) -> None:
    """Test 7: _calculate_wow_trend returns None for window < 7 days."""
    mock_budget_tracker.list_spend.return_value = single_day_record
    
    trend = _calculate_wow_trend(mock_budget_tracker, current_window=3)
    
    assert trend is None


# ---------------------------------------------------------------------------
# Test 8: format_burn_rate_display with data
# ---------------------------------------------------------------------------


def test_format_burn_rate_display_with_data() -> None:
    """Test 8: format_burn_rate_display formats analysis nicely."""
    analysis = BurnRateAnalysis(
        window_days=7,
        total_cost=100.0,
        daily_avg=14.29,
        weekly_avg=100.0,
        monthly_projection=429.0,
        week_over_week_trend=12.5,
        by_model={"claude-3-sonnet": 60.0, "gpt-4": 40.0},
        by_activity={"OpenClaw": 62.0, "TokenPak CLI": 38.0},
        data_points=14,
    )
    
    output = format_burn_rate_display(analysis)
    
    assert "Burn Rate Analysis" in output
    assert "Daily average" in output
    assert "14.29" in output
    assert "Weekly average" in output
    assert "100.00" in output
    assert "Monthly projection" in output
    assert "429.00" in output
    assert "Growth trend" in output
    assert "12.5" in output
    assert "claude-3-sonnet" in output
    assert "OpenClaw" in output


# ---------------------------------------------------------------------------
# Test 9: format_burn_rate_display with budget threshold
# ---------------------------------------------------------------------------


def test_format_burn_rate_display_over_threshold() -> None:
    """Test 9: format_burn_rate_display alerts when over budget threshold."""
    analysis = BurnRateAnalysis(
        window_days=7,
        total_cost=100.0,
        daily_avg=14.29,
        weekly_avg=100.0,
        monthly_projection=429.0,
        data_points=14,
    )
    
    threshold = 400.0  # Budget is $400
    output = format_burn_rate_display(analysis, threshold=threshold)
    
    assert "⚠️  Over budget" in output
    assert "429.00" in output


# ---------------------------------------------------------------------------
# Test 10: format_burn_rate_display with empty data
# ---------------------------------------------------------------------------


def test_format_burn_rate_display_empty_data() -> None:
    """Test 10: format_burn_rate_display gracefully handles empty data."""
    analysis = BurnRateAnalysis(
        window_days=7,
        total_cost=0.0,
        daily_avg=0.0,
        weekly_avg=0.0,
        monthly_projection=0.0,
        data_points=0,
    )
    
    output = format_burn_rate_display(analysis)
    
    assert "No spend data available" in output
    assert "< 1 day history" in output


# ---------------------------------------------------------------------------
# Test 11: Model breakdown calculation
# ---------------------------------------------------------------------------


def test_get_burn_rate_model_breakdown(mock_budget_tracker) -> None:
    """Test 11: get_burn_rate correctly breaks down costs by model."""
    today = date.today()
    date_str = today.isoformat()
    
    records = [
        {
            "timestamp": f"{date_str}T08:00:00Z",
            "cost_usd": 20.0,
            "model": "claude-3-sonnet",
            "agent": "agent-1",
        },
        {
            "timestamp": f"{date_str}T09:00:00Z",
            "cost_usd": 30.0,
            "model": "gpt-4",
            "agent": "agent-1",
        },
        {
            "timestamp": f"{date_str}T10:00:00Z",
            "cost_usd": 10.0,
            "model": "claude-3-haiku",
            "agent": "agent-2",
        },
    ]
    
    mock_budget_tracker.list_spend.return_value = records
    analysis = get_burn_rate(mock_budget_tracker, window_days=1)
    
    assert analysis.by_model["claude-3-sonnet"] == 20.0
    assert analysis.by_model["gpt-4"] == 30.0
    assert analysis.by_model["claude-3-haiku"] == 10.0
    assert analysis.total_cost == 60.0


# ---------------------------------------------------------------------------
# Test 12: Activity breakdown calculation
# ---------------------------------------------------------------------------


def test_get_burn_rate_activity_breakdown(mock_budget_tracker) -> None:
    """Test 12: get_burn_rate correctly breaks down costs by activity."""
    today = date.today()
    date_str = today.isoformat()
    
    records = [
        {
            "timestamp": f"{date_str}T08:00:00Z",
            "cost_usd": 50.0,
            "model": "claude-3-sonnet",
            "agent": "OpenClaw agent tasks",
        },
        {
            "timestamp": f"{date_str}T09:00:00Z",
            "cost_usd": 30.0,
            "model": "gpt-4",
            "agent": "TokenPak CLI",
        },
        {
            "timestamp": f"{date_str}T10:00:00Z",
            "cost_usd": 20.0,
            "model": "claude-3-haiku",
            "agent": "Cron jobs",
        },
    ]
    
    mock_budget_tracker.list_spend.return_value = records
    analysis = get_burn_rate(mock_budget_tracker, window_days=1)
    
    assert analysis.by_activity["OpenClaw agent tasks"] == 50.0
    assert analysis.by_activity["TokenPak CLI"] == 30.0
    assert analysis.by_activity["Cron jobs"] == 20.0


# ---------------------------------------------------------------------------
# Test 13: Empty agent field defaults to "other"
# ---------------------------------------------------------------------------


def test_get_burn_rate_empty_agent_field(mock_budget_tracker) -> None:
    """Test 13: Empty agent field is handled as 'other'."""
    today = date.today()
    date_str = today.isoformat()
    
    records = [
        {
            "timestamp": f"{date_str}T08:00:00Z",
            "cost_usd": 15.0,
            "model": "claude-3-sonnet",
            "agent": "",  # Empty agent
        },
        {
            "timestamp": f"{date_str}T09:00:00Z",
            "cost_usd": 15.0,
            "model": "gpt-4",
            "agent": None,  # None agent
        },
    ]
    
    mock_budget_tracker.list_spend.return_value = records
    analysis = get_burn_rate(mock_budget_tracker, window_days=1)
    
    assert "other" in analysis.by_activity
    assert analysis.by_activity["other"] == 30.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
