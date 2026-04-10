"""Tests for the BudgetTracker — local cost tracking module."""

import pytest
from datetime import datetime, date, timedelta

from tokenpak.telemetry.budget import (
    BudgetConfig,
    BudgetTracker,
    SpendRecord,
    BudgetStatus,
)


@pytest.fixture
def tracker():
    cfg = BudgetConfig(daily_limit_usd=10.0, monthly_limit_usd=100.0, alert_at_percent=80.0)
    return BudgetTracker(config=cfg, db_path=":memory:")


# ---------------------------------------------------------------------------
# record_spend
# ---------------------------------------------------------------------------

def test_record_spend_returns_record(tracker):
    rec = tracker.record_spend(0.05, request_id="r1", model="claude-sonnet")
    assert isinstance(rec, SpendRecord)
    assert rec.cost_usd == 0.05
    assert rec.model == "claude-sonnet"


def test_record_spend_auto_request_id(tracker):
    rec = tracker.record_spend(0.01)
    assert rec.request_id.startswith("req-")


def test_record_spend_accumulates(tracker):
    tracker.record_spend(1.00)
    tracker.record_spend(2.50)
    assert abs(tracker.total_spent("daily") - 3.50) < 0.0001


# ---------------------------------------------------------------------------
# total_spent periods
# ---------------------------------------------------------------------------

def test_total_spent_empty(tracker):
    assert tracker.total_spent("daily") == 0.0
    assert tracker.total_spent("monthly") == 0.0
    assert tracker.total_spent("weekly") == 0.0


def test_total_spent_all(tracker):
    tracker.record_spend(5.0)
    tracker.record_spend(3.0)
    assert tracker.total_spent("all") == 8.0


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

def test_get_status_daily_under_limit(tracker):
    tracker.record_spend(5.0)
    status = tracker.get_status("daily")
    assert status is not None
    assert status.period == "daily"
    assert abs(status.spent_usd - 5.0) < 0.001
    assert abs(status.remaining_usd - 5.0) < 0.001
    assert status.percent_used == pytest.approx(50.0, abs=0.1)
    assert status.alert_triggered is False


def test_get_status_daily_alert_triggered(tracker):
    tracker.record_spend(9.0)
    status = tracker.get_status("daily")
    assert status.alert_triggered is True
    assert status.percent_used == pytest.approx(90.0, abs=0.1)


def test_get_status_no_limit():
    cfg = BudgetConfig()  # no limits
    t = BudgetTracker(config=cfg, db_path=":memory:")
    t.record_spend(5.0)
    assert t.get_status("daily") is None
    assert t.get_status("monthly") is None


def test_get_status_monthly(tracker):
    tracker.record_spend(50.0)
    status = tracker.get_status("monthly")
    assert status is not None
    assert abs(status.spent_usd - 50.0) < 0.001


# ---------------------------------------------------------------------------
# is_budget_exceeded
# ---------------------------------------------------------------------------

def test_budget_not_exceeded(tracker):
    tracker.record_spend(5.0)
    assert tracker.is_budget_exceeded() is False


def test_budget_exceeded_daily(tracker):
    tracker.record_spend(10.01)
    assert tracker.is_budget_exceeded() is True


def test_budget_exceeded_monthly(tracker):
    tracker.record_spend(100.01)
    assert tracker.is_budget_exceeded() is True


# ---------------------------------------------------------------------------
# list_spend
# ---------------------------------------------------------------------------

def test_list_spend_basic(tracker):
    tracker.record_spend(1.0, request_id="a", model="m1")
    tracker.record_spend(2.0, request_id="b", model="m2")
    rows = tracker.list_spend(limit=10)
    assert len(rows) == 2
    assert rows[0]["request_id"] == "b"  # most recent first


def test_list_spend_model_filter(tracker):
    tracker.record_spend(1.0, request_id="a", model="gpt4")
    tracker.record_spend(2.0, request_id="b", model="claude")
    rows = tracker.list_spend(model="gpt4")
    assert len(rows) == 1
    assert rows[0]["model"] == "gpt4"


# ---------------------------------------------------------------------------
# by_model_summary
# ---------------------------------------------------------------------------

def test_by_model_summary(tracker):
    tracker.record_spend(1.0, model="m1", tokens_input=100, tokens_output=50)
    tracker.record_spend(2.0, model="m1", tokens_input=200, tokens_output=100)
    tracker.record_spend(3.0, model="m2", tokens_input=300)
    summary = tracker.by_model_summary()
    assert len(summary) == 2
    m1 = next(r for r in summary if r["model"] == "m1")
    assert m1["requests"] == 2
    assert abs(m1["cost_usd"] - 3.0) < 0.001


# ---------------------------------------------------------------------------
# export_csv
# ---------------------------------------------------------------------------

def test_export_csv_empty(tracker):
    csv = tracker.export_csv()
    assert "request_id" in csv
    assert "cost_usd" in csv


def test_export_csv_with_data(tracker):
    tracker.record_spend(1.23, request_id="csv-1", model="test")
    csv = tracker.export_csv()
    assert "csv-1" in csv
    assert "1.23" in csv


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

def test_prune_removes_old(tracker):
    # Insert a record with an old timestamp directly
    conn = tracker._conn()
    conn.execute(
        "INSERT INTO tp_spend (request_id, timestamp, model, cost_usd, tokens_input, tokens_output, agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("old-req", "2020-01-01T00:00:00", "m", 1.0, 0, 0, ""),
    )
    conn.commit()
    tracker.record_spend(1.0, request_id="new-req")
    deleted = tracker.prune(days=30)
    assert deleted >= 1
    rows = tracker.list_spend(limit=100)
    ids = [r["request_id"] for r in rows]
    assert "old-req" not in ids
    assert "new-req" in ids


# ---------------------------------------------------------------------------
# BudgetConfig serde
# ---------------------------------------------------------------------------

def test_config_round_trip():
    cfg = BudgetConfig(daily_limit_usd=5.0, monthly_limit_usd=50.0, alert_at_percent=75.0, hard_stop=True)
    d = cfg.to_dict()
    cfg2 = BudgetConfig.from_dict(d)
    assert cfg2.daily_limit_usd == 5.0
    assert cfg2.monthly_limit_usd == 50.0
    assert cfg2.alert_at_percent == 75.0
    assert cfg2.hard_stop is True


# ---------------------------------------------------------------------------
# BudgetStatus.to_dict
# ---------------------------------------------------------------------------

def test_budget_status_to_dict(tracker):
    tracker.record_spend(3.0)
    status = tracker.get_status("daily")
    d = status.to_dict()
    assert d["period"] == "daily"
    assert "spent_usd" in d
    assert "percent_used" in d
    assert "alert_triggered" in d
