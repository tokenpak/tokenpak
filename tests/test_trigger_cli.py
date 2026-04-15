"""Tests for tokenpak trigger CLI — list, add, remove, test, log commands.

Covers:
- add/remove lifecycle
- list output (text + JSON)
- test dry-run / fire
- log output (text + JSON)
- --json flag on all commands
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak._internal", reason="module not available in current build")
import json
import pytest
from pathlib import Path
from click.testing import CliRunner

from tokenpak._internal.triggers.store import TriggerStore
from tokenpak.cli.trigger_cmd import trigger_group


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def store(tmp_path) -> TriggerStore:
    return TriggerStore(config_path=tmp_path / "triggers.yaml")


@pytest.fixture
def patched_group(store, monkeypatch):
    """Patch _store() so CLI commands use our temp store."""
    import tokenpak.cli.trigger_cmd as mod
    monkeypatch.setattr(mod, "_store", lambda: store)
    return store


# ---------------------------------------------------------------------------
# add / remove lifecycle
# ---------------------------------------------------------------------------

class TestAddRemoveLifecycle:
    def test_add_creates_trigger(self, runner, patched_group):
        result = runner.invoke(trigger_group, [
            "add", "--event", "git:commit", "--action", "echo committed"
        ])
        assert result.exit_code == 0, result.output
        assert "Trigger added" in result.output
        assert len(patched_group.list()) == 1

    def test_add_returns_id(self, runner, patched_group):
        result = runner.invoke(trigger_group, [
            "add", "--event", "file:changed:*.py", "--action", "echo py"
        ])
        triggers = patched_group.list()
        assert triggers[0].id in result.output

    def test_add_json(self, runner, patched_group):
        result = runner.invoke(trigger_group, [
            "add", "--event", "cost:daily>5", "--action", "echo cost",
            "--json"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["event"] == "cost:daily>5"
        assert data["action"] == "echo cost"
        assert "id" in data
        assert data["enabled"] is True

    def test_remove_existing(self, runner, patched_group):
        t = patched_group.add(event="git:push", action="echo push")
        result = runner.invoke(trigger_group, ["remove", t.id])
        assert result.exit_code == 0
        assert "removed" in result.output
        assert patched_group.list() == []

    def test_remove_nonexistent(self, runner, patched_group):
        result = runner.invoke(trigger_group, ["remove", "deadbeef"])
        assert result.exit_code != 0

    def test_remove_json(self, runner, patched_group):
        t = patched_group.add(event="timer:5m", action="echo tick")
        result = runner.invoke(trigger_group, ["remove", t.id, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["removed"] is True
        assert data["id"] == t.id

    def test_remove_json_nonexistent(self, runner, patched_group):
        result = runner.invoke(trigger_group, ["remove", "missing", "--json"])
        data = json.loads(result.output)
        assert data["removed"] is False

    def test_add_multiple_persist(self, runner, patched_group):
        for event in ["git:commit", "git:push", "file:changed:*.py"]:
            runner.invoke(trigger_group, [
                "add", "--event", event, "--action", f"echo {event}"
            ])
        assert len(patched_group.list()) == 3

    def test_remove_one_leaves_others(self, runner, patched_group):
        t1 = patched_group.add(event="git:commit", action="echo a")
        t2 = patched_group.add(event="git:push", action="echo b")
        runner.invoke(trigger_group, ["remove", t1.id])
        remaining = patched_group.list()
        assert len(remaining) == 1
        assert remaining[0].id == t2.id


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

class TestList:
    def test_empty_list(self, runner, patched_group):
        result = runner.invoke(trigger_group, ["list"])
        assert result.exit_code == 0
        assert "No triggers" in result.output

    def test_list_shows_triggers(self, runner, patched_group):
        patched_group.add(event="git:commit", action="echo hello")
        result = runner.invoke(trigger_group, ["list"])
        assert "git:commit" in result.output
        assert "echo hello" in result.output

    def test_list_json(self, runner, patched_group):
        patched_group.add(event="file:changed:*.md", action="echo md")
        patched_group.add(event="cost:daily>10", action="echo cost")
        result = runner.invoke(trigger_group, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        events = {d["event"] for d in data}
        assert "file:changed:*.md" in events
        assert "cost:daily>10" in events

    def test_list_json_empty(self, runner, patched_group):
        result = runner.invoke(trigger_group, ["list", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == []


# ---------------------------------------------------------------------------
# test (dry-run / fire)
# ---------------------------------------------------------------------------

class TestTestCmd:
    def test_dry_run_shows_match(self, runner, patched_group):
        patched_group.add(event="git:commit", action="echo committed")
        result = runner.invoke(trigger_group, [
            "test", "--event", "git:commit"
        ])
        assert result.exit_code == 0
        assert "1 trigger" in result.output
        assert "echo committed" in result.output

    def test_dry_run_no_match(self, runner, patched_group):
        patched_group.add(event="git:push", action="echo push")
        result = runner.invoke(trigger_group, [
            "test", "--event", "git:commit"
        ])
        assert result.exit_code == 0
        assert "0 of" in result.output

    def test_dry_run_json(self, runner, patched_group):
        patched_group.add(event="file:changed:*.py", action="echo py")
        result = runner.invoke(trigger_group, [
            "test", "--event", "file:changed:main.py", "--json"
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["would_fire"] is True
        assert data[0]["dry_run"] is True
        assert "echo py" in data[0]["action"]

    def test_execute_fires_and_logs(self, runner, patched_group):
        patched_group.add(event="git:commit", action="echo fired")
        result = runner.invoke(trigger_group, [
            "test", "--event", "git:commit", "--execute"
        ])
        assert result.exit_code == 0
        # Should produce output from the echo command
        assert "fired" in result.output
        # Log should have an entry
        logs = patched_group.list_logs()
        assert len(logs) == 1
        assert logs[0].exit_code == 0

    def test_execute_json_contains_output(self, runner, patched_group):
        patched_group.add(event="git:commit", action="echo hello_output")
        result = runner.invoke(trigger_group, [
            "test", "--event", "git:commit", "--execute", "--json"
        ])
        data = json.loads(result.output)
        assert data[0]["exit_code"] == 0
        assert "hello_output" in data[0]["output"]

    def test_file_event_glob_match(self, runner, patched_group):
        patched_group.add(event="file:changed:*.py", action="echo py")
        patched_group.add(event="file:changed:*.md", action="echo md")
        result = runner.invoke(trigger_group, [
            "test", "--event", "file:changed:app.py", "--json"
        ])
        data = json.loads(result.output)
        assert len(data) == 1
        assert "echo py" in data[0]["action"]

    def test_cost_threshold_match(self, runner, patched_group):
        patched_group.add(event="cost:daily>5", action="echo expensive")
        result = runner.invoke(trigger_group, [
            "test", "--event", "cost:daily>10.00", "--json"
        ])
        data = json.loads(result.output)
        assert len(data) == 1

    def test_agent_event_match(self, runner, patched_group):
        patched_group.add(event="agent:register", action="echo registered")
        result = runner.invoke(trigger_group, [
            "test", "--event", "agent:register", "--json"
        ])
        data = json.loads(result.output)
        assert len(data) == 1

    def test_schedule_cron_stored_and_matched(self, runner, patched_group):
        """schedule:cron events are stored and matched by exact string for daemon use."""
        patched_group.add(event="schedule:cron:*/5 * * * *", action="echo cron")
        result = runner.invoke(trigger_group, [
            "test", "--event", "schedule:cron:*/5 * * * *", "--json"
        ])
        data = json.loads(result.output)
        assert len(data) == 1


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------

class TestLog:
    def test_log_empty(self, runner, patched_group):
        result = runner.invoke(trigger_group, ["log"])
        assert result.exit_code == 0
        assert "No trigger log" in result.output

    def test_log_shows_entries(self, runner, patched_group):
        t = patched_group.add(event="git:commit", action="echo hi")
        patched_group.log_fire(t, exit_code=0, output="hi")
        result = runner.invoke(trigger_group, ["log"])
        assert "git:commit" in result.output
        assert "echo hi" in result.output

    def test_log_json(self, runner, patched_group):
        t = patched_group.add(event="file:changed:*.py", action="echo py")
        patched_group.log_fire(t, exit_code=0, output="py")
        patched_group.log_fire(t, exit_code=1, output="error")
        result = runner.invoke(trigger_group, ["log", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        assert all("trigger_id" in d for d in data)
        assert all("fired_at" in d for d in data)

    def test_log_limit(self, runner, patched_group):
        t = patched_group.add(event="git:push", action="echo push")
        for i in range(5):
            patched_group.log_fire(t, exit_code=0, output=f"run {i}")
        result = runner.invoke(trigger_group, ["log", "--limit", "3", "--json"])
        data = json.loads(result.output)
        assert len(data) == 3

    def test_log_filter_by_trigger_id(self, runner, patched_group):
        t1 = patched_group.add(event="git:commit", action="echo a")
        t2 = patched_group.add(event="git:push", action="echo b")
        patched_group.log_fire(t1, exit_code=0, output="a")
        patched_group.log_fire(t2, exit_code=0, output="b")
        patched_group.log_fire(t1, exit_code=0, output="a2")
        result = runner.invoke(trigger_group, [
            "log", "--trigger-id", t1.id, "--json"
        ])
        data = json.loads(result.output)
        assert all(d["trigger_id"] == t1.id for d in data)
        assert len(data) == 2

    def test_log_json_fields(self, runner, patched_group):
        t = patched_group.add(event="cost:daily>5", action="echo cost")
        patched_group.log_fire(t, exit_code=0, output="cost saved")
        result = runner.invoke(trigger_group, ["log", "--json"])
        data = json.loads(result.output)
        entry = data[0]
        required_fields = {"trigger_id", "event", "action", "fired_at", "exit_code", "output"}
        assert required_fields.issubset(entry.keys())
