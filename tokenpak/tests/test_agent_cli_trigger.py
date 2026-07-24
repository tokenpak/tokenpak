"""Tests for tokenpak.cli.trigger_cmd — CLI trigger commands.

Coverage targets:
  - trigger_group / subcommands importable
  - list: empty store, populated store, --json flag
  - add: creates trigger, --json output, missing required args
  - remove: removes existing, error on unknown id
  - test: dry-run matching, no-match case, --json
  - log: empty log, with entries, --json, --trigger-id filter
  - _build_cmd: tokenpak prefix logic
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tokenpak.cli.trigger_cmd import (
    _build_cmd,
    add_cmd,
    list_cmd,
    log_cmd,
    remove_cmd,
    test_cmd,
    trigger_group,
)
from tokenpak.orchestration.triggers.store import TriggerStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_store(monkeypatch, tmp_path):
    """Patch _store() to return a TriggerStore backed by a temp file."""
    config = tmp_path / "triggers.yaml"
    store = TriggerStore(config_path=config)

    monkeypatch.setattr("tokenpak.cli.trigger_cmd._store", lambda: TriggerStore(config_path=config))
    return store, config


@pytest.fixture()
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Test 1: Module imports cleanly
# ---------------------------------------------------------------------------


def test_module_import():
    """trigger_cmd can be imported and exposes the expected public API."""
    from tokenpak.cli import trigger_cmd  # noqa: F401

    assert hasattr(trigger_cmd, "trigger_group")
    assert hasattr(trigger_cmd, "add_cmd")
    assert hasattr(trigger_cmd, "remove_cmd")
    assert hasattr(trigger_cmd, "list_cmd")
    assert hasattr(trigger_cmd, "test_cmd")
    assert hasattr(trigger_cmd, "log_cmd")


# ---------------------------------------------------------------------------
# Test 2: trigger list — empty store
# ---------------------------------------------------------------------------


def test_list_empty(runner, tmp_store):
    """list with no triggers shows the 'no triggers' hint."""
    result = runner.invoke(list_cmd, [])
    assert result.exit_code == 0
    assert "No triggers configured" in result.output


# ---------------------------------------------------------------------------
# Test 3: trigger add — creates a trigger and shows confirmation
# ---------------------------------------------------------------------------


def test_add_creates_trigger(runner, tmp_store):
    """add --event git:commit --action 'echo hi' stores a trigger."""
    result = runner.invoke(add_cmd, ["--event", "git:commit", "--action", "echo hi"])
    assert result.exit_code == 0
    assert "Trigger added" in result.output
    assert "git:commit" in result.output
    assert "echo hi" in result.output


# ---------------------------------------------------------------------------
# Test 4: trigger add --json
# ---------------------------------------------------------------------------


def test_add_json_output(runner, tmp_store):
    """add --json returns valid JSON with expected keys."""
    result = runner.invoke(
        add_cmd,
        ["--event", "file:changed:*.py", "--action", "pytest", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "id" in data
    assert data["event"] == "file:changed:*.py"
    assert data["action"] == "pytest"
    assert data["enabled"] is True


# ---------------------------------------------------------------------------
# Test 5: trigger add — missing required --event / --action
# ---------------------------------------------------------------------------


def test_add_missing_required_args(runner, tmp_store):
    """add without --event and --action exits non-zero with usage error."""
    result = runner.invoke(add_cmd, [])
    assert result.exit_code != 0
    # Click reports "Missing option '--event'"
    assert "Missing option" in result.output or result.exit_code == 2


# ---------------------------------------------------------------------------
# Test 6: trigger list — shows added triggers
# ---------------------------------------------------------------------------


def test_list_shows_triggers(runner, tmp_store):
    """After adding a trigger, list shows it."""
    runner.invoke(add_cmd, ["--event", "agent:finished", "--action", "echo done"])
    result = runner.invoke(list_cmd, [])
    assert result.exit_code == 0
    assert "agent:finished" in result.output
    assert "echo done" in result.output


# ---------------------------------------------------------------------------
# Test 7: trigger list --json
# ---------------------------------------------------------------------------


def test_list_json_output(runner, tmp_store):
    """list --json returns valid JSON array."""
    runner.invoke(add_cmd, ["--event", "git:push", "--action", "ls"])
    result = runner.invoke(list_cmd, ["--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["event"] == "git:push"


# ---------------------------------------------------------------------------
# Test 8: trigger remove — existing trigger
# ---------------------------------------------------------------------------


def test_remove_existing(runner, tmp_store):
    """remove <id> removes a known trigger and confirms."""
    add_result = runner.invoke(
        add_cmd, ["--event", "git:commit", "--action", "make test", "--json"]
    )
    trigger_id = json.loads(add_result.output)["id"]

    result = runner.invoke(remove_cmd, [trigger_id])
    assert result.exit_code == 0
    assert "removed" in result.output

    # Verify it's gone from list
    list_result = runner.invoke(list_cmd, ["--json"])
    triggers = json.loads(list_result.output)
    assert not any(t["id"] == trigger_id for t in triggers)


# ---------------------------------------------------------------------------
# Test 9: trigger remove — unknown id exits 1
# ---------------------------------------------------------------------------


def test_remove_unknown_id(runner, tmp_store):
    """remove <nonexistent-id> exits with code 1 and error message."""
    result = runner.invoke(remove_cmd, ["nonexistent-id-xyz"])
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# Test 10: trigger test --dry-run — no matching triggers
# ---------------------------------------------------------------------------


def test_test_no_matches(runner, tmp_store):
    """test --event when no triggers are configured reports 0 matches."""
    result = runner.invoke(test_cmd, ["--event", "git:commit"])
    assert result.exit_code == 0
    assert "No triggers match" in result.output or "0 of 0" in result.output


# ---------------------------------------------------------------------------
# Test 11: trigger test --dry-run — matching trigger found
# ---------------------------------------------------------------------------


def test_test_match_dry_run(runner, tmp_store):
    """test --event matches a configured trigger in dry-run mode."""
    runner.invoke(add_cmd, ["--event", "git:commit", "--action", "echo triggered"])
    result = runner.invoke(test_cmd, ["--event", "git:commit", "--dry-run"])
    assert result.exit_code == 0
    assert "would fire" in result.output or "git:commit" in result.output


# ---------------------------------------------------------------------------
# Test 12: trigger test --json
# ---------------------------------------------------------------------------


def test_test_json_dry_run(runner, tmp_store):
    """test --json --dry-run returns a JSON array of matching triggers."""
    runner.invoke(add_cmd, ["--event", "agent:register", "--action", "echo registered"])
    result = runner.invoke(test_cmd, ["--event", "agent:register", "--dry-run", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["would_fire"] is True
    assert data[0]["dry_run"] is True


# ---------------------------------------------------------------------------
# Test 13: trigger log — empty log
# ---------------------------------------------------------------------------


def test_log_empty(runner, tmp_store):
    """log with no fired triggers shows the 'no entries' message."""
    result = runner.invoke(log_cmd, [])
    assert result.exit_code == 0
    assert "No trigger log entries" in result.output


# ---------------------------------------------------------------------------
# Test 14: trigger log --json after a fire
# ---------------------------------------------------------------------------


def test_log_json_after_fire(runner, tmp_store):
    """log --json returns entries after a trigger fires."""
    store, config = tmp_store
    # Add a trigger, then manually log a fire
    t = store.add(event="git:commit", action="echo logged")
    store.log_fire(t, exit_code=0, output="logged output")

    result = runner.invoke(log_cmd, ["--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["trigger_id"] == t.id
    assert data[0]["exit_code"] == 0


# ---------------------------------------------------------------------------
# Test 15: trigger log --trigger-id filter
# ---------------------------------------------------------------------------


def test_log_trigger_id_filter(runner, tmp_store):
    """log --trigger-id filters entries by trigger id."""
    store, config = tmp_store
    t1 = store.add(event="git:commit", action="echo a")
    t2 = store.add(event="git:push", action="echo b")
    store.log_fire(t1, 0, "output a")
    store.log_fire(t2, 0, "output b")

    result = runner.invoke(log_cmd, ["--json", "--trigger-id", t1.id])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert all(entry["trigger_id"] == t1.id for entry in data)


# ---------------------------------------------------------------------------
# Test 16: _build_cmd — tokenpak sub-command prefixing
# ---------------------------------------------------------------------------


def test_build_cmd_known_subcmd():
    """_build_cmd prefixes known tokenpak subcommands."""
    assert _build_cmd("status") == "tokenpak status"
    assert _build_cmd("cost report") == "tokenpak cost report"
    assert _build_cmd("metrics --all") == "tokenpak metrics --all"


def test_build_cmd_shell_passthrough():
    """_build_cmd does not prefix absolute paths or shell commands."""
    assert _build_cmd("/usr/bin/make test") == "/usr/bin/make test"
    assert _build_cmd("./run.sh") == "./run.sh"
    assert _build_cmd("echo hello") == "echo hello"


# ---------------------------------------------------------------------------
# Test 17: trigger_group is a Click group
# ---------------------------------------------------------------------------


def test_trigger_group_is_click_group():
    """trigger_group is a Click Group with the expected subcommands."""
    import click

    assert isinstance(trigger_group, click.Group)
    cmds = set(trigger_group.commands.keys())
    assert {"list", "add", "remove", "test", "log"}.issubset(cmds)
