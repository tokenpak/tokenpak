"""
Tests for TokenPak alerts.py — alert rule evaluation, state management, and health monitoring.

Covers:
- AlertRule and AlertRuleState dataclasses
- load_config / load_state / save_state I/O
- evaluate_rule logic (cache_hit_rate, error_rate, health)
- check_alerts orchestration (cooldown enforcement, state persistence)
- Edge cases: empty stats, zero division, unknown conditions, disabled alerts
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.alerts import (
    AlertRule,
    AlertRuleState,
    check_alerts,
    evaluate_rule,
    load_config,
    load_state,
    save_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    name: str = "test_rule",
    condition: str = "error_rate > 0.05",
    message: str = "Test alert: {value}",
    cooldown_minutes: int = 30,
) -> AlertRule:
    return AlertRule(
        name=name,
        condition=condition,
        message=message,
        cooldown_minutes=cooldown_minutes,
    )


def _make_state(name: str = "test_rule", last_fired: float | None = None, fired_count: int = 0) -> AlertRuleState:
    return AlertRuleState(name=name, last_fired=last_fired, fired_count=fired_count)


# ---------------------------------------------------------------------------
# AlertRule dataclass
# ---------------------------------------------------------------------------


class TestAlertRule:
    """Tests for AlertRule dataclass."""

    def test_defaults(self):
        rule = AlertRule(name="r", condition="x > 1", message="msg")
        assert rule.cooldown_minutes == 30

    def test_fields_stored_correctly(self):
        rule = AlertRule(name="foo", condition="error_rate > 0.05", message="hi", cooldown_minutes=15)
        assert rule.name == "foo"
        assert rule.condition == "error_rate > 0.05"
        assert rule.message == "hi"
        assert rule.cooldown_minutes == 15

    def test_asdict_roundtrip(self):
        rule = AlertRule(name="r", condition="x < 0.8", message="drop", cooldown_minutes=10)
        d = asdict(rule)
        assert d == {"name": "r", "condition": "x < 0.8", "message": "drop", "cooldown_minutes": 10}


# ---------------------------------------------------------------------------
# AlertRuleState — should_fire and update_fired
# ---------------------------------------------------------------------------


class TestAlertRuleState:
    """Tests for AlertRuleState cooldown logic."""

    def test_should_fire_when_never_fired(self):
        state = _make_state(last_fired=None)
        assert state.should_fire(cooldown_minutes=30) is True

    def test_should_not_fire_within_cooldown(self):
        # Fired 1 minute ago, cooldown 30 min
        state = _make_state(last_fired=time.time() - 60)
        assert state.should_fire(cooldown_minutes=30) is False

    def test_should_fire_after_cooldown_elapsed(self):
        # Fired 31 minutes ago, cooldown 30 min
        state = _make_state(last_fired=time.time() - 31 * 60)
        assert state.should_fire(cooldown_minutes=30) is True

    def test_should_fire_exactly_at_boundary(self):
        # Fired exactly cooldown minutes ago — should fire (elapsed >= cooldown)
        state = _make_state(last_fired=time.time() - 30 * 60)
        assert state.should_fire(cooldown_minutes=30) is True

    def test_update_fired_increments_count(self):
        state = _make_state(fired_count=0)
        state.update_fired(value=42.0)
        assert state.fired_count == 1
        assert state.last_value == 42.0
        assert state.last_fired is not None

    def test_update_fired_multiple_times(self):
        state = _make_state()
        state.update_fired(1.0)
        state.update_fired(2.0)
        assert state.fired_count == 2
        assert state.last_value == 2.0

    def test_to_dict_has_expected_keys(self):
        state = AlertRuleState(name="x", last_fired=None, last_value=None, fired_count=5)
        d = state.to_dict()
        assert "name" in d
        assert "fired_count" in d
        assert d["fired_count"] == 5


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for load_config — reads ~/.tokenpak/config.yaml."""

    def test_returns_dict_with_enabled_key(self):
        with patch("tokenpak.alerts._get_config_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/config.yaml")
            config = load_config()
            assert "enabled" in config

    def test_default_config_contains_rules(self):
        with patch("tokenpak.alerts._get_config_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/config.yaml")
            config = load_config()
            assert "rules" in config
            assert len(config["rules"]) > 0

    def test_default_config_enabled_is_true(self):
        with patch("tokenpak.alerts._get_config_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/config.yaml")
            config = load_config()
            assert config["enabled"] is True

    def test_loads_from_real_config_file(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text('{"alerts": {"enabled": false, "rules": []}}')
        with patch("tokenpak.alerts._get_config_path", return_value=cfg):
            config = load_config()
            assert config.get("enabled") is False

    def test_handles_corrupt_config_gracefully(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("NOT VALID YAML OR JSON {{{{")
        with patch("tokenpak.alerts._get_config_path", return_value=cfg):
            # Should not raise — falls back to defaults
            config = load_config()
            assert isinstance(config, dict)


# ---------------------------------------------------------------------------
# load_state / save_state
# ---------------------------------------------------------------------------


class TestStateIO:
    """Tests for alert state persistence."""

    def test_load_state_returns_empty_when_no_file(self, tmp_path):
        state_file = tmp_path / "alert_state.json"
        with patch("tokenpak.alerts._get_state_path", return_value=state_file):
            state = load_state()
            assert state == {}

    def test_save_and_reload_state(self, tmp_path):
        state_file = tmp_path / "alert_state.json"
        original = {"cache_drop": AlertRuleState(name="cache_drop", fired_count=3, last_value=70.0)}
        with patch("tokenpak.alerts._get_state_path", return_value=state_file):
            save_state(original)
            loaded = load_state()
        assert "cache_drop" in loaded
        assert loaded["cache_drop"].fired_count == 3
        assert loaded["cache_drop"].last_value == 70.0

    def test_save_state_creates_parent_directories(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "alert_state.json"
        state = {"rule1": AlertRuleState(name="rule1")}
        with patch("tokenpak.alerts._get_state_path", return_value=nested):
            save_state(state)
        assert nested.exists()

    def test_load_state_handles_corrupt_json(self, tmp_path):
        state_file = tmp_path / "alert_state.json"
        state_file.write_text("{broken json")
        with patch("tokenpak.alerts._get_state_path", return_value=state_file):
            state = load_state()
            assert state == {}


# ---------------------------------------------------------------------------
# evaluate_rule
# ---------------------------------------------------------------------------


class TestEvaluateRule:
    """Tests for rule evaluation logic."""

    # --- cache_hit_rate ---

    def test_cache_hit_rate_below_threshold_fires(self):
        rule = _make_rule(condition="cache_hit_rate < 0.80")
        cache_stats = {"cache_hits": 70, "cache_misses": 30}
        with patch("tokenpak.alerts._get_proxy_cache_stats", return_value=cache_stats):
            fired, value = evaluate_rule(rule, stats={}, health={})
        assert fired is True
        assert value == pytest.approx(70.0)

    def test_cache_hit_rate_above_threshold_does_not_fire(self):
        rule = _make_rule(condition="cache_hit_rate < 0.80")
        cache_stats = {"cache_hits": 90, "cache_misses": 10}
        with patch("tokenpak.alerts._get_proxy_cache_stats", return_value=cache_stats):
            fired, value = evaluate_rule(rule, stats={}, health={})
        assert fired is False

    def test_cache_hit_rate_zero_division_safe(self):
        rule = _make_rule(condition="cache_hit_rate < 0.80")
        with patch("tokenpak.alerts._get_proxy_cache_stats", return_value={"cache_hits": 0, "cache_misses": 0}):
            fired, value = evaluate_rule(rule, stats={}, health={})
        # 0/0 → 0.0 hit rate, which is < 80%, so fires
        assert fired is True
        assert value == pytest.approx(0.0)

    # --- error_rate ---

    def test_error_rate_above_threshold_fires(self):
        rule = _make_rule(condition="error_rate > 0.05")
        stats = {"requests": 100, "errors": 10}
        fired, value = evaluate_rule(rule, stats=stats, health={})
        assert fired is True
        assert value == pytest.approx(10.0)

    def test_error_rate_below_threshold_does_not_fire(self):
        rule = _make_rule(condition="error_rate > 0.05")
        stats = {"requests": 100, "errors": 2}
        fired, value = evaluate_rule(rule, stats=stats, health={})
        assert fired is False

    def test_error_rate_zero_requests_safe(self):
        rule = _make_rule(condition="error_rate > 0.05")
        stats = {"requests": 0, "errors": 0}
        fired, value = evaluate_rule(rule, stats=stats, health={})
        assert fired is False
        assert value == pytest.approx(0.0)

    # --- health ---

    def test_health_not_ok_fires(self):
        rule = _make_rule(condition="health != 'ok'")
        fired, value = evaluate_rule(rule, stats={}, health={"status": "error"})
        assert fired is True
        assert value is None

    def test_health_ok_does_not_fire(self):
        rule = _make_rule(condition="health != 'ok'")
        fired, value = evaluate_rule(rule, stats={}, health={"status": "ok"})
        assert fired is False

    def test_health_missing_status_fires(self):
        rule = _make_rule(condition="health != 'ok'")
        # Missing status → defaults to "unknown" which != "ok"
        fired, value = evaluate_rule(rule, stats={}, health={})
        assert fired is True

    # --- unknown condition ---

    def test_unknown_condition_returns_no_fire(self):
        rule = _make_rule(condition="some_unknown_metric > 999")
        fired, value = evaluate_rule(rule, stats={}, health={})
        assert fired is False
        assert value is None


# ---------------------------------------------------------------------------
# check_alerts — orchestration
# ---------------------------------------------------------------------------


class TestCheckAlerts:
    """Tests for the check_alerts() orchestration function."""

    def _patch_all_io(self, tmp_path, stats=None, health=None, cache_stats=None, config=None):
        """Helper to patch all external I/O for check_alerts."""
        state_file = tmp_path / "alert_state.json"
        patches = [
            patch("tokenpak.alerts._get_state_path", return_value=state_file),
            patch("tokenpak.alerts._get_proxy_stats", return_value=stats or {}),
            patch("tokenpak.alerts._get_proxy_health", return_value=health or {"status": "ok"}),
            patch("tokenpak.alerts._get_proxy_cache_stats", return_value=cache_stats or {}),
        ]
        if config is not None:
            patches.append(patch("tokenpak.alerts.load_config", return_value=config))
        return patches

    def test_returns_list(self, tmp_path):
        patches = self._patch_all_io(tmp_path)
        with patch("tokenpak.alerts.load_config", return_value={"enabled": True, "rules": []}):
            for p in patches:
                p.start()
            try:
                result = check_alerts()
                assert isinstance(result, list)
            finally:
                for p in patches:
                    p.stop()

    def test_disabled_alerts_returns_empty(self, tmp_path):
        state_file = tmp_path / "alert_state.json"
        with patch("tokenpak.alerts._get_state_path", return_value=state_file), \
             patch("tokenpak.alerts.load_config", return_value={"enabled": False}):
            result = check_alerts()
            assert result == []

    def test_alert_fires_when_threshold_breached(self, tmp_path):
        state_file = tmp_path / "alert_state.json"
        config = {
            "enabled": True,
            "rules": [asdict(_make_rule(name="err_spike", condition="error_rate > 0.05", cooldown_minutes=30))],
        }
        with patch("tokenpak.alerts._get_state_path", return_value=state_file), \
             patch("tokenpak.alerts.load_config", return_value=config), \
             patch("tokenpak.alerts._get_proxy_stats", return_value={"requests": 100, "errors": 10}), \
             patch("tokenpak.alerts._get_proxy_health", return_value={"status": "ok"}), \
             patch("tokenpak.alerts._get_proxy_cache_stats", return_value={}):
            result = check_alerts()
        assert len(result) == 1
        rule, value = result[0]
        assert rule.name == "err_spike"
        assert value == pytest.approx(10.0)

    def test_cooldown_prevents_repeat_fire(self, tmp_path):
        state_file = tmp_path / "alert_state.json"
        # Pre-populate state with recent fire
        recent_state = {
            "err_spike": {
                "name": "err_spike",
                "last_fired": time.time() - 60,  # fired 1 min ago
                "last_value": 10.0,
                "fired_count": 1,
            }
        }
        state_file.write_text(json.dumps(recent_state))
        config = {
            "enabled": True,
            "rules": [asdict(_make_rule(name="err_spike", condition="error_rate > 0.05", cooldown_minutes=30))],
        }
        with patch("tokenpak.alerts._get_state_path", return_value=state_file), \
             patch("tokenpak.alerts.load_config", return_value=config), \
             patch("tokenpak.alerts._get_proxy_stats", return_value={"requests": 100, "errors": 10}), \
             patch("tokenpak.alerts._get_proxy_health", return_value={"status": "ok"}), \
             patch("tokenpak.alerts._get_proxy_cache_stats", return_value={}):
            result = check_alerts()
        assert result == []

    def test_state_persisted_after_fire(self, tmp_path):
        state_file = tmp_path / "alert_state.json"
        config = {
            "enabled": True,
            "rules": [asdict(_make_rule(name="err_spike", condition="error_rate > 0.05", cooldown_minutes=30))],
        }
        with patch("tokenpak.alerts._get_state_path", return_value=state_file), \
             patch("tokenpak.alerts.load_config", return_value=config), \
             patch("tokenpak.alerts._get_proxy_stats", return_value={"requests": 100, "errors": 10}), \
             patch("tokenpak.alerts._get_proxy_health", return_value={"status": "ok"}), \
             patch("tokenpak.alerts._get_proxy_cache_stats", return_value={}):
            check_alerts()

        persisted = json.loads(state_file.read_text())
        assert "err_spike" in persisted
        assert persisted["err_spike"]["fired_count"] == 1

    def test_no_rules_uses_defaults(self, tmp_path):
        """When rules list is empty, check_alerts falls back to built-in default rules."""
        state_file = tmp_path / "alert_state.json"
        config = {"enabled": True, "rules": []}
        # Proxy is healthy and error-free → defaults should NOT fire
        with patch("tokenpak.alerts._get_state_path", return_value=state_file), \
             patch("tokenpak.alerts.load_config", return_value=config), \
             patch("tokenpak.alerts._get_proxy_stats", return_value={"requests": 100, "errors": 0}), \
             patch("tokenpak.alerts._get_proxy_health", return_value={"status": "ok"}), \
             patch("tokenpak.alerts._get_proxy_cache_stats", return_value={"cache_hits": 95, "cache_misses": 5}):
            result = check_alerts()
        # All default thresholds should be satisfied (no alerts)
        assert result == []
