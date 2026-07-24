"""Tests for the TokenPak plugin system."""

import json
import logging
import sys
import textwrap

import pytest

from tokenpak.plugins.base import CompressorPlugin
from tokenpak.plugins.registry import PluginRegistry

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class UpperPlugin(CompressorPlugin):
    name = "upper"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text.upper(), "metadata": {"plugin": self.name}}


class LowerPlugin(CompressorPlugin):
    name = "lower"

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text.lower(), "metadata": {"plugin": self.name}}


class HighPriorityPlugin(CompressorPlugin):
    name = "high"

    def priority(self) -> int:
        return 100

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text + " [high]", "metadata": {"plugin": self.name}}


class LowPriorityPlugin(CompressorPlugin):
    name = "low"

    def priority(self) -> int:
        return 10

    def compress(self, text: str, context: dict) -> dict:
        return {"text": text + " [low]", "metadata": {"plugin": self.name}}


class ExplodingPlugin(CompressorPlugin):
    name = "exploding"

    def compress(self, text: str, context: dict) -> dict:
        raise RuntimeError("boom")


@pytest.fixture()
def registry():
    return PluginRegistry()


# ---------------------------------------------------------------------------
# 1. Plugin registered and called
# ---------------------------------------------------------------------------


def test_plugin_registered_and_called(registry):
    registry.register(UpperPlugin)
    plugins = registry.get_plugins()
    assert len(plugins) == 1
    result = plugins[0].compress("hello", {})
    assert result["text"] == "HELLO"
    assert "metadata" in result


# ---------------------------------------------------------------------------
# 2. Priority ordering correct (higher first)
# ---------------------------------------------------------------------------


def test_priority_ordering(registry):
    registry.register(LowPriorityPlugin)
    registry.register(HighPriorityPlugin)
    ordered = registry.get_plugins()
    assert ordered[0].name == "high"
    assert ordered[1].name == "low"


# ---------------------------------------------------------------------------
# 3. Invalid plugin path → warning logged, no crash
# ---------------------------------------------------------------------------


def test_invalid_plugin_path_no_crash(registry, caplog):
    with caplog.at_level(logging.WARNING, logger="tokenpak.plugins.registry"):
        registry._load_plugin_path("nonexistent.module.FakePlugin")
    assert len(registry.get_plugins()) == 0
    assert any("nonexistent.module.FakePlugin" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# 4. Multiple plugins run in order
# ---------------------------------------------------------------------------


def test_multiple_plugins_run_in_order(registry):
    registry.register(HighPriorityPlugin)
    registry.register(LowPriorityPlugin)
    text = "start"
    for plugin in registry.get_plugins():
        result = plugin.compress(text, {})
        text = result["text"]
    assert text == "start [high] [low]"


# ---------------------------------------------------------------------------
# 5. Plugin result used in final compression (pipeline simulation)
# ---------------------------------------------------------------------------


def test_plugin_result_used_in_pipeline(registry):
    registry.register(UpperPlugin)
    text = "hello world"
    for plugin in registry.get_plugins():
        out = plugin.compress(text, {"mode": "hybrid"})
        text = out["text"]
    assert text == "HELLO WORLD"


# ---------------------------------------------------------------------------
# 6. Plugin exception → caught, fallback gracefully
# ---------------------------------------------------------------------------


def test_plugin_exception_caught_with_fallback(registry):
    registry.register(ExplodingPlugin)
    original_text = "safe text"
    text = original_text
    for plugin in registry.get_plugins():
        try:
            result = plugin.compress(text, {})
            text = result["text"]
        except Exception:
            pass  # graceful fallback — keep original
    assert text == original_text


# ---------------------------------------------------------------------------
# 7. Empty plugin list → works fine
# ---------------------------------------------------------------------------


def test_empty_plugin_list(registry):
    assert registry.get_plugins() == []
    # Running pipeline on empty list should be a no-op
    text = "unchanged"
    for plugin in registry.get_plugins():
        result = plugin.compress(text, {})
        text = result["text"]
    assert text == "unchanged"


# ---------------------------------------------------------------------------
# 8. Plugin name collision → raises ValueError
# ---------------------------------------------------------------------------


def test_plugin_name_collision_raises(registry):
    registry.register(UpperPlugin)
    with pytest.raises(ValueError, match="upper"):
        registry.register(UpperPlugin)


# ---------------------------------------------------------------------------
# 9. Env var discovery works
# ---------------------------------------------------------------------------


def test_env_var_discovery(registry, monkeypatch):
    monkeypatch.setenv(
        "TOKENPAK_PLUGINS",
        "tokenpak.plugins.examples.passthrough.PassthroughPlugin",
    )
    registry._discover_from_env()
    plugins = registry.get_plugins()
    assert len(plugins) == 1
    assert plugins[0].name == "passthrough"
    result = plugins[0].compress("hello", {})
    assert result["text"] == "hello"


# ---------------------------------------------------------------------------
# 10. Config file discovery works
# ---------------------------------------------------------------------------


def test_config_file_discovery(registry, tmp_path, monkeypatch):
    config = {"plugins": ["tokenpak.plugins.examples.passthrough.PassthroughPlugin"]}
    config_file = tmp_path / "tokenpak.config.json"
    config_file.write_text(json.dumps(config))

    monkeypatch.chdir(tmp_path)
    registry._discover_from_config()

    plugins = registry.get_plugins()
    assert len(plugins) == 1
    assert plugins[0].name == "passthrough"


# ---------------------------------------------------------------------------
# 11. PassthroughPlugin returns text unchanged
# ---------------------------------------------------------------------------


def test_passthrough_plugin_noop():
    from tokenpak.plugins.examples.passthrough import PassthroughPlugin

    p = PassthroughPlugin()
    result = p.compress("original text", {"model": "claude-3"})
    assert result["text"] == "original text"
    assert result["metadata"]["changed"] is False


# ---------------------------------------------------------------------------
# 12. Default priority is 50
# ---------------------------------------------------------------------------


def test_default_priority():
    p = UpperPlugin()
    assert p.priority() == 50


# ---------------------------------------------------------------------------
# 13. discover() combines env + config
# ---------------------------------------------------------------------------


def test_discover_combines_env_and_config(tmp_path, monkeypatch):
    # Write a minimal plugin into a temp module
    plugin_src = textwrap.dedent("""\
        from tokenpak.plugins.base import CompressorPlugin
        class ConfigPlugin(CompressorPlugin):
            name = "config_plugin"
            def compress(self, text, context):
                return {"text": text, "metadata": {}}
    """)
    plugin_file = tmp_path / "my_config_plugin.py"
    plugin_file.write_text(plugin_src)

    sys.path.insert(0, str(tmp_path))
    try:
        config = {"plugins": ["my_config_plugin.ConfigPlugin"]}
        config_file = tmp_path / "tokenpak.config.json"
        config_file.write_text(json.dumps(config))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(
            "TOKENPAK_PLUGINS",
            "tokenpak.plugins.examples.passthrough.PassthroughPlugin",
        )

        reg = PluginRegistry()
        reg.discover()
        names = {p.name for p in reg.get_plugins()}
        assert "passthrough" in names
        assert "config_plugin" in names
    finally:
        sys.path.pop(0)
