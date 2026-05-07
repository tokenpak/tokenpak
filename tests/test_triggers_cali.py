"""Unit tests for TokenPak event trigger framework."""


import pytest

pytest.importorskip("tokenpak._internal.triggers.matcher", reason="module not available in current build")

import pytest

from tokenpak._internal.triggers.daemon import _parse_interval_seconds
from tokenpak._internal.triggers.matcher import match_event
from tokenpak._internal.triggers.store import TriggerStore

# ── Store CRUD ────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_store(tmp_path):
    config = tmp_path / "triggers.yaml"
    return TriggerStore(config_path=config)


def test_add_trigger(tmp_store):
    t = tmp_store.add(event="file:changed:*.py", action="stats")
    assert t.id
    assert t.event == "file:changed:*.py"
    assert t.action == "stats"
    assert len(tmp_store.list()) == 1


def test_list_empty(tmp_store):
    assert tmp_store.list() == []


def test_remove_trigger(tmp_store):
    t = tmp_store.add(event="timer:5m", action="stats")
    assert tmp_store.remove(t.id) is True
    assert tmp_store.list() == []


def test_remove_nonexistent(tmp_store):
    assert tmp_store.remove("deadbeef") is False


def test_get_trigger(tmp_store):
    t = tmp_store.add(event="cost:daily>5", action="stats")
    found = tmp_store.get(t.id)
    assert found is not None
    assert found.id == t.id


def test_persistence(tmp_path):
    config = tmp_path / "triggers.yaml"
    s1 = TriggerStore(config_path=config)
    t = s1.add(event="file:created:*.log", action="stats")
    tid = t.id

    s2 = TriggerStore(config_path=config)
    assert len(s2.list()) == 1
    assert s2.get(tid) is not None


def test_log_fire(tmp_store):
    t = tmp_store.add(event="timer:1m", action="stats")
    tmp_store.log_fire(t, exit_code=0, output="ok")
    logs = tmp_store.list_logs()
    assert len(logs) == 1
    assert logs[0].exit_code == 0


# ── Matcher ───────────────────────────────────────────────────────────────────

def test_match_exact():
    assert match_event("timer:5m", "timer:5m")


def test_match_file_changed_glob():
    assert match_event("file:changed:*.py", "file:changed:/home/user/foo.py")
    assert match_event("file:changed:*.py", "file:changed:bar.py")
    assert not match_event("file:changed:*.py", "file:changed:bar.js")


def test_match_file_created_glob():
    assert match_event("file:created:*.log", "file:created:/var/log/app.log")
    assert not match_event("file:created:*.log", "file:created:/var/log/app.txt")


def test_match_cost_threshold():
    assert match_event("cost:daily>5", "cost:daily>10.50")
    assert match_event("cost:daily>5", "cost:daily>5.00")
    assert not match_event("cost:daily>10", "cost:daily>9.99")


def test_no_match_different_kinds():
    assert not match_event("file:changed:*.py", "file:created:foo.py")


# ── Daemon helpers ────────────────────────────────────────────────────────────

def test_parse_interval_seconds():
    assert _parse_interval_seconds("30s") == 30
    assert _parse_interval_seconds("5m") == 300
    assert _parse_interval_seconds("2h") == 7200


def test_parse_interval_invalid():
    with pytest.raises(ValueError):
        _parse_interval_seconds("5d")
    with pytest.raises(ValueError):
        _parse_interval_seconds("abc")


# ── Git + Agent Events ────────────────────────────────────────────────────────

def test_match_git_push_exact():
    assert match_event("git:push", "git:push")


def test_match_git_commit_exact():
    assert match_event("git:commit", "git:commit")


def test_match_git_push_with_branch():
    """git:push pattern should match git:push:<branch> events"""
    assert match_event("git:push", "git:push:main")
    assert match_event("git:push", "git:push:feature/x")


def test_no_match_git_push_vs_commit():
    assert not match_event("git:push", "git:commit")
    assert not match_event("git:commit", "git:push")


def test_match_agent_finished_wildcard():
    assert match_event("agent:finished", "agent:finished:cali")
    assert match_event("agent:finished", "agent:finished")


def test_match_agent_finished_specific():
    assert match_event("agent:finished:cali", "agent:finished:cali")
    assert not match_event("agent:finished:cali", "agent:finished:trix")


def test_match_agent_failed_wildcard():
    assert match_event("agent:failed", "agent:failed:some-task")


def test_match_agent_failed_specific():
    assert match_event("agent:failed:trix", "agent:failed:trix")
    assert not match_event("agent:failed:trix", "agent:failed:cali")


def test_no_cross_match_finished_failed():
    assert not match_event("agent:finished", "agent:failed:cali")
    assert not match_event("agent:failed", "agent:finished:cali")
