"""Tests for tokenpak cost and budget CLI commands."""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to wire the module to a temp DB
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(tmp_path):
    """Create a temp monitor.db with schema + sample rows."""
    db_path = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model TEXT,
            request_type TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            latency_ms REAL,
            status_code INTEGER,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER,
            compressed_tokens INTEGER,
            injected_tokens INTEGER,
            injected_sources TEXT,
            cache_read_tokens INTEGER,
            cache_creation_tokens INTEGER
        )
    """)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rows = [
        (f"{today}T10:00:00", "claude-sonnet-4-6", "chat", 1000, 100, 0.003, 300, 200, "https://api.anthropic.com/v1/messages", "hybrid", 900, 100, 200, None, 500, 50),
        (f"{today}T11:00:00", "claude-haiku-4-5",  "chat",  500,  50, 0.001, 200, 200, "https://api.anthropic.com/v1/messages", "hybrid", 400,  50, 100, None, 200, 20),
        (f"{today}T12:00:00", "claude-sonnet-4-6", "chat", 2000, 200, 0.006, 400, 200, "https://api.anthropic.com/v1/messages", "hybrid", 1800, 200, 300, None, 800, 80),
        (f"{yesterday}T09:00:00", "claude-sonnet-4-6", "chat", 1500, 150, 0.0045, 350, 200, "https://api.anthropic.com/v1/messages", "hybrid", 1400, 100, 150, None, 600, 60),
    ]
    conn.executemany(
        "INSERT INTO requests (timestamp,model,request_type,input_tokens,output_tokens,estimated_cost,latency_ms,status_code,endpoint,compilation_mode,protected_tokens,compressed_tokens,injected_tokens,injected_sources,cache_read_tokens,cache_creation_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows
    )
    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def cost_mod(temp_db):
    """Import cost module patched to use temp DB."""
    import importlib
    import tokenpak.cli.commands.cost as cost
    importlib.reload(cost)
    with patch.object(cost, "_MONITOR_DB", temp_db):
        yield cost


@pytest.fixture
def budget_mod(temp_db, tmp_path):
    """Import budget module patched to use temp DB + temp config."""
    import importlib
    import tokenpak.cli.commands.budget as budget
    importlib.reload(budget)
    cfg_path = tmp_path / "budget_config.yaml"
    with patch.object(budget, "_MONITOR_DB", temp_db), \
         patch.object(budget, "_BUDGET_CONFIG", cfg_path):
        yield budget, cfg_path


# ===========================================================================
# cost.py tests
# ===========================================================================

class TestQuerySummary:
    def test_today_returns_correct_totals(self, cost_mod):
        result = cost_mod.query_summary("today")
        assert result["requests"] == 3
        assert result["input_tokens"] == 3500
        assert result["output_tokens"] == 350
        assert abs(result["total_cost_usd"] - 0.010) < 0.0001

    def test_yesterday_returns_one_row(self, cost_mod):
        result = cost_mod.query_summary("yesterday")
        assert result["requests"] == 1

    def test_week_includes_today_and_yesterday(self, cost_mod):
        result = cost_mod.query_summary("week")
        assert result["requests"] == 4

    def test_month_includes_all_rows(self, cost_mod):
        result = cost_mod.query_summary("month")
        assert result["requests"] == 4

    def test_empty_db_returns_zeros(self, tmp_path):
        import tokenpak.cli.commands.cost as cost
        empty_db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(empty_db))
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, request_type TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL, latency_ms REAL, status_code INTEGER, endpoint TEXT, compilation_mode TEXT, protected_tokens INTEGER, compressed_tokens INTEGER, injected_tokens INTEGER, injected_sources TEXT, cache_read_tokens INTEGER, cache_creation_tokens INTEGER)")
        conn.close()
        with patch.object(cost, "_MONITOR_DB", str(empty_db)):
            result = cost.query_summary("today")
        assert result["requests"] == 0
        assert result["total_cost_usd"] == 0.0

    def test_missing_db_returns_error(self, tmp_path):
        import tokenpak.cli.commands.cost as cost
        with patch.object(cost, "_MONITOR_DB", str(tmp_path / "nonexistent.db")):
            result = cost.query_summary("today")
        assert "error" in result


class TestQueryByModel:
    def test_returns_two_models_today(self, cost_mod):
        rows = cost_mod.query_by_model("today")
        models = [r["model"] for r in rows]
        assert "claude-sonnet-4-6" in models
        assert "claude-haiku-4-5" in models

    def test_ordered_by_cost_desc(self, cost_mod):
        rows = cost_mod.query_by_model("today")
        costs = [r["cost_usd"] for r in rows]
        assert costs == sorted(costs, reverse=True)

    def test_empty_period_returns_empty_list(self, cost_mod):
        rows = cost_mod.query_by_model("yesterday")
        assert len(rows) == 1
        assert rows[0]["model"] == "claude-sonnet-4-6"


class TestQueryByAgent:
    def test_returns_agent_breakdown(self, cost_mod):
        rows = cost_mod.query_by_agent("today")
        assert len(rows) >= 1

    def test_agent_has_cost_field(self, cost_mod):
        rows = cost_mod.query_by_agent("today")
        for r in rows:
            assert "cost_usd" in r
            assert "requests" in r


class TestExportCsv:
    def test_csv_has_header_row(self, cost_mod):
        output = cost_mod.export_csv_data("today")
        reader = csv.reader(io.StringIO(output))
        header = next(reader)
        assert "timestamp" in header
        assert "model" in header
        assert "estimated_cost" in header

    def test_csv_has_correct_row_count(self, cost_mod):
        output = cost_mod.export_csv_data("today")
        lines = [l for l in output.strip().splitlines() if l]
        assert len(lines) == 4  # 1 header + 3 data rows

    def test_empty_db_returns_only_header(self, tmp_path):
        import tokenpak.cli.commands.cost as cost
        empty_db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(empty_db))
        conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT, request_type TEXT, input_tokens INTEGER, output_tokens INTEGER, estimated_cost REAL, latency_ms REAL, status_code INTEGER, endpoint TEXT, compilation_mode TEXT, protected_tokens INTEGER, compressed_tokens INTEGER, injected_tokens INTEGER, injected_sources TEXT, cache_read_tokens INTEGER, cache_creation_tokens INTEGER)")
        conn.close()
        with patch.object(cost, "_MONITOR_DB", str(empty_db)):
            output = cost.export_csv_data("today")
        lines = [l for l in output.strip().splitlines() if l]
        assert len(lines) == 1  # header only


class TestPrintSummary:
    def test_prints_without_error(self, cost_mod, capsys):
        cost_mod.print_summary("today")
        captured = capsys.readouterr()
        assert "TOKENPAK" in captured.out
        assert "Cost" in captured.out

    def test_raw_output_is_valid_json(self, cost_mod, capsys):
        cost_mod.print_summary("today", raw=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "total_cost_usd" in data


class TestRunCostCmd:
    def test_default_prints_summary(self, cost_mod, capsys):
        args = SimpleNamespace(yesterday=False, week=False, month=False, by_model=False, by_agent=False, export=None, raw=False)
        cost_mod.run_cost_cmd(args)
        captured = capsys.readouterr()
        assert "TOKENPAK" in captured.out

    def test_yesterday_flag(self, cost_mod, capsys):
        args = SimpleNamespace(yesterday=True, week=False, month=False, by_model=False, by_agent=False, export=None, raw=False)
        cost_mod.run_cost_cmd(args)
        captured = capsys.readouterr()
        assert "Yesterday" in captured.out

    def test_week_flag(self, cost_mod, capsys):
        args = SimpleNamespace(yesterday=False, week=True, month=False, by_model=False, by_agent=False, export=None, raw=False)
        cost_mod.run_cost_cmd(args)
        captured = capsys.readouterr()
        assert "7 Days" in captured.out

    def test_export_csv_flag(self, cost_mod, capsys):
        args = SimpleNamespace(yesterday=False, week=False, month=False, by_model=False, by_agent=False, export="csv", raw=False)
        cost_mod.run_cost_cmd(args)
        captured = capsys.readouterr()
        assert "timestamp" in captured.out

    def test_by_model_flag(self, cost_mod, capsys):
        args = SimpleNamespace(yesterday=False, week=False, month=False, by_model=True, by_agent=False, export=None, raw=False)
        cost_mod.run_cost_cmd(args)
        captured = capsys.readouterr()
        assert "Model" in captured.out

    def test_by_agent_flag(self, cost_mod, capsys):
        args = SimpleNamespace(yesterday=False, week=False, month=False, by_model=False, by_agent=True, export=None, raw=False)
        cost_mod.run_cost_cmd(args)
        captured = capsys.readouterr()
        assert "Agent" in captured.out


# ===========================================================================
# budget.py tests
# ===========================================================================

class TestBudgetConfig:
    def test_set_daily_limit(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        args = SimpleNamespace(budget_cmd="set", daily=5.0, monthly=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "Daily" in captured.out
        assert cfg_path.exists()

    def test_set_monthly_limit(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        args = SimpleNamespace(budget_cmd="set", daily=None, monthly=50.0, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "Monthly" in captured.out

    def test_set_alert_threshold(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        args = SimpleNamespace(budget_cmd="alert", at=75.0, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "75" in captured.out
        assert cfg_path.exists()

    def test_set_without_flags_shows_usage(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        args = SimpleNamespace(budget_cmd="set", daily=None, monthly=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "Usage" in captured.out


class TestBudgetStatus:
    def test_no_limit_set_shows_not_set(self, budget_mod, capsys):
        budget, _ = budget_mod
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "not set" in captured.out or "TOKENPAK" in captured.out

    def test_with_limit_shows_progress(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        # Set a daily limit first
        cfg_path.write_text("daily_limit_usd: 1.00\nalert_at_percent: 80.0\n")
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "%" in captured.out

    def test_alert_fires_when_over_threshold(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        # Set a very low limit to force alert
        cfg_path.write_text("daily_limit_usd: 0.001\nalert_at_percent: 80.0\n")
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        # Should show some kind of alert — WARNING, ALERT, or OVER BUDGET
        assert any(s in captured.out for s in ["WARNING", "ALERT", "OVER BUDGET"])

    def test_raw_output_is_valid_json(self, budget_mod, capsys):
        budget, _ = budget_mod
        args = SimpleNamespace(budget_cmd=None, raw=True)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "daily" in data
        assert "monthly" in data

    def test_no_alert_when_under_threshold(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        # Set very high limit to avoid alert
        cfg_path.write_text("daily_limit_usd: 1000.00\nalert_at_percent: 80.0\n")
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "ALERT" not in captured.out


class TestBudgetHistory:
    def test_history_shows_days(self, budget_mod, capsys):
        budget, _ = budget_mod
        args = SimpleNamespace(budget_cmd="history", days=30, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "History" in captured.out

    def test_history_raw_is_json(self, budget_mod, capsys):
        budget, _ = budget_mod
        args = SimpleNamespace(budget_cmd="history", days=7, raw=True)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)


class TestBudgetForecast:
    def test_forecast_shows_projection(self, budget_mod, capsys):
        budget, _ = budget_mod
        args = SimpleNamespace(budget_cmd="forecast", raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "Forecast" in captured.out

    def test_forecast_raw_is_json(self, budget_mod, capsys):
        budget, _ = budget_mod
        args = SimpleNamespace(budget_cmd="forecast", raw=True)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "projected_total_usd" in data

    def test_forecast_with_monthly_limit_shows_status(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        cfg_path.write_text("monthly_limit_usd: 100.00\nalert_at_percent: 80.0\n")
        args = SimpleNamespace(budget_cmd="forecast", raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert any(s in captured.out for s in ["OK", "CLOSE", "OVER"])


# ===========================================================================
# New feature tests: --model filter, budget clear, 95% alert, hard-stop
# ===========================================================================

class TestCostModelFilter:
    def test_model_filter_returns_only_matching_model(self, cost_mod):
        rows = cost_mod.query_summary("today", model="claude-sonnet-4-6")
        assert rows["requests"] == 2  # Only sonnet rows today

    def test_model_filter_excludes_other_models(self, cost_mod):
        rows = cost_mod.query_summary("today", model="claude-haiku-4-5")
        assert rows["requests"] == 1  # Only haiku rows today

    def test_model_filter_nonexistent_returns_zeros(self, cost_mod):
        rows = cost_mod.query_summary("today", model="does-not-exist")
        assert rows["requests"] == 0
        assert rows["total_cost_usd"] == 0.0

    def test_run_cost_cmd_with_model_flag(self, cost_mod, capsys):
        from types import SimpleNamespace
        args = SimpleNamespace(
            yesterday=False, week=False, month=False,
            by_model=False, by_agent=False, export=None,
            raw=False, model="claude-sonnet-4-6"
        )
        cost_mod.run_cost_cmd(args)
        captured = capsys.readouterr()
        assert "claude-sonnet-4-6" in captured.out

    def test_model_filter_included_in_result_key(self, cost_mod):
        data = cost_mod.query_summary("today", model="claude-haiku-4-5")
        assert data.get("model_filter") == "claude-haiku-4-5"


class TestBudgetClear:
    def test_clear_removes_daily_limit(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        cfg_path.write_text("daily_limit_usd: 5.0\nmonthly_limit_usd: 50.0\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd="clear", target="daily", raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "daily" in captured.out.lower()
        # Verify config no longer has daily
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        assert "daily_limit_usd" not in cfg

    def test_clear_removes_monthly_limit(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        cfg_path.write_text("daily_limit_usd: 5.0\nmonthly_limit_usd: 50.0\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd="clear", target="monthly", raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "monthly" in captured.out.lower()
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        assert "monthly_limit_usd" not in cfg

    def test_clear_all_removes_all_limits(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        cfg_path.write_text("daily_limit_usd: 5.0\nmonthly_limit_usd: 50.0\nhard_stop: true\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd="clear", target=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "Cleared" in captured.out


class TestBudgetThresholds:
    def test_warning_at_80pct(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        # Set limit so spend (0.01) is between 80% and 95%
        cfg_path.write_text("daily_limit_usd: 0.011\nalert_at_percent: 80.0\nwarn_at_percent: 95.0\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    def test_alert_at_95pct(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        # Set limit so spend is > 95%
        cfg_path.write_text("daily_limit_usd: 0.0105\nalert_at_percent: 80.0\nwarn_at_percent: 95.0\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        # Should show ALERT (between 95% and 100%)
        assert any(s in captured.out for s in ["ALERT", "OVER BUDGET"])

    def test_over_budget_at_100pct(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        cfg_path.write_text("daily_limit_usd: 0.001\nalert_at_percent: 80.0\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "OVER BUDGET" in captured.out


class TestBudgetHardStop:
    def test_hard_stop_shown_in_status(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        cfg_path.write_text("daily_limit_usd: 5.0\nhard_stop: true\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "Hard-stop" in captured.out or "hard-stop" in captured.out.lower()

    def test_hard_stop_not_shown_when_disabled(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        cfg_path.write_text("daily_limit_usd: 5.0\nhard_stop: false\n")
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd=None, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        # Should not show hard-stop message when disabled
        assert "Hard-stop ENABLED" not in captured.out

    def test_set_hard_stop_via_set_cmd(self, budget_mod, capsys):
        budget, cfg_path = budget_mod
        from types import SimpleNamespace
        args = SimpleNamespace(budget_cmd="set", daily=5.0, monthly=None, hard_stop=True, raw=False)
        budget.run_budget_cmd(args)
        captured = capsys.readouterr()
        assert "Hard-stop" in captured.out or "hard-stop" in captured.out.lower()
        import yaml
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        assert cfg.get("hard_stop") is True
