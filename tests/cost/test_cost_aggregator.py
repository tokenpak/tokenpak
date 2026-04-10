"""Tests for tokenpak.cost.cost_aggregator"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import pytest

from tokenpak.telemetry.cost_tracker import CostTracker
from tokenpak.cost.cost_aggregator import (
    BurnRateAlarm,
    CostAggregator,
    DailySummary,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tracker():
    """In-memory CostTracker for tests."""
    return CostTracker(db_path=":memory:")


@pytest.fixture()
def agg(tracker):
    return CostAggregator(tracker=tracker)


def _day(offset: int = 0) -> str:
    """Return ISO date string for today + offset days."""
    return (date.today() + timedelta(days=offset)).isoformat()


def _populate(tracker: CostTracker, entries: list[tuple]) -> None:
    """Insert (day_offset, model, prompt_tokens, completion_tokens) entries."""
    for day_offset, model, pt, ct in entries:
        day = _day(day_offset)
        ts = f"{day}T12:00:00"
        tracker.record_request(model, pt, ct, timestamp=ts)


# ---------------------------------------------------------------------------
# DailySummary tests
# ---------------------------------------------------------------------------


class TestDailySummaries:
    def test_empty_returns_empty_list(self, agg):
        assert agg.daily_summaries(days=7) == []

    def test_single_day_single_model(self, agg, tracker):
        tracker.record_request("claude-haiku-3-5", 1000, 200, timestamp=f"{_day()}T10:00:00")
        summaries = agg.daily_summaries(days=1)
        assert len(summaries) == 1
        s = summaries[0]
        assert isinstance(s, DailySummary)
        assert s.day == _day()
        assert s.total_requests == 1
        assert s.total_tokens == 1200
        assert s.total_cost_usd > 0

    def test_multiple_models_same_day(self, agg, tracker):
        for _ in range(3):
            tracker.record_request("gpt-4o", 500, 100, timestamp=f"{_day()}T10:00:00")
        for _ in range(2):
            tracker.record_request("claude-haiku-3-5", 300, 50, timestamp=f"{_day()}T11:00:00")
        summaries = agg.daily_summaries(days=1)
        assert len(summaries) == 1
        s = summaries[0]
        assert s.total_requests == 5
        assert len(s.by_model) == 2

    def test_multi_day_range(self, agg, tracker):
        _populate(tracker, [
            (-2, "gpt-4o", 1000, 200),
            (-1, "gpt-4o", 800, 150),
            (0,  "gpt-4o", 600, 100),
        ])
        summaries = agg.daily_summaries(days=3)
        assert len(summaries) == 3
        days = [s.day for s in summaries]
        assert days == sorted(days)  # ordered oldest → newest

    def test_days_outside_range_excluded(self, agg, tracker):
        _populate(tracker, [
            (-10, "gpt-4o", 1000, 200),
            (-1,  "gpt-4o", 500, 100),
        ])
        summaries = agg.daily_summaries(days=3)
        # Only the -1 day should be in 3-day window
        assert len(summaries) == 1

    def test_returns_sorted_oldest_first(self, agg, tracker):
        _populate(tracker, [
            (0,  "gpt-4o", 100, 20),
            (-3, "gpt-4o", 200, 40),
            (-6, "gpt-4o", 300, 60),
        ])
        summaries = agg.daily_summaries(days=7)
        assert len(summaries) == 3
        assert summaries[0].day < summaries[1].day < summaries[2].day

    def test_by_model_sorted_by_cost_desc(self, agg, tracker):
        # gpt-4o is more expensive than haiku
        tracker.record_request("gpt-4o", 10000, 2000, timestamp=f"{_day()}T10:00:00")
        tracker.record_request("claude-haiku-3-5", 100, 20, timestamp=f"{_day()}T11:00:00")
        s = agg.daily_summaries(days=1)[0]
        assert s.by_model[0]["model"] == "gpt-4o"

    def test_cost_usd_non_negative(self, agg, tracker):
        tracker.record_request("claude-haiku-3-5", 500, 100, timestamp=f"{_day()}T10:00:00")
        s = agg.daily_summaries(days=1)[0]
        assert s.total_cost_usd >= 0

    def test_tokens_sum_correctly(self, agg, tracker):
        tracker.record_request("gpt-4o", 300, 100, timestamp=f"{_day()}T10:00:00")
        tracker.record_request("gpt-4o", 200, 50, timestamp=f"{_day()}T11:00:00")
        s = agg.daily_summaries(days=1)[0]
        assert s.total_tokens == 650


# ---------------------------------------------------------------------------
# Aggregate tests
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_empty_returns_zeros(self, agg):
        result = agg.aggregate(days=7)
        assert result["total_cost_usd"] == 0.0
        assert result["total_requests"] == 0
        assert result["total_tokens"] == 0
        assert result["avg_daily_cost_usd"] == 0.0
        assert result["by_model"] == []

    def test_single_request(self, agg, tracker):
        tracker.record_request("gpt-4o", 1000, 200, timestamp=f"{_day()}T10:00:00")
        result = agg.aggregate(days=1)
        assert result["total_requests"] == 1
        assert result["total_cost_usd"] > 0
        assert len(result["by_model"]) == 1

    def test_multi_day_totals(self, agg, tracker):
        _populate(tracker, [(-2, "gpt-4o", 1000, 200), (-1, "gpt-4o", 1000, 200), (0, "gpt-4o", 1000, 200)])
        result = agg.aggregate(days=3)
        assert result["total_requests"] == 3
        assert result["days"] == 3

    def test_by_model_aggregated_across_days(self, agg, tracker):
        _populate(tracker, [
            (-1, "gpt-4o", 1000, 200),
            (0,  "gpt-4o", 1000, 200),
            (0,  "claude-haiku-3-5", 500, 100),
        ])
        result = agg.aggregate(days=2)
        models = {m["model"] for m in result["by_model"]}
        assert "gpt-4o" in models
        assert "claude-haiku-3-5" in models
        gpt = next(m for m in result["by_model"] if m["model"] == "gpt-4o")
        assert gpt["requests"] == 2

    def test_avg_daily_cost_computed(self, agg, tracker):
        _populate(tracker, [(-1, "gpt-4o", 1000, 200), (0, "gpt-4o", 1000, 200)])
        result = agg.aggregate(days=2)
        assert result["avg_daily_cost_usd"] > 0
        assert abs(result["avg_daily_cost_usd"] - result["total_cost_usd"] / 2) < 1e-9


# ---------------------------------------------------------------------------
# CSV export tests
# ---------------------------------------------------------------------------


class TestExportCsv:
    def test_empty_returns_header_only(self, agg):
        csv_text = agg.export_csv(days=7)
        rows = list(csv.reader(io.StringIO(csv_text)))
        assert len(rows) == 1
        assert rows[0] == ["date", "model", "requests", "total_tokens", "cost_usd"]

    def test_by_model_false_returns_daily_header(self, agg):
        csv_text = agg.export_csv(days=7, by_model=False)
        rows = list(csv.reader(io.StringIO(csv_text)))
        assert rows[0] == ["date", "requests", "total_tokens", "cost_usd"]

    def test_csv_has_data_rows(self, agg, tracker):
        tracker.record_request("gpt-4o", 500, 100, timestamp=f"{_day()}T10:00:00")
        csv_text = agg.export_csv(days=1)
        rows = list(csv.reader(io.StringIO(csv_text)))
        assert len(rows) == 2  # header + 1 data row
        assert rows[1][0] == _day()
        assert rows[1][1] == "gpt-4o"

    def test_csv_by_model_false(self, agg, tracker):
        tracker.record_request("gpt-4o", 500, 100, timestamp=f"{_day()}T10:00:00")
        tracker.record_request("claude-haiku-3-5", 200, 50, timestamp=f"{_day()}T11:00:00")
        csv_text = agg.export_csv(days=1, by_model=False)
        rows = list(csv.reader(io.StringIO(csv_text)))
        assert len(rows) == 2  # header + 1 day row (combined)

    def test_csv_cost_usd_is_numeric(self, agg, tracker):
        tracker.record_request("gpt-4o", 1000, 200, timestamp=f"{_day()}T10:00:00")
        csv_text = agg.export_csv(days=1)
        rows = list(csv.reader(io.StringIO(csv_text)))
        cost = float(rows[1][4])
        assert cost > 0

    def test_csv_multiple_days(self, agg, tracker):
        _populate(tracker, [
            (-2, "gpt-4o", 100, 20),
            (-1, "gpt-4o", 100, 20),
            (0,  "gpt-4o", 100, 20),
        ])
        csv_text = agg.export_csv(days=3)
        rows = list(csv.reader(io.StringIO(csv_text)))
        assert len(rows) == 4  # header + 3 days


# ---------------------------------------------------------------------------
# Burn-rate alarm tests
# ---------------------------------------------------------------------------


class TestBurnRateAlarm:
    def test_no_data_no_alarms(self, agg):
        alarms = agg.check_burn_rate(monthly_budget_usd=100.0)
        assert alarms == []

    def test_no_alarm_below_threshold(self, agg, tracker):
        # $0.001 daily << 20% of $100
        tracker.record_request("claude-haiku-3-5", 100, 20, timestamp=f"{_day()}T10:00:00")
        alarms = agg.check_burn_rate(monthly_budget_usd=100.0, threshold_pct=20.0)
        assert alarms == []

    def test_alarm_triggered_above_threshold(self, agg, tracker):
        # gpt-4o at 100k/20k tokens costs ~$0.45 — that's >20% of $1 monthly budget
        tracker.record_request("gpt-4o", 100_000, 20_000, timestamp=f"{_day()}T10:00:00")
        alarms = agg.check_burn_rate(monthly_budget_usd=1.0, threshold_pct=20.0)
        assert len(alarms) == 1
        assert isinstance(alarms[0], BurnRateAlarm)

    def test_alarm_contains_correct_fields(self, agg, tracker):
        tracker.record_request("gpt-4o", 100_000, 20_000, timestamp=f"{_day()}T10:00:00")
        alarms = agg.check_burn_rate(monthly_budget_usd=1.0, threshold_pct=20.0)
        a = alarms[0]
        assert a.day == _day()
        assert a.monthly_budget_usd == 1.0
        assert a.threshold_pct == 20.0
        assert a.actual_pct > 20.0
        assert "⚠️" in a.message

    def test_zero_budget_returns_empty(self, agg, tracker):
        tracker.record_request("gpt-4o", 1000, 200, timestamp=f"{_day()}T10:00:00")
        alarms = agg.check_burn_rate(monthly_budget_usd=0)
        assert alarms == []

    def test_negative_budget_returns_empty(self, agg, tracker):
        tracker.record_request("gpt-4o", 1000, 200, timestamp=f"{_day()}T10:00:00")
        alarms = agg.check_burn_rate(monthly_budget_usd=-50.0)
        assert alarms == []

    def test_multiple_alarm_days(self, agg, tracker):
        _populate(tracker, [
            (-2, "gpt-4o", 100_000, 20_000),
            (-1, "gpt-4o", 100_000, 20_000),
            (0,  "gpt-4o", 100_000, 20_000),
        ])
        alarms = agg.check_burn_rate(monthly_budget_usd=1.0, threshold_pct=20.0, days=3)
        assert len(alarms) == 3

    def test_custom_threshold(self, agg, tracker):
        # Low threshold (1%) — even small spend should trigger
        tracker.record_request("gpt-4o", 1000, 200, timestamp=f"{_day()}T10:00:00")
        alarms_tight = agg.check_burn_rate(monthly_budget_usd=1.0, threshold_pct=0.001)
        alarms_loose = agg.check_burn_rate(monthly_budget_usd=1000.0, threshold_pct=20.0)
        assert len(alarms_tight) >= 1
        assert len(alarms_loose) == 0


# ---------------------------------------------------------------------------
# BurnRateAlarm message tests
# ---------------------------------------------------------------------------


class TestBurnRateAlarmMessage:
    def test_message_contains_day(self):
        a = BurnRateAlarm("2026-03-01", 25.0, 100.0, 20.0, 25.0)
        assert "2026-03-01" in a.message

    def test_message_contains_cost(self):
        a = BurnRateAlarm("2026-03-01", 25.0, 100.0, 20.0, 25.0)
        assert "25.0" in a.message or "$25" in a.message

    def test_repr(self):
        a = BurnRateAlarm("2026-03-01", 25.0, 100.0, 20.0, 25.0)
        assert "BurnRateAlarm" in repr(a)
