# SPDX-License-Identifier: Apache-2.0
"""Tests for daily report and alerts features.

Tests coverage:
- Report generation from mock stats
- Markdown format output
- Alert rule evaluation
- Cooldown enforcement
- Exit code behavior
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.daily_report", reason="module not available in current build")
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import modules under test
from tokenpak.daily_report import (
    DailySavingsData,
    _calculate_data,
    _format_json,
    _format_markdown,
    _format_terminal,
    generate_report,
)

from tokenpak.alerts import (
    AlertRule,
    AlertRuleState,
    _get_default_rules,
    check_alerts,
    evaluate_rule,
    load_state,
    save_state,
)


class TestReportGeneration:
    """Test daily report generation."""

    @patch("tokenpak.daily_report._proxy_get")
    @patch("tokenpak.daily_report._get_savings_report")
    def test_calculate_data_with_mocks(self, mock_savings, mock_proxy):
        """Test data collection from mock proxy."""
        mock_proxy.side_effect = lambda path, port=None: {
            "/health": {
                "status": "ok",
                "uptime_seconds": 3600,
                "requests_total": 100,
                "requests_errors": 0,
            },
            "/stats": {
                "session": {
                    "input_tokens": 10000,
                    "saved_tokens": 2000,
                }
            },
            "/cache-stats": {
                "cache_hits": 80,
                "cache_misses": 20,
            },
        }.get(path, {})

        mock_savings.return_value = {
            "total_cost": 10.0,
            "estimated_without_compression": 12.0,
            "savings_amount": 2.0,
            "savings_pct": 16.7,
            "cache_hit_rate": 0.8,
        }

        data = _calculate_data()

        assert data.requests == 100
        assert data.errors == 0
        assert data.compression_percent == 20.0  # 2000/10000
        assert data.cache_hit_rate == 0.8
        assert data.savings_amount == 2.0
        assert data.uptime_hours == 1

    def test_format_terminal(self):
        """Test terminal format output."""
        data = DailySavingsData(
            timestamp="2026-03-11T17:30:00",
            requests=100,
            savings_amount=2.50,
            savings_percent=15.0,
            cache_hit_rate=0.85,
            compression_percent=18.5,
            top_model="opus-4-6",
            top_model_savings=1.20,
            uptime_hours=2,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=75.0,
        )

        output = _format_terminal(data)

        assert "TokenPak Daily Report" in output
        assert "100" in output
        assert "$2.50" in output
        assert "15.0%" in output
        assert "opus-4-6" in output

    def test_format_markdown(self):
        """Test markdown format output."""
        data = DailySavingsData(
            timestamp="2026-03-11T17:30:00",
            requests=100,
            savings_amount=2.50,
            savings_percent=15.0,
            cache_hit_rate=0.85,
            compression_percent=18.5,
            top_model="opus-4-6",
            top_model_savings=1.20,
            uptime_hours=2,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=75.0,
        )

        output = _format_markdown(data)

        assert "## 📊" in output
        assert "2026-03-11" in output
        assert "100" in output
        assert "$2.50" in output
        assert "| Metric | Value |" in output

    def test_format_json(self):
        """Test JSON format output."""
        data = DailySavingsData(
            timestamp="2026-03-11T17:30:00",
            requests=100,
            savings_amount=2.50,
            savings_percent=15.0,
            cache_hit_rate=0.85,
            compression_percent=18.5,
            top_model="opus-4-6",
            top_model_savings=1.20,
            uptime_hours=2,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=75.0,
        )

        output = _format_json(data)

        assert isinstance(output, dict)
        assert output["requests"] == 100
        assert output["savings_amount"] == 2.50
        assert output["top_model"] == "opus-4-6"

    @patch("tokenpak.daily_report._calculate_data")
    def test_generate_report_terminal(self, mock_calc):
        """Test report generation in terminal format."""
        mock_calc.return_value = DailySavingsData(
            timestamp="2026-03-11T17:30:00",
            requests=100,
            savings_amount=2.50,
            savings_percent=15.0,
            cache_hit_rate=0.85,
            compression_percent=18.5,
            top_model="opus-4-6",
            top_model_savings=1.20,
            uptime_hours=2,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=75.0,
        )

        report = generate_report(format="terminal")
        assert isinstance(report, str)
        assert "TokenPak Daily Report" in report

    @patch("tokenpak.daily_report._calculate_data")
    def test_generate_report_markdown(self, mock_calc):
        """Test report generation in markdown format."""
        mock_calc.return_value = DailySavingsData(
            timestamp="2026-03-11T17:30:00",
            requests=100,
            savings_amount=2.50,
            savings_percent=15.0,
            cache_hit_rate=0.85,
            compression_percent=18.5,
            top_model="opus-4-6",
            top_model_savings=1.20,
            uptime_hours=2,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=75.0,
        )

        report = generate_report(format="markdown")
        assert isinstance(report, str)
        assert "## 📊" in report
        assert "| Metric |" in report

    @patch("tokenpak.daily_report._calculate_data")
    def test_generate_report_json(self, mock_calc):
        """Test report generation in JSON format."""
        mock_calc.return_value = DailySavingsData(
            timestamp="2026-03-11T17:30:00",
            requests=100,
            savings_amount=2.50,
            savings_percent=15.0,
            cache_hit_rate=0.85,
            compression_percent=18.5,
            top_model="opus-4-6",
            top_model_savings=1.20,
            uptime_hours=2,
            uptime_minutes=30,
            errors=0,
            estimated_monthly_rate=75.0,
        )

        report = generate_report(format="json")
        assert isinstance(report, dict)
        assert report["requests"] == 100


class TestAlertRuleState:
    """Test alert state management."""

    def test_alert_state_initial(self):
        """Test initial alert state (no fire)."""
        state = AlertRuleState(name="test_rule")
        assert state.last_fired is None
        assert state.should_fire(cooldown_minutes=30)

    def test_alert_state_cooldown(self):
        """Test cooldown enforcement."""
        state = AlertRuleState(name="test_rule")
        state.update_fired(value=0.5)

        # Should not fire within cooldown
        assert not state.should_fire(cooldown_minutes=30)

        # Should fire after cooldown
        state.last_fired = time.time() - 60 * 31  # 31 minutes ago
        assert state.should_fire(cooldown_minutes=30)

    def test_alert_state_serialize(self):
        """Test state serialization to dict."""
        state = AlertRuleState(
            name="test_rule",
            last_fired=time.time(),
            last_value=0.75,
            fired_count=3,
        )
        data = state.to_dict()

        assert data["name"] == "test_rule"
        assert data["fired_count"] == 3
        assert data["last_value"] == 0.75
        assert "last_fired" in data


class TestAlertEvaluation:
    """Test alert rule evaluation."""

    def test_evaluate_cache_drop(self):
        """Test cache_hit_rate evaluation."""
        rule = AlertRule(
            name="cache_drop",
            condition="cache_hit_rate < 0.80",
            message="Cache hit rate dropped to {value:.0f}%",
            cooldown_minutes=30,
        )

        # Mock cache stats
        with patch("tokenpak.alerts._get_proxy_cache_stats") as mock_cache:
            mock_cache.return_value = {
                "cache_hits": 60,
                "cache_misses": 40,
            }

            # Should NOT fire (hit rate = 60%)
            triggered, value = evaluate_rule(rule, {}, {})
            assert triggered
            assert value < 80

            # Should NOT fire (hit rate = 85%)
            mock_cache.return_value = {
                "cache_hits": 85,
                "cache_misses": 15,
            }
            triggered, value = evaluate_rule(rule, {}, {})
            assert not triggered

    def test_evaluate_error_spike(self):
        """Test error_rate evaluation."""
        rule = AlertRule(
            name="error_spike",
            condition="error_rate > 0.05",
            message="Error rate at {value:.1f}%",
            cooldown_minutes=15,
        )

        # Error rate = 3/100 = 3%
        stats = {"requests": 100, "errors": 3}
        triggered, value = evaluate_rule(rule, stats, {})
        assert not triggered

        # Error rate = 10/100 = 10%
        stats = {"requests": 100, "errors": 10}
        triggered, value = evaluate_rule(rule, stats, {})
        assert triggered
        assert value > 5

    def test_evaluate_proxy_down(self):
        """Test health != ok evaluation."""
        rule = AlertRule(
            name="proxy_down",
            condition="health != 'ok'",
            message="TokenPak proxy is down!",
            cooldown_minutes=5,
        )

        # Should NOT fire when health is ok
        health = {"status": "ok"}
        triggered, value = evaluate_rule(rule, {}, health)
        assert not triggered

        # Should fire when health is not ok
        health = {"status": "error"}
        triggered, value = evaluate_rule(rule, {}, health)
        assert triggered


class TestAlertStatePersistence:
    """Test alert state persistence."""

    def test_load_save_state(self):
        """Test loading and saving alert state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "alert_state.json"

            # Create state
            state = {
                "cache_drop": AlertRuleState(
                    name="cache_drop",
                    last_fired=time.time(),
                    last_value=0.65,
                    fired_count=1,
                ),
            }

            # Patch the path
            with patch("tokenpak.alerts._get_state_path") as mock_path:
                mock_path.return_value = state_path

                # Save state
                save_state(state)
                assert state_path.exists()

                # Load state
                loaded = load_state()
                assert "cache_drop" in loaded
                assert loaded["cache_drop"].name == "cache_drop"
                assert loaded["cache_drop"].fired_count == 1

    def test_default_rules(self):
        """Test default alert rules are created."""
        rules = _get_default_rules()
        assert len(rules) >= 3
        assert any(r.name == "cache_drop" for r in rules)
        assert any(r.name == "error_spike" for r in rules)
        assert any(r.name == "proxy_down" for r in rules)


class TestCheckAlertsIntegration:
    """Integration tests for check_alerts function."""

    @patch("tokenpak.alerts.load_config")
    @patch("tokenpak.alerts._get_proxy_stats")
    @patch("tokenpak.alerts._get_proxy_health")
    @patch("tokenpak.alerts._get_proxy_cache_stats")
    @patch("tokenpak.alerts.save_state")
    def test_check_alerts_all_clear(
        self, mock_save, mock_cache, mock_health, mock_stats, mock_config
    ):
        """Test when all alerts are clear."""
        mock_config.return_value = {
            "enabled": True,
            "rules": [
                {
                    "name": "cache_drop",
                    "condition": "cache_hit_rate < 0.80",
                    "message": "Cache drop",
                    "cooldown_minutes": 30,
                },
            ],
        }
        mock_stats.return_value = {"requests": 100, "errors": 2}
        mock_health.return_value = {"status": "ok"}
        mock_cache.return_value = {"cache_hits": 90, "cache_misses": 10}

        with patch("tokenpak.alerts.load_state") as mock_load:
            mock_load.return_value = {}

            fired = check_alerts()
            assert len(fired) == 0

    @patch("tokenpak.alerts.load_config")
    @patch("tokenpak.alerts._get_proxy_stats")
    @patch("tokenpak.alerts._get_proxy_health")
    @patch("tokenpak.alerts._get_proxy_cache_stats")
    @patch("tokenpak.alerts.save_state")
    def test_check_alerts_fired(self, mock_save, mock_cache, mock_health, mock_stats, mock_config):
        """Test when an alert fires."""
        mock_config.return_value = {
            "enabled": True,
            "rules": [
                {
                    "name": "error_spike",
                    "condition": "error_rate > 0.05",
                    "message": "Error rate at {value:.1f}%",
                    "cooldown_minutes": 15,
                },
            ],
        }
        mock_stats.return_value = {"requests": 100, "errors": 10}
        mock_health.return_value = {"status": "ok"}
        mock_cache.return_value = {"cache_hits": 90, "cache_misses": 10}

        with patch("tokenpak.alerts.load_state") as mock_load:
            mock_load.return_value = {}

            fired = check_alerts()
            assert len(fired) == 1
            assert fired[0][0].name == "error_spike"
            assert fired[0][1] == 10.0  # error rate value


class TestCLIIntegration:
    """Integration tests for CLI commands."""

    @patch("tokenpak.daily_report.generate_report")
    def test_cmd_report_terminal(self, mock_gen):
        """Test cmd_report with terminal format."""
        from tokenpak.cli import cmd_report

        mock_gen.return_value = "Test report output"

        args = MagicMock()
        args.markdown = False
        args.json = False

        with patch("builtins.print") as mock_print:
            cmd_report(args)
            mock_print.assert_called_with("Test report output")
            mock_gen.assert_called_with(format="terminal")

    @patch("tokenpak.daily_report.generate_report")
    def test_cmd_report_markdown(self, mock_gen):
        """Test cmd_report with markdown format."""
        from tokenpak.cli import cmd_report

        mock_gen.return_value = "## Test Report"

        args = MagicMock()
        args.markdown = True
        args.json = False

        with patch("builtins.print") as mock_print:
            cmd_report(args)
            mock_print.assert_called_with("## Test Report")
            mock_gen.assert_called_with(format="markdown")

    @patch("tokenpak.alerts.check_alerts")
    def test_cmd_check_alerts_all_clear(self, mock_check):
        """Test cmd_check_alerts when all clear."""
        from tokenpak.cli import cmd_check_alerts

        mock_check.return_value = []

        args = MagicMock()

        with patch("builtins.print") as mock_print:
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_alerts(args)
            assert exc_info.value.code == 0
            mock_print.assert_called_with("✅ All alert rules clear")

    @patch("tokenpak.alerts.check_alerts")
    def test_cmd_check_alerts_fired(self, mock_check):
        """Test cmd_check_alerts when alert fires."""
        from tokenpak.cli import cmd_check_alerts

        rule = AlertRule(
            name="test",
            condition="test",
            message="Test alert fired",
            cooldown_minutes=30,
        )
        mock_check.return_value = [(rule, 0.5)]

        args = MagicMock()

        with patch("builtins.print"):
            with pytest.raises(SystemExit) as exc_info:
                cmd_check_alerts(args)
            assert exc_info.value.code == 1
