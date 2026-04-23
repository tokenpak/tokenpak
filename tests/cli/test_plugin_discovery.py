"""TPS-01 acceptance: plugin discovery via tokenpak.commands entry-points."""

from __future__ import annotations

from unittest.mock import patch

from tokenpak.cli._plugin_loader import (
    _ENTRY_POINT_GROUP,
    discover_plugin_commands,
    is_paid_command_available,
    plugins_enabled,
)


class _FakeEntryPoint:
    def __init__(self, name, target, dist_name="fake-dist"):
        self.name = name
        self._target = target
        # Minimal dist stub
        self.dist = type("Dist", (), {"name": dist_name, "metadata": {}})()

    def load(self):
        return self._target


def _stub_entry_points(group, eps):
    """Return a callable that mimics importlib.metadata.entry_points(group=...)."""
    def _fake(**kwargs):
        if kwargs.get("group") == group:
            return eps
        return []
    return _fake


def test_plugins_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TOKENPAK_ENABLE_PLUGINS", raising=False)
    assert not plugins_enabled()
    assert discover_plugin_commands() == {}


def test_plugins_enabled_flag(monkeypatch):
    monkeypatch.setenv("TOKENPAK_ENABLE_PLUGINS", "1")
    assert plugins_enabled()


def test_discovery_returns_registered_callables(monkeypatch):
    monkeypatch.setenv("TOKENPAK_ENABLE_PLUGINS", "1")

    def fake_cmd():
        return "ran"

    fake_ep = _FakeEntryPoint("fake-command", fake_cmd)
    with patch(
        "tokenpak.cli._plugin_loader.entry_points",
        _stub_entry_points(_ENTRY_POINT_GROUP, [fake_ep]),
    ):
        commands = discover_plugin_commands()

    assert "fake-command" in commands
    assert commands["fake-command"]() == "ran"


def test_broken_entry_point_logs_and_skips(monkeypatch, caplog):
    monkeypatch.setenv("TOKENPAK_ENABLE_PLUGINS", "1")

    class BrokenEP:
        name = "broken"
        dist = type("D", (), {"name": "broken-dist", "metadata": {}})()

        def load(self):
            raise RuntimeError("boom")

    with patch(
        "tokenpak.cli._plugin_loader.entry_points",
        _stub_entry_points(_ENTRY_POINT_GROUP, [BrokenEP()]),
    ):
        with caplog.at_level("WARNING"):
            commands = discover_plugin_commands()

    assert "broken" not in commands
    assert any("failed to load" in r.message for r in caplog.records)


def test_collision_with_reserved_name_skipped(monkeypatch, caplog):
    monkeypatch.setenv("TOKENPAK_ENABLE_PLUGINS", "1")
    fake_ep = _FakeEntryPoint("start", lambda: "collision")

    with patch(
        "tokenpak.cli._plugin_loader.entry_points",
        _stub_entry_points(_ENTRY_POINT_GROUP, [fake_ep]),
    ):
        with caplog.at_level("WARNING"):
            commands = discover_plugin_commands(reserved_names=["start"])

    assert "start" not in commands
    assert any("collides with an OSS command" in r.message for r in caplog.records)


def test_non_callable_target_rejected(monkeypatch, caplog):
    monkeypatch.setenv("TOKENPAK_ENABLE_PLUGINS", "1")
    fake_ep = _FakeEntryPoint("not-callable", "just a string")

    with patch(
        "tokenpak.cli._plugin_loader.entry_points",
        _stub_entry_points(_ENTRY_POINT_GROUP, [fake_ep]),
    ):
        with caplog.at_level("WARNING"):
            commands = discover_plugin_commands()

    assert "not-callable" not in commands
    assert any("did not resolve to a callable" in r.message for r in caplog.records)


def test_is_paid_command_available_respects_flag(monkeypatch):
    monkeypatch.delenv("TOKENPAK_ENABLE_PLUGINS", raising=False)
    assert not is_paid_command_available("optimize")


def test_plugin_target_gets_annotation(monkeypatch):
    monkeypatch.setenv("TOKENPAK_ENABLE_PLUGINS", "1")

    def fake_cmd():
        pass

    fake_ep = _FakeEntryPoint("annotated", fake_cmd, dist_name="tokenpak-paid")
    with patch(
        "tokenpak.cli._plugin_loader.entry_points",
        _stub_entry_points(_ENTRY_POINT_GROUP, [fake_ep]),
    ):
        commands = discover_plugin_commands()

    assert getattr(commands["annotated"], "_tp_plugin", False) is True
    assert getattr(commands["annotated"], "_tp_plugin_dist", None) == "tokenpak-paid"
