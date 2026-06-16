# SPDX-License-Identifier: Apache-2.0
"""Tests for plugin CLI verb discovery + dispatch.

The core CLI discovers verbs registered under the ``tokenpak.commands``
entry-point group and routes them to the plugin's own (already gated) callable.
These tests verify, with a fabricated entry point:

  * a plugin verb becomes discoverable and dispatchable;
  * the plugin callable receives the remaining argv and its int exit code
    propagates (including a gate "upgrade" stub code such as 2, proving the
    loader routes *through* the entitlement gate rather than around it);
  * a built-in verb is never overridable by a colliding plugin entry;
  * the ``TOKENPAK_ENABLE_PLUGINS=0`` opt-out disables discovery.

No real plugin package is installed: ``importlib.metadata.entry_points`` is
monkeypatched to yield fake entry points whose ``.load()`` returns a local
callable.
"""
from __future__ import annotations

import importlib.metadata

import pytest

from tokenpak import _cli_core


class _FakeEntryPoint:
    """Minimal stand-in for importlib.metadata.EntryPoint.

    ``load()`` returns the supplied callable rather than resolving a real
    ``module:attr`` target, so tests need no installed package.
    """

    def __init__(self, name: str, func):
        self.name = name
        self.group = "tokenpak.commands"
        self._func = func

    def load(self):
        return self._func


class _FakeEntryPoints:
    """Stand-in for the object returned by ``entry_points()`` (3.12 style)."""

    def __init__(self, eps):
        self._eps = list(eps)

    def select(self, group=None):
        if group == "tokenpak.commands":
            return list(self._eps)
        return []


@pytest.fixture(autouse=True)
def _clear_plugin_cache():
    """Reset the process-level discovery cache around each test."""
    _cli_core._plugin_commands_cache = None
    yield
    _cli_core._plugin_commands_cache = None


def _patch_entry_points(monkeypatch, eps):
    fake = _FakeEntryPoints(eps)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda *a, **k: fake)


def test_plugin_verb_is_discoverable(monkeypatch):
    """A registered tokenpak.commands verb shows up in discovery."""
    ep = _FakeEntryPoint("daemon", lambda argv: 0)
    _patch_entry_points(monkeypatch, [ep])

    discovered = _cli_core._discover_plugin_commands(force=True)
    assert "daemon" in discovered
    assert discovered["daemon"] is ep


def test_plugin_verb_dispatches_with_argv(monkeypatch):
    """The plugin callable receives the remaining argv and its rc propagates."""
    seen = {}

    def _gated(argv):
        # Stand-in for the plugin's gated wrapper: entitled path runs + exits 0.
        seen["argv"] = list(argv)
        return 0

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("daemon", _gated)])

    rc = _cli_core._dispatch_plugin_command("daemon", ["start", "--port", "9000"])
    assert rc == 0
    assert seen["argv"] == ["start", "--port", "9000"]


def test_unentitled_gate_stub_exit_code_propagates(monkeypatch):
    """An unentitled invocation returns the gate's upgrade-stub code untouched.

    This proves the loader routes *to* the plugin's gate; it does not bypass or
    reimplement entitlement enforcement. A non-zero stub code (2) must survive.
    """
    def _gated_unentitled(argv):
        # Mirrors the gate's behavior for a user lacking the entitlement.
        print("This command requires an active entitlement.")
        return 2

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("daemon", _gated_unentitled)])

    rc = _cli_core._dispatch_plugin_command("daemon", ["start"])
    assert rc == 2


def test_core_verb_not_overridable_by_plugin(monkeypatch):
    """A plugin entry colliding with a built-in verb is excluded (core wins)."""
    core_verb = _cli_core._ALL_COMMANDS[0]
    assert core_verb  # sanity: at least one built-in exists

    sentinel = {"called": False}

    def _plugin_impl(argv):
        sentinel["called"] = True
        return 99

    # Register a plugin entry that tries to shadow an existing built-in verb.
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(core_verb, _plugin_impl)]
    )

    discovered = _cli_core._discover_plugin_commands(force=True)
    # The colliding name must NOT enter the plugin dispatch table.
    assert core_verb not in discovered

    # And dispatching the colliding name as a plugin must not invoke the plugin.
    rc = _cli_core._dispatch_plugin_command(core_verb, [])
    assert rc == 1  # not found in plugin table
    assert sentinel["called"] is False


def test_argparse_registered_core_verb_not_overridable(monkeypatch):
    """A built-in registered via argparse/stub (not in _COMMAND_GROUPS) also wins.

    These verbs live in the extra-known set rather than the grouped table, so
    this guards the *full* core-name exclusion, not just the grouped subset.
    """
    # 'last' is a built-in verb that is NOT part of _COMMAND_GROUPS.
    extra_verb = "last"
    assert extra_verb in _cli_core._EXTRA_KNOWN_COMMANDS
    assert extra_verb not in _cli_core._ALL_COMMANDS

    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(extra_verb, lambda argv: 77)]
    )
    discovered = _cli_core._discover_plugin_commands(force=True)
    assert extra_verb not in discovered


def test_plugins_disabled_via_env(monkeypatch):
    """TOKENPAK_ENABLE_PLUGINS=0 disables discovery entirely."""
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("daemon", lambda argv: 0)])
    monkeypatch.setenv("TOKENPAK_ENABLE_PLUGINS", "0")

    discovered = _cli_core._discover_plugin_commands(force=True)
    assert discovered == {}


def test_broken_plugin_environment_is_safe(monkeypatch):
    """If entry_points() raises, discovery returns empty and never propagates."""
    def _boom(*a, **k):
        raise RuntimeError("entry point backend exploded")

    monkeypatch.setattr(importlib.metadata, "entry_points", _boom)

    discovered = _cli_core._discover_plugin_commands(force=True)
    assert discovered == {}
