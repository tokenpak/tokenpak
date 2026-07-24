"""Unit tests for tokenpak/config_loader.py"""

import os
from unittest.mock import patch

import pytest

# Reset module-level cache before each test
import tokenpak.core.config_loader as _cl


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset the module-level _config cache between tests."""
    _cl._config = None
    yield
    _cl._config = None


# ---------------------------------------------------------------------------
# _deep_get helper
# ---------------------------------------------------------------------------


class TestDeepGet:
    def test_top_level_key(self):
        d = {"port": 8766}
        assert _cl._deep_get(d, "port") == 8766

    def test_nested_key(self):
        d = {"compression": {"enabled": True}}
        assert _cl._deep_get(d, "compression.enabled") is True

    def test_missing_key_returns_default(self):
        d = {"port": 8766}
        assert _cl._deep_get(d, "missing") is None

    def test_missing_nested_key_returns_default(self):
        d = {"compression": {}}
        assert _cl._deep_get(d, "compression.missing", "fallback") == "fallback"

    def test_non_dict_intermediate(self):
        d = {"compression": "not_a_dict"}
        assert _cl._deep_get(d, "compression.enabled") is None

    def test_three_level_nesting(self):
        d = {"a": {"b": {"c": 42}}}
        assert _cl._deep_get(d, "a.b.c") == 42


# ---------------------------------------------------------------------------
# _bool_env helper
# ---------------------------------------------------------------------------


class TestBoolEnv:
    def test_true_values(self):
        for val in ("1", "true", "yes", "on", "True", "YES", "ON"):
            assert _cl._bool_env(val) is True

    def test_false_values(self):
        for val in ("0", "false", "no", "off", "False", "NO"):
            assert _cl._bool_env(val) is False


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_empty_dict_for_missing_file(self, tmp_path):
        nonexistent = str(tmp_path / "nofile.yaml")
        result = _cl.load_config(nonexistent)
        assert result == {}

    def test_loads_valid_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("port: 9999\nmode: strict\n")
        result = _cl.load_config(str(cfg_file))
        assert result["port"] == 9999
        assert result["mode"] == "strict"

    def test_loads_nested_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("compression:\n  enabled: false\n  max_chars: 50\n")
        result = _cl.load_config(str(cfg_file))
        assert result["compression"]["enabled"] is False
        assert result["compression"]["max_chars"] == 50

    def test_malformed_yaml_returns_empty_dict(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("}{invalid yaml{{")
        result = _cl.load_config(str(cfg_file))
        assert result == {}

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        result = _cl.load_config(str(cfg_file))
        assert result == {}

    def test_caches_result_for_default_path(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("port: 1234\n")
        with patch.object(_cl, "CONFIG_PATH", cfg_file):
            r1 = _cl.load_config()
            r2 = _cl.load_config()
            assert r1 is r2  # same object (cached)

    def test_explicit_path_bypasses_cache(self, tmp_path):
        f1 = tmp_path / "a.yaml"
        f2 = tmp_path / "b.yaml"
        f1.write_text("port: 1111\n")
        f2.write_text("port: 2222\n")
        r1 = _cl.load_config(str(f1))
        _cl._config = None
        r2 = _cl.load_config(str(f2))
        assert r1["port"] == 1111
        assert r2["port"] == 2222


# ---------------------------------------------------------------------------
# get — priority order
# ---------------------------------------------------------------------------


class TestGet:
    def test_env_var_takes_priority(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("port: 8766\n")
        _cl.load_config(str(cfg_file))
        with patch.dict(os.environ, {"MY_PORT": "9999"}):
            result = _cl.get("port", default=8766, env_var="MY_PORT", cast=int)
        assert result == 9999

    def test_config_file_used_when_no_env(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("port: 7777\n")
        _cl.load_config(str(cfg_file))
        result = _cl.get("port", default=8766)
        assert result == 7777

    def test_default_used_when_no_env_no_file(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        result = _cl.get("port", default=1234)
        assert result == 1234

    def test_cast_int(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        with patch.dict(os.environ, {"MY_VAL": "42"}):
            result = _cl.get("anything", env_var="MY_VAL", cast=int)
        assert result == 42
        assert isinstance(result, int)

    def test_cast_bool_from_env(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        with patch.dict(os.environ, {"MY_FLAG": "true"}):
            result = _cl.get("anything", env_var="MY_FLAG", cast=bool)
        assert result is True

    def test_cast_float_from_env(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        with patch.dict(os.environ, {"MY_FLOAT": "3.14"}):
            result = _cl.get("anything", env_var="MY_FLOAT", cast=float)
        assert abs(result - 3.14) < 0.001

    def test_nested_key_from_config(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("compression:\n  enabled: false\n")
        _cl.load_config(str(cfg_file))
        result = _cl.get("compression.enabled", default=True)
        assert result is False


# ---------------------------------------------------------------------------
# get_all — defaults and structure
# ---------------------------------------------------------------------------


class TestGetAll:
    def test_returns_dict(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        result = _cl.get_all()
        assert isinstance(result, dict)

    def test_contains_core_keys(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        result = _cl.get_all()
        for key in ("port", "mode", "compression.enabled", "compression.threshold_tokens"):
            assert key in result

    def test_default_port_is_8766(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        result = _cl.get_all()
        assert result["port"] == 8766

    def test_default_mode_is_hybrid(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        result = _cl.get_all()
        assert result["mode"] == "hybrid"

    def test_feature_keys_present(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        result = _cl.get_all()
        assert "features.skeleton" in result
        assert "features.router" in result
        assert "features.budget_controller" in result

    def test_env_override_applied(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        with patch.dict(os.environ, {"TOKENPAK_PORT": "9000"}):
            result = _cl.get_all()
        assert result["port"] == 9000

    def test_compression_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("")
        _cl.load_config(str(cfg_file))
        result = _cl.get_all()
        assert result["compression.enabled"] is True
        assert result["compression.threshold_tokens"] == 1500


# ---------------------------------------------------------------------------
# generate_default_yaml
# ---------------------------------------------------------------------------


class TestGenerateDefaultYaml:
    def test_returns_string(self):
        result = _cl.generate_default_yaml()
        assert isinstance(result, str)

    def test_contains_port(self):
        result = _cl.generate_default_yaml()
        assert "port: 8766" in result

    def test_contains_compression_section(self):
        result = _cl.generate_default_yaml()
        assert "compression:" in result

    def test_contains_features_section(self):
        result = _cl.generate_default_yaml()
        assert "features:" in result

    def test_is_valid_yaml(self):
        import yaml

        result = _cl.generate_default_yaml()
        parsed = yaml.safe_load(result)
        assert isinstance(parsed, dict)
        assert parsed["port"] == 8766
