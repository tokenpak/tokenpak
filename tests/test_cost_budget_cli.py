"""Tests for tokenpak cost and budget CLI commands.

Tests for:
- tokenpak.agent.cli.commands.cost
- tokenpak.agent.cli.commands.budget
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def monitor_db(tmp_path):
    """Create a temp monitor DB with test data."""
    db_path = str(tmp_path / "monitor.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms INTEGER,
            status_code INTEGER,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER,
            compressed_tokens INTEGER,
            injected_tokens INTEGER DEFAULT 0,
            injected_sources TEXT DEFAULT '',
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0
        )
    """)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    last_week = (date.today() - timedelta(days=5)).isoformat()

    rows = [
        # Today
        (f"{today}T10:00:00", "claude-sonnet-4-6", 1000, 200, 0.0034, "https://api.anthropic.com/v1/messages"),
        (f"{today}T11:00:00", "claude-haiku-4-5",  500,  100, 0.0002, "https://api.anthropic.com/v1/messages"),
        (f"{today}T12:00:00", "claude-sonnet-4-6", 2000, 400, 0.0068, "https://api.anthropic.com/v1/messages"),
        # Yesterday
        (f"{yesterday}T09:00:00", "claude-opus-4-6", 3000, 600, 0.06, "https://api.anthropic.com/v1/messages"),
        (f"{yesterday}T14:00:00", "gpt-4o",           1000, 200, 0.003, "https://openai.com/v1/chat"),
        # Last week
        (f"{last_week}T10:00:00", "claude-sonnet-4-6", 800, 160, 0.0027, "https://api.anthropic.com/v1/messages"),
    ]
    conn.executemany(
        "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, estimated_cost, endpoint) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def budget_config_file(tmp_path):
    """Create a temp budget config YAML."""
    cfg_path = tmp_path / "budget_config.yaml"
    cfg_path.write_text("daily_limit_usd: 10.0\nalert_at_percent: 80.0\nhard_stop: false\n")
    return str(cfg_path)


# ---------------------------------------------------------------------------
# cost.py tests
# ---------------------------------------------------------------------------

class TestCostQuerySummary:
    def test_today_summary(self, monitor_db):
        with patch.dict(os.environ, {"TOKENPAK_DB": monitor_db}):
            from importlib import reload
            import tokenpak.agent.cli.commands.cost as cost_mod
            reload(cost_mod)
            # Patch _MONITOR_DB directly
            cost_mod._MONITOR_DB = monitor_db
            data = cost_mod.query_summary("today")
        assert data["requests"] == 3
        assert data["input_tokens"] == 3500
        assert data["output_tokens"] == 700
        assert data["total_tokens"] == 4200
        assert data["total_cost_usd"] == pytest.approx(0.0104, abs=1e-4)

    def test_yesterday_summary(self, monitor_db):
        from importlib import reload
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        data = cost_mod.query_summary("yesterday")
        assert data["requests"] == 2
        assert data["total_cost_usd"] == pytest.approx(0.063, abs=1e-3)

    def test_week_summary(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        data = cost_mod.query_summary("week")
        assert data["requests"] >= 5  # today + yesterday + last_week

    def test_month_summary(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        data = cost_mod.query_summary("month")
        assert data["requests"] >= 3

    def test_empty_db(self, tmp_path):
        """No crash on empty DB."""
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL, endpoint TEXT)")
        conn.commit()
        conn.close()
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = db_path
        data = cost_mod.query_summary("today")
        assert data["requests"] == 0
        assert data["total_cost_usd"] == 0.0

    def test_missing_db(self, tmp_path):
        """Returns error dict when DB doesn't exist."""
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = str(tmp_path / "nonexistent.db")
        data = cost_mod.query_summary("today")
        assert "error" in data


class TestCostQueryByModel:
    def test_by_model_today(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        rows = cost_mod.query_by_model("today")
        assert len(rows) == 2  # sonnet + haiku
        models = [r["model"] for r in rows]
        assert "claude-sonnet-4-6" in models
        assert "claude-haiku-4-5" in models

    def test_by_model_sorted_by_cost(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        rows = cost_mod.query_by_model("today")
        costs = [r["cost_usd"] for r in rows]
        assert costs == sorted(costs, reverse=True)

    def test_by_model_empty(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL, endpoint TEXT)")
        conn.commit()
        conn.close()
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = db_path
        rows = cost_mod.query_by_model("today")
        assert rows == []

    def test_by_model_missing_db(self, tmp_path):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = str(tmp_path / "no.db")
        rows = cost_mod.query_by_model("today")
        assert rows == []


class TestCostQueryByAgent:
    def test_by_agent_returns_data(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        rows = cost_mod.query_by_agent("today")
        assert len(rows) >= 1

    def test_by_agent_has_required_keys(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        rows = cost_mod.query_by_agent("today")
        for r in rows:
            assert "agent" in r
            assert "requests" in r
            assert "cost_usd" in r


class TestCostExportCsv:
    def test_csv_has_header(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        csv_data = cost_mod.export_csv_data("today")
        lines = csv_data.strip().splitlines()
        assert lines[0] == "timestamp,model,input_tokens,output_tokens,estimated_cost"

    def test_csv_row_count(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        csv_data = cost_mod.export_csv_data("today")
        reader = csv.DictReader(io.StringIO(csv_data))
        rows = list(reader)
        assert len(rows) == 3

    def test_csv_empty_db(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL, endpoint TEXT)")
        conn.commit()
        conn.close()
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = db_path
        csv_data = cost_mod.export_csv_data("today")
        # Just header row
        assert "timestamp" in csv_data
        reader = csv.DictReader(io.StringIO(csv_data))
        assert list(reader) == []

    def test_csv_yesterday(self, monitor_db):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        csv_data = cost_mod.export_csv_data("yesterday")
        reader = csv.DictReader(io.StringIO(csv_data))
        rows = list(reader)
        assert len(rows) == 2


class TestCostPrintFunctions:
    def test_print_summary_output(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        cost_mod.print_summary("today")
        out = capsys.readouterr().out
        assert "TOKENPAK" in out
        assert "Cost" in out
        assert "Requests:" in out
        assert "Total Cost:" in out

    def test_print_summary_raw(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        cost_mod.print_summary("today", raw=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["requests"] == 3

    def test_print_by_model_output(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        cost_mod.print_by_model("today")
        out = capsys.readouterr().out
        assert "claude-sonnet-4-6" in out

    def test_print_by_model_raw(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        cost_mod.print_by_model("today", raw=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 2

    def test_print_by_agent_output(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.cost as cost_mod
        cost_mod._MONITOR_DB = monitor_db
        cost_mod.print_by_agent("today")
        out = capsys.readouterr().out
        assert "Agent" in out


# ---------------------------------------------------------------------------
# budget.py tests
# ---------------------------------------------------------------------------

class TestBudgetConfig:
    def test_load_config(self, budget_config_file):
        import tokenpak.agent.cli.commands.budget as budget_mod
        orig = budget_mod._BUDGET_CONFIG
        budget_mod._BUDGET_CONFIG = Path(budget_config_file)
        try:
            cfg = budget_mod._load_config()
            assert cfg["daily_limit_usd"] == 10.0
            assert cfg["alert_at_percent"] == 80.0
        finally:
            budget_mod._BUDGET_CONFIG = orig

    def test_load_config_missing_returns_empty(self, tmp_path):
        import tokenpak.agent.cli.commands.budget as budget_mod
        orig = budget_mod._BUDGET_CONFIG
        budget_mod._BUDGET_CONFIG = tmp_path / "no_config.yaml"
        try:
            cfg = budget_mod._load_config()
            assert cfg == {}
        finally:
            budget_mod._BUDGET_CONFIG = orig

    def test_save_config(self, tmp_path):
        import tokenpak.agent.cli.commands.budget as budget_mod
        orig = budget_mod._BUDGET_CONFIG
        cfg_path = tmp_path / "budget_config.yaml"
        budget_mod._BUDGET_CONFIG = cfg_path
        try:
            budget_mod._save_config({"daily_limit_usd": 25.0, "alert_at_percent": 90.0})
            loaded = budget_mod._load_config()
            assert loaded["daily_limit_usd"] == 25.0
            assert loaded["alert_at_percent"] == 90.0
        finally:
            budget_mod._BUDGET_CONFIG = orig


class TestBudgetSpendQueries:
    def test_get_spent_today(self, monitor_db):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        spent = budget_mod._get_spent("daily")
        assert spent == pytest.approx(0.0104, abs=1e-4)

    def test_get_spent_monthly(self, monitor_db):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        spent = budget_mod._get_spent("monthly")
        # includes today + yesterday (both in current month if same month)
        assert spent > 0

    def test_get_spent_missing_db(self, tmp_path):
        import tokenpak.agent.cli.commands.budget as budget_mod
        orig = budget_mod._MONITOR_DB
        budget_mod._MONITOR_DB = str(tmp_path / "no.db")
        try:
            spent = budget_mod._get_spent("daily")
            assert spent == 0.0
        finally:
            budget_mod._MONITOR_DB = orig


class TestBudgetHistory:
    def test_history_returns_list(self, monitor_db):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        history = budget_mod._budget_history(days=30)
        assert isinstance(history, list)
        assert len(history) > 0

    def test_history_has_required_keys(self, monitor_db):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        history = budget_mod._budget_history(days=30)
        for r in history:
            assert "day" in r
            assert "requests" in r
            assert "cost_usd" in r

    def test_history_sorted_by_day(self, monitor_db):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        history = budget_mod._budget_history(days=30)
        days = [r["day"] for r in history]
        assert days == sorted(days)

    def test_history_empty_db(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL, endpoint TEXT)")
        conn.commit()
        conn.close()
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = db_path
        history = budget_mod._budget_history(days=30)
        assert history == []


class TestBudgetForecast:
    def test_forecast_structure(self, monitor_db):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        fc = budget_mod._budget_forecast("monthly")
        assert "daily_avg_usd" in fc
        assert "days_remaining" in fc
        assert "projected_total_usd" in fc
        assert "already_spent_usd" in fc

    def test_forecast_projected_positive(self, monitor_db):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        fc = budget_mod._budget_forecast("monthly")
        assert fc["projected_total_usd"] >= 0

    def test_forecast_empty_db_no_crash(self, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL, endpoint TEXT)")
        conn.commit()
        conn.close()
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = db_path
        fc = budget_mod._budget_forecast("monthly")
        assert fc["projected_total_usd"] == 0.0


class TestBudgetPrintFunctions:
    def test_print_status_no_limit(self, monitor_db, tmp_path, capsys):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        orig_cfg = budget_mod._BUDGET_CONFIG
        budget_mod._BUDGET_CONFIG = tmp_path / "empty_cfg.yaml"
        try:
            budget_mod.print_budget_status()
            out = capsys.readouterr().out
            assert "Budget Status" in out
            assert "not set" in out
        finally:
            budget_mod._BUDGET_CONFIG = orig_cfg

    def test_print_status_with_limit(self, monitor_db, budget_config_file, capsys):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        orig_cfg = budget_mod._BUDGET_CONFIG
        budget_mod._BUDGET_CONFIG = Path(budget_config_file)
        try:
            budget_mod.print_budget_status()
            out = capsys.readouterr().out
            assert "Limit:" in out
            assert "Spent:" in out
            assert "Progress:" in out
        finally:
            budget_mod._BUDGET_CONFIG = orig_cfg

    def test_print_status_raw(self, monitor_db, budget_config_file, capsys):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        orig_cfg = budget_mod._BUDGET_CONFIG
        budget_mod._BUDGET_CONFIG = Path(budget_config_file)
        try:
            budget_mod.print_budget_status(raw=True)
            out = capsys.readouterr().out
            data = json.loads(out)
            assert "daily" in data
            assert "monthly" in data
        finally:
            budget_mod._BUDGET_CONFIG = orig_cfg

    def test_print_history_output(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        budget_mod.print_budget_history(days=30)
        out = capsys.readouterr().out
        assert "History" in out
        assert "Date" in out

    def test_print_history_raw(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        budget_mod.print_budget_history(days=30, raw=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)

    def test_print_forecast_output(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        budget_mod.print_budget_forecast()
        out = capsys.readouterr().out
        assert "Forecast" in out
        assert "Daily Avg" in out

    def test_print_forecast_raw(self, monitor_db, capsys):
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        budget_mod.print_budget_forecast(raw=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "projected_total_usd" in data


class TestBudgetAlertThreshold:
    def test_alert_triggered_when_over_threshold(self, monitor_db, tmp_path, capsys):
        """Alert should appear when spend exceeds threshold."""
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        cfg_path = tmp_path / "alert_cfg.yaml"
        # Set very low limit so today's spend triggers alert
        cfg_path.write_text("daily_limit_usd: 0.001\nalert_at_percent: 80.0\nhard_stop: false\n")
        orig_cfg = budget_mod._BUDGET_CONFIG
        budget_mod._BUDGET_CONFIG = cfg_path
        try:
            budget_mod.print_budget_status()
            out = capsys.readouterr().out
            assert "ALERT" in out
        finally:
            budget_mod._BUDGET_CONFIG = orig_cfg

    def test_no_alert_when_under_threshold(self, monitor_db, tmp_path, capsys):
        """No alert when spend is below threshold."""
        import tokenpak.agent.cli.commands.budget as budget_mod
        budget_mod._MONITOR_DB = monitor_db
        cfg_path = tmp_path / "safe_cfg.yaml"
        # Set very high limit so today's spend is safely under
        cfg_path.write_text("daily_limit_usd: 1000.0\nalert_at_percent: 80.0\nhard_stop: false\n")
        orig_cfg = budget_mod._BUDGET_CONFIG
        budget_mod._BUDGET_CONFIG = cfg_path
        try:
            budget_mod.print_budget_status()
            out = capsys.readouterr().out
            assert "ALERT" not in out
        finally:
            budget_mod._BUDGET_CONFIG = orig_cfg
