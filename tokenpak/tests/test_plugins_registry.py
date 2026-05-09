"""Unit tests for tokenpak/plugins/registry.py — PluginRegistry."""

import json
import logging
import os
from unittest.mock import patch

import pytest

from tokenpak.plugins.base import CompressorPlugin
from tokenpak.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Concrete plugin fixtures
# ---------------------------------------------------------------------------

class AlphaPlugin(CompressorPlugin):
    name = "alpha"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text, "metadata": {"plugin": "alpha"}}

    def priority(self) -> int:
        return 10


class BetaPlugin(CompressorPlugin):
    name = "beta"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text, "metadata": {"plugin": "beta"}}

    def priority(self) -> int:
        return 20


class GammaPlugin(CompressorPlugin):
    name = "gamma"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text, "metadata": {"plugin": "gamma"}}

    def priority(self) -> int:
        return 15


class UnnamedPlugin(CompressorPlugin):
    """Plugin whose name class attribute is empty — falls back to class name."""
    name = ""

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text, "metadata": {}}


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

class TestPluginRegistryInit:
    def test_empty_on_init(self):
        reg = PluginRegistry()
        assert reg.get_plugins() == []

    def test_names_set_empty_on_init(self):
        reg = PluginRegistry()
        assert len(reg._names) == 0

    def test_plugins_list_empty_on_init(self):
        reg = PluginRegistry()
        assert len(reg._plugins) == 0

    def test_multiple_registries_are_independent(self):
        reg1 = PluginRegistry()
        reg2 = PluginRegistry()
        reg1.register(AlphaPlugin)
        assert len(reg1.get_plugins()) == 1
        assert len(reg2.get_plugins()) == 0


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------

class TestPluginRegistryRegister:
    def test_register_single_plugin(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        plugins = reg.get_plugins()
        assert len(plugins) == 1

    def test_register_returns_instance(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        plugins = reg.get_plugins()
        assert isinstance(plugins[0], AlphaPlugin)

    def test_register_multiple_plugins(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        reg.register(BetaPlugin)
        assert len(reg.get_plugins()) == 2

    def test_register_name_collision_raises(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        with pytest.raises(ValueError, match="alpha"):
            reg.register(AlphaPlugin)

    def test_register_unnamed_plugin_uses_class_name(self):
        reg = PluginRegistry()
        reg.register(UnnamedPlugin)
        assert "UnnamedPlugin" in reg._names

    def test_register_adds_to_names_set(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        assert "alpha" in reg._names

    def test_register_different_names_no_collision(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        reg.register(BetaPlugin)
        assert "alpha" in reg._names
        assert "beta" in reg._names


# ---------------------------------------------------------------------------
# get_plugins() — ordering
# ---------------------------------------------------------------------------

class TestGetPluginsSorting:
    def test_single_plugin_returned(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        result = reg.get_plugins()
        assert len(result) == 1
        assert isinstance(result[0], AlphaPlugin)

    def test_sorted_highest_priority_first(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)   # priority 10
        reg.register(BetaPlugin)    # priority 20
        reg.register(GammaPlugin)   # priority 15
        result = reg.get_plugins()
        priorities = [p.priority() for p in result]
        assert priorities == sorted(priorities, reverse=True)

    def test_sorted_order_is_beta_gamma_alpha(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)   # 10
        reg.register(BetaPlugin)    # 20
        reg.register(GammaPlugin)   # 15
        result = reg.get_plugins()
        assert isinstance(result[0], BetaPlugin)
        assert isinstance(result[1], GammaPlugin)
        assert isinstance(result[2], AlphaPlugin)

    def test_get_plugins_does_not_mutate_internal_list(self):
        reg = PluginRegistry()
        reg.register(AlphaPlugin)
        first = reg.get_plugins()
        second = reg.get_plugins()
        assert first == second


# ---------------------------------------------------------------------------
# _load_plugin_path()
# ---------------------------------------------------------------------------

class TestLoadPluginPath:
    def test_load_valid_path(self):
        reg = PluginRegistry()
        reg._load_plugin_path("tokenpak.plugins.examples.passthrough.PassthroughPlugin")
        assert len(reg.get_plugins()) == 1

    def test_load_empty_string_noop(self):
        reg = PluginRegistry()
        reg._load_plugin_path("")
        assert reg.get_plugins() == []

    def test_load_whitespace_string_noop(self):
        reg = PluginRegistry()
        reg._load_plugin_path("   ")
        assert reg.get_plugins() == []

    def test_load_bad_module_warns_and_skips(self, caplog):
        reg = PluginRegistry()
        with caplog.at_level(logging.WARNING, logger="tokenpak.plugins.registry"):
            reg._load_plugin_path("nonexistent.module.FakePlugin")
        assert reg.get_plugins() == []

    def test_load_non_plugin_class_warns_and_skips(self, caplog):
        """Path points to a real class but not a CompressorPlugin subclass."""
        reg = PluginRegistry()
        with caplog.at_level(logging.WARNING, logger="tokenpak.plugins.registry"):
            reg._load_plugin_path("pathlib.Path")
        assert reg.get_plugins() == []

    def test_load_missing_class_in_module_warns(self, caplog):
        reg = PluginRegistry()
        with caplog.at_level(logging.WARNING, logger="tokenpak.plugins.registry"):
            reg._load_plugin_path("tokenpak.plugins.base.NonExistentClass")
        assert reg.get_plugins() == []

    def test_load_strips_whitespace_from_path(self):
        reg = PluginRegistry()
        reg._load_plugin_path("  tokenpak.plugins.examples.passthrough.PassthroughPlugin  ")
        assert len(reg.get_plugins()) == 1


# ---------------------------------------------------------------------------
# _discover_from_env()
# ---------------------------------------------------------------------------

class TestDiscoverFromEnv:
    def test_empty_env_var_noop(self):
        reg = PluginRegistry()
        with patch.dict(os.environ, {"TOKENPAK_PLUGINS": ""}):
            reg._discover_from_env()
        assert reg.get_plugins() == []

    def test_env_var_not_set_noop(self):
        reg = PluginRegistry()
        env = {k: v for k, v in os.environ.items() if k != "TOKENPAK_PLUGINS"}
        with patch.dict(os.environ, env, clear=True):
            reg._discover_from_env()
        assert reg.get_plugins() == []

    def test_single_plugin_via_env(self):
        reg = PluginRegistry()
        with patch.dict(os.environ, {
            "TOKENPAK_PLUGINS": "tokenpak.plugins.examples.passthrough.PassthroughPlugin"
        }):
            reg._discover_from_env()
        assert len(reg.get_plugins()) == 1

    def test_multiple_plugins_via_env_comma_separated(self):
        reg = PluginRegistry()
        # Use passthrough twice would collision — only use it once with a mock
        with patch.dict(os.environ, {
            "TOKENPAK_PLUGINS": "tokenpak.plugins.examples.passthrough.PassthroughPlugin"
        }):
            reg._discover_from_env()
        assert len(reg.get_plugins()) == 1

    def test_bad_path_in_env_skips_gracefully(self, caplog):
        reg = PluginRegistry()
        with patch.dict(os.environ, {"TOKENPAK_PLUGINS": "bad.module.PluginX"}):
            with caplog.at_level(logging.WARNING, logger="tokenpak.plugins.registry"):
                reg._discover_from_env()
        assert reg.get_plugins() == []

    def test_env_with_spaces_around_entries_handled(self):
        reg = PluginRegistry()
        with patch.dict(os.environ, {
            "TOKENPAK_PLUGINS": " tokenpak.plugins.examples.passthrough.PassthroughPlugin "
        }):
            reg._discover_from_env()
        assert len(reg.get_plugins()) == 1


# ---------------------------------------------------------------------------
# _discover_from_config() — canonical config.yaml path
# ---------------------------------------------------------------------------

class TestDiscoverFromConfigYaml:
    def test_config_yaml_plugins_loaded(self):
        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get") as mock_get:
            mock_get.return_value = [
                "tokenpak.plugins.examples.passthrough.PassthroughPlugin"
            ]
            reg._discover_from_config()
        assert len(reg.get_plugins()) == 1

    def test_config_yaml_empty_list_noop(self, tmp_path):
        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get") as mock_get:
            mock_get.return_value = []
            reg._discover_from_config()
        assert reg.get_plugins() == []

    def test_config_yaml_exception_falls_through(self, tmp_path, caplog):
        """If config_get raises, registry logs and moves on (no crash)."""
        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get", side_effect=RuntimeError("boom")):
            with patch("pathlib.Path.exists", return_value=False):
                with caplog.at_level(logging.DEBUG, logger="tokenpak.plugins.registry"):
                    reg._discover_from_config()
        assert reg.get_plugins() == []


# ---------------------------------------------------------------------------
# _discover_from_config() — legacy JSON fallback
# ---------------------------------------------------------------------------

class TestDiscoverFromConfigLegacyJson:
    def test_legacy_json_loads_plugins(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = {"plugins": ["tokenpak.plugins.examples.passthrough.PassthroughPlugin"]}
        (tmp_path / "tokenpak.config.json").write_text(json.dumps(cfg))

        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get") as mock_get:
            mock_get.return_value = []  # canonical list is empty → fall to legacy
            reg._discover_from_config()

        assert len(reg.get_plugins()) == 1

    def test_legacy_json_emits_deprecation_warning(self, tmp_path, monkeypatch, caplog):
        monkeypatch.chdir(tmp_path)
        cfg = {"plugins": ["tokenpak.plugins.examples.passthrough.PassthroughPlugin"]}
        (tmp_path / "tokenpak.config.json").write_text(json.dumps(cfg))

        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get") as mock_get:
            mock_get.return_value = []
            with caplog.at_level(logging.WARNING, logger="tokenpak.plugins.registry"):
                reg._discover_from_config()

        assert "deprecated" in caplog.text.lower()

    def test_legacy_json_missing_plugins_key_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "tokenpak.config.json").write_text(json.dumps({}))

        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get") as mock_get:
            mock_get.return_value = []
            reg._discover_from_config()

        assert reg.get_plugins() == []

    def test_legacy_json_malformed_warns_and_skips(self, tmp_path, monkeypatch, caplog):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "tokenpak.config.json").write_text("NOT VALID JSON {{")

        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get") as mock_get:
            mock_get.return_value = []
            with caplog.at_level(logging.WARNING, logger="tokenpak.plugins.registry"):
                reg._discover_from_config()

        assert reg.get_plugins() == []

    def test_no_legacy_file_and_empty_config_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        reg = PluginRegistry()
        with patch("tokenpak.core.config_loader.get") as mock_get:
            mock_get.return_value = []
            reg._discover_from_config()
        assert reg.get_plugins() == []


# ---------------------------------------------------------------------------
# discover() — integration of env + config
# ---------------------------------------------------------------------------

class TestDiscover:
    def test_discover_calls_both_sources(self):
        reg = PluginRegistry()
        with patch.object(reg, "_discover_from_env") as mock_env, \
             patch.object(reg, "_discover_from_config") as mock_cfg:
            reg.discover()
        mock_env.assert_called_once()
        mock_cfg.assert_called_once()
