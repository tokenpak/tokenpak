"""CLI-surface coverage for governed trigger execution (CP-02).

Both the argparse ``trigger fire`` path (``_cli_core.cmd_trigger_fire``) and the
Click ``trigger test --execute`` path historically ran trigger actions through the
host shell. These tests prove they now execute via the governed command-action
model (``shell=False`` by default), pass payloads literally, and still record the
exit code and output in the fire log.
"""

from __future__ import annotations

import shlex
import sys
from types import SimpleNamespace

import pytest

_ECHO_ARG = [sys.executable, "-c", "import sys; print(sys.argv[1])"]


def _payload(sentinel) -> str:
    return f"; touch {sentinel} ; echo owned & whoami | cat"


def _quote(parts) -> str:
    return " ".join(shlex.quote(p) for p in parts)


class _FakeStore:
    """Minimal stand-in for TriggerStore used by the CLI fire/test paths."""

    def __init__(self, triggers):
        self._triggers = triggers
        self.fired = []

    def list(self):
        return list(self._triggers)

    def log_fire(self, trigger, exit_code, output):
        self.fired.append((exit_code, output))


def _trigger(event, action):
    return SimpleNamespace(id="t1", event=event, action=action, enabled=True)


# ── argparse: cmd_trigger_fire ────────────────────────────────────────────────


def test_cmd_trigger_fire_runs_without_shell(monkeypatch, tmp_path, capsys):
    from tokenpak import _cli_core

    sentinel = tmp_path / "PWNED"
    payload = _payload(sentinel)
    action = _quote([*_ECHO_ARG, payload])
    store = _FakeStore([_trigger("git:push", action)])
    monkeypatch.setattr(_cli_core, "_trigger_store", lambda: store)

    _cli_core.cmd_trigger_fire(SimpleNamespace(event="git:push"))

    # Logged with exit 0 and the payload echoed back literally; no shell ran the
    # injected ';touch', so the sentinel must not exist.
    assert store.fired and store.fired[0][0] == 0
    assert payload in store.fired[0][1]
    assert not sentinel.exists()


def test_cmd_trigger_fire_prefixes_tokenpak_subcommand(monkeypatch):
    from tokenpak import _cli_core
    from tokenpak.orchestration import commands as commands_mod

    seen = {}

    def recorder(action, **kwargs):
        seen["parsed"] = commands_mod.parse_trigger_action(action, warn=False)
        return commands_mod.CommandResult(returncode=0, output="ok")

    monkeypatch.setattr(commands_mod, "run_trigger_action", recorder)
    store = _FakeStore([_trigger("git:push", "status")])
    monkeypatch.setattr(_cli_core, "_trigger_store", lambda: store)

    _cli_core.cmd_trigger_fire(SimpleNamespace(event="git:push"))

    assert seen["parsed"].use_shell is False
    assert seen["parsed"].argv == ("tokenpak", "status")
    assert store.fired == [(0, "ok")]


# ── Click: trigger test --execute ─────────────────────────────────────────────


def test_trigger_test_execute_runs_without_shell(monkeypatch, tmp_path):
    from tokenpak.cli import trigger_cmd

    sentinel = tmp_path / "PWNED"
    payload = _payload(sentinel)
    action = _quote([*_ECHO_ARG, payload])
    store = _FakeStore([_trigger("git:push", action)])
    monkeypatch.setattr(trigger_cmd, "_store", lambda: store)

    # output_json keeps the path non-interactive and exercises the execute branch.
    trigger_cmd.test_cmd.callback(event="git:push", dry_run=False, output_json=True)

    assert store.fired and store.fired[0][0] == 0
    assert payload in store.fired[0][1]
    assert not sentinel.exists()


def test_trigger_test_dry_run_executes_nothing(monkeypatch, tmp_path):
    from tokenpak.cli import trigger_cmd

    sentinel = tmp_path / "created"
    action = f"{sys.executable} -c " + shlex.quote(f"open({str(sentinel)!r},'w').close()")
    store = _FakeStore([_trigger("git:push", action)])
    monkeypatch.setattr(trigger_cmd, "_store", lambda: store)

    trigger_cmd.test_cmd.callback(event="git:push", dry_run=True, output_json=True)

    assert store.fired == []  # dry-run logs nothing and runs nothing
    assert not sentinel.exists()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
