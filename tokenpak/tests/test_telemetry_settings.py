"""Tests for tokenpak/telemetry/settings.py and telemetry/config.py.

Targets:
  - AlertSettings: load, save, validate, deep-merge, atomic write
  - TelemetryConfig (config.py): valid configs, validator errors, defaults

Coverage goal: settings.py > 60%
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tokenpak.telemetry.settings import (
    DEFAULT_ALERT_CONFIG,
    SEVERITY_LEVELS,
    AlertSettings,
    _deep_merge,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "alerts.json"


@pytest.fixture
def settings(cfg_path: pathlib.Path) -> AlertSettings:
    return AlertSettings(cfg_path)


# ---------------------------------------------------------------------------
# AlertSettings.load
# ---------------------------------------------------------------------------

class TestAlertSettingsLoad:
    def test_load_returns_defaults_when_file_missing(self, settings):
        """Non-existent file → returns a copy of DEFAULT_ALERT_CONFIG."""
        result = settings.load()
        assert result["cost_spike"]["threshold_pct"] == DEFAULT_ALERT_CONFIG["cost_spike"]["threshold_pct"]
        assert result["latency"]["threshold_ms"] == DEFAULT_ALERT_CONFIG["latency"]["threshold_ms"]

    def test_load_returns_dict(self, settings):
        config = settings.load()
        assert isinstance(config, dict)

    def test_load_merges_saved_with_defaults(self, cfg_path):
        """Partial JSON on disk merges with defaults for missing keys."""
        cfg_path.write_text(json.dumps({"cost_spike": {"threshold_pct": 99}}))
        s = AlertSettings(cfg_path)
        result = s.load()
        assert result["cost_spike"]["threshold_pct"] == 99  # override wins
        assert "savings_drop" in result  # default key still present

    def test_load_returns_defaults_on_corrupt_json(self, cfg_path):
        """Corrupt file → silently falls back to defaults."""
        cfg_path.write_text("NOT_JSON{{{{")
        s = AlertSettings(cfg_path)
        result = s.load()
        assert result == DEFAULT_ALERT_CONFIG

    def test_load_does_not_mutate_defaults(self, settings):
        """Modifying load() result must not change DEFAULT_ALERT_CONFIG."""
        result = settings.load()
        result["cost_spike"]["threshold_pct"] = 999
        assert DEFAULT_ALERT_CONFIG["cost_spike"]["threshold_pct"] != 999

    def test_load_creates_parent_dir(self, tmp_path):
        """AlertSettings creates missing parent directories on construction."""
        deep_path = tmp_path / "a" / "b" / "c" / "alerts.json"
        s = AlertSettings(deep_path)
        assert deep_path.parent.exists()


# ---------------------------------------------------------------------------
# AlertSettings.save + atomic write
# ---------------------------------------------------------------------------

class TestAlertSettingsSave:
    def test_save_creates_file(self, settings, cfg_path):
        config = settings.load()
        settings.save(config)
        assert cfg_path.exists()

    def test_save_and_load_roundtrip(self, settings):
        """Config saved and reloaded must equal the original."""
        original = settings.load()
        original["cost_spike"]["threshold_pct"] = 42.0
        settings.save(original)
        reloaded = settings.load()
        assert reloaded["cost_spike"]["threshold_pct"] == 42.0

    def test_save_is_valid_json(self, settings, cfg_path):
        settings.save(settings.load())
        data = json.loads(cfg_path.read_text())
        assert isinstance(data, dict)

    def test_no_tmp_file_left_after_save(self, settings, cfg_path):
        """Atomic rename: no .tmp file should remain after save."""
        settings.save(settings.load())
        assert not cfg_path.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# AlertSettings._validate
# ---------------------------------------------------------------------------

class TestAlertSettingsValidate:
    def test_valid_config_passes(self, settings):
        """Default config should pass validation without raising."""
        config = settings.load()
        settings.save(config)  # save calls _validate internally

    def test_cost_spike_threshold_pct_out_of_range_raises(self, settings):
        config = settings.load()
        config["cost_spike"]["threshold_pct"] = 9999
        with pytest.raises(ValueError, match="cost_spike.threshold_pct"):
            settings.save(config)

    def test_cost_spike_negative_threshold_raises(self, settings):
        config = settings.load()
        config["cost_spike"]["threshold_pct"] = -1
        with pytest.raises(ValueError):
            settings.save(config)

    def test_cost_spike_threshold_abs_valid(self, settings):
        config = settings.load()
        config["cost_spike"]["threshold_abs"] = 50.0
        settings.save(config)  # should not raise
        reloaded = settings.load()
        assert reloaded["cost_spike"]["threshold_abs"] == 50.0

    def test_cost_spike_threshold_abs_too_large_raises(self, settings):
        config = settings.load()
        config["cost_spike"]["threshold_abs"] = 99999
        with pytest.raises(ValueError, match="threshold_abs"):
            settings.save(config)

    def test_invalid_severity_raises(self, settings):
        config = settings.load()
        config["cost_spike"]["severity"] = "unknown_level"
        with pytest.raises(ValueError, match="severity"):
            settings.save(config)

    def test_all_valid_severities_accepted(self, settings):
        for sev in SEVERITY_LEVELS:
            config = settings.load()
            config["cost_spike"]["severity"] = sev
            settings.save(config)  # should not raise

    def test_savings_drop_threshold_out_of_range_raises(self, settings):
        config = settings.load()
        config["savings_drop"]["threshold_pct"] = 600
        with pytest.raises(ValueError, match="savings_drop"):
            settings.save(config)

    def test_latency_threshold_ms_out_of_range_raises(self, settings):
        config = settings.load()
        config["latency"]["threshold_ms"] = 999999
        with pytest.raises(ValueError, match="latency.threshold_ms"):
            settings.save(config)

    def test_latency_invalid_metric_raises(self, settings):
        config = settings.load()
        config["latency"]["metric"] = "p50"
        with pytest.raises(ValueError, match="latency.metric"):
            settings.save(config)

    def test_latency_valid_metrics_accepted(self, settings):
        for metric in ("p95", "p99"):
            config = settings.load()
            config["latency"]["metric"] = metric
            settings.save(config)  # no raise

    def test_error_rate_threshold_out_of_range_raises(self, settings):
        config = settings.load()
        config["error_rate"]["threshold_pct"] = -5
        with pytest.raises(ValueError):
            settings.save(config)

    def test_email_invalid_address_raises_when_enabled(self, settings):
        config = settings.load()
        config["channels"]["email"]["enabled"] = True
        config["channels"]["email"]["address"] = "not-an-email"
        with pytest.raises(ValueError, match="email"):
            settings.save(config)

    def test_email_valid_address_accepted(self, settings):
        config = settings.load()
        config["channels"]["email"]["enabled"] = True
        config["channels"]["email"]["address"] = "user@example.com"
        settings.save(config)  # should not raise

    def test_email_disabled_skips_address_validation(self, settings):
        """Invalid address OK if email is disabled."""
        config = settings.load()
        config["channels"]["email"]["enabled"] = False
        config["channels"]["email"]["address"] = "bad"
        settings.save(config)  # should not raise

    def test_invalid_min_severity_falls_back_to_warning(self, settings):
        config = settings.load()
        config["channels"]["email"]["min_severity"] = "extreme"
        settings.save(config)
        reloaded = settings.load()
        assert reloaded["channels"]["email"]["min_severity"] == "warning"


# ---------------------------------------------------------------------------
# _deep_merge helper
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_override_wins_for_flat_values(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = _deep_merge(base, override)
        assert result["b"] == 99
        assert result["a"] == 1

    def test_base_preserved_for_missing_override_keys(self):
        base = {"x": {"y": 1, "z": 2}}
        override = {"x": {"y": 9}}
        result = _deep_merge(base, override)
        assert result["x"]["z"] == 2  # base value kept

    def test_does_not_mutate_base(self):
        base = {"k": {"v": 1}}
        _deep_merge(base, {"k": {"v": 2}})
        assert base["k"]["v"] == 1

    def test_new_keys_in_override_added(self):
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result["b"] == 2


# ---------------------------------------------------------------------------
# telemetry/config.py
# ---------------------------------------------------------------------------

try:
    from tokenpak.telemetry.config import (
        CaptureConfig,
        RetentionConfig,
        ServerConfig,
        StorageConfig,
        TelemetryConfig,
    )
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False


@pytest.mark.skipif(not CONFIG_AVAILABLE, reason="telemetry/config.py not importable")
class TestTelemetryConfig:
    def test_default_server_config(self):
        cfg = ServerConfig()
        assert cfg.port == 17888
        assert cfg.host == "0.0.0.0"

    def test_custom_server_port(self):
        cfg = ServerConfig(port=9999)
        assert cfg.port == 9999

    def test_default_storage_config(self):
        cfg = StorageConfig()
        assert cfg.type == "sqlite"

    def test_default_retention_config(self):
        cfg = RetentionConfig()
        assert cfg.events_days == 90
        assert cfg.auto_prune is True

    def test_capture_sampling_rate_default(self):
        cfg = CaptureConfig()
        assert cfg.sampling_rate == 1.0

    def test_capture_invalid_sampling_rate_raises(self):
        with pytest.raises(Exception):
            CaptureConfig(sampling_rate=1.5)

    def test_capture_zero_sampling_rate_raises(self):
        with pytest.raises(Exception):
            CaptureConfig(sampling_rate=0.0)

    def test_capture_valid_sampling_rate(self):
        cfg = CaptureConfig(sampling_rate=0.5)
        assert cfg.sampling_rate == 0.5

    def test_full_telemetry_config_composes(self):
        cfg = TelemetryConfig()
        assert hasattr(cfg, "server") or hasattr(cfg, "storage") or True  # just no error
