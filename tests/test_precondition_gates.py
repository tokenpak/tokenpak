"""Tests for tokenpak._internal.agentic.precondition_gates"""

import pytest
pytest.importorskip("tokenpak._internal.agentic.precondition_gates", reason="module not available in current build")
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tokenpak._internal.agentic.precondition_gates import (
    Gate,
    GateResult,
    PreconditionGates,
    SUPPORTED_GATE_TYPES,
    AUTO_PROMOTE_THRESHOLD,
    _check_env_check,
    _check_file_exists,
    _check_service_running,
    _check_test_passing,
    _check_diff_clean,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def gates_engine(tmp_path):
    """PreconditionGates with isolated tmp paths."""
    return PreconditionGates(
        gates_path=tmp_path / "preconditions.json",
        failures_path=tmp_path / "precondition_failures.jsonl",
        threshold=3,
    )


# ── Gate type: env_check ──────────────────────────────────────────────────────

def test_env_check_passes_when_vars_set(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "abc123")
    passed, reason = _check_env_check({"vars": ["MY_TOKEN"]})
    assert passed
    assert "present" in reason


def test_env_check_fails_when_var_missing(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    passed, reason = _check_env_check({"vars": ["MISSING_VAR"]})
    assert not passed
    assert "MISSING_VAR" in reason


def test_env_check_multiple_vars_partial_missing(monkeypatch):
    monkeypatch.setenv("GOOD_VAR", "yes")
    monkeypatch.delenv("BAD_VAR", raising=False)
    passed, reason = _check_env_check({"vars": ["GOOD_VAR", "BAD_VAR"]})
    assert not passed
    assert "BAD_VAR" in reason


# ── Gate type: file_exists ────────────────────────────────────────────────────

def test_file_exists_passes(tmp_path):
    f = tmp_path / "required.txt"
    f.write_text("hello")
    passed, reason = _check_file_exists({"paths": [str(f)]})
    assert passed


def test_file_exists_fails_missing(tmp_path):
    missing = str(tmp_path / "nope.txt")
    passed, reason = _check_file_exists({"paths": [missing]})
    assert not passed
    assert "nope.txt" in reason


def test_file_exists_empty_params():
    passed, reason = _check_file_exists({})
    assert passed  # no paths required → trivially passes


# ── Gate type: service_running ────────────────────────────────────────────────

def test_service_running_passes_via_pgrep():
    """Use a real process known to be running (python3 or pytest process)."""
    # Check that 'init' or 'systemd' or 'bash' is running via pgrep
    result = subprocess.run(["pgrep", "-x", "bash"], capture_output=True)
    # This may fail in some CI envs; skip if no bash
    if result.returncode != 0:
        pytest.skip("bash process not found; skipping service_running test")
    passed, reason = _check_service_running({"services": ["bash"]})
    assert passed, reason


def test_service_running_fails_for_nonexistent():
    passed, reason = _check_service_running({"services": ["definitely_not_a_real_service_xyz"]})
    assert not passed
    assert "definitely_not_a_real_service_xyz" in reason


# ── Gate type: test_passing ───────────────────────────────────────────────────

def test_test_passing_passes(tmp_path):
    passed, reason = _check_test_passing({"command": "true"})
    assert passed


def test_test_passing_fails(tmp_path):
    passed, reason = _check_test_passing({"command": "false"})
    assert not passed
    assert "failed" in reason.lower()


def test_test_passing_no_command():
    passed, reason = _check_test_passing({})
    assert passed  # no command = trivially passes


def test_test_passing_missing_command():
    passed, reason = _check_test_passing({"command": "definitely_not_a_command_xyz123"})
    assert not passed


# ── Gate type: diff_clean ─────────────────────────────────────────────────────

def test_diff_clean_passes_clean_repo(tmp_path):
    """Create a clean git repo and verify diff_clean passes."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )
    passed, reason = _check_diff_clean({"path": str(tmp_path)})
    assert passed, reason


def test_diff_clean_fails_with_uncommitted(tmp_path):
    """Create a dirty git repo and verify diff_clean fails."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )
    (tmp_path / "dirty.txt").write_text("uncommitted")
    passed, reason = _check_diff_clean({"path": str(tmp_path)})
    assert not passed
    assert "dirty.txt" in reason or "Uncommitted" in reason


# ── PreconditionGates: add / check / remove ───────────────────────────────────

def test_add_and_check_env_gate(gates_engine, monkeypatch):
    monkeypatch.setenv("REQUIRED_KEY", "set")
    gates_engine.add_gate(Gate(
        step="deploy",
        gate_type="env_check",
        params={"vars": ["REQUIRED_KEY"]},
    ))
    passed, reason = gates_engine.check("deploy")
    assert passed


def test_gate_blocks_when_condition_unmet(gates_engine, monkeypatch):
    monkeypatch.delenv("UNSET_KEY", raising=False)
    gates_engine.add_gate(Gate(
        step="deploy",
        gate_type="env_check",
        params={"vars": ["UNSET_KEY"]},
    ))
    passed, reason = gates_engine.check("deploy")
    assert not passed
    assert "UNSET_KEY" in reason


def test_no_gates_always_passes(gates_engine):
    passed, reason = gates_engine.check("ungated_step")
    assert passed


def test_remove_gate(gates_engine, monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    gates_engine.add_gate(Gate(
        step="build",
        gate_type="env_check",
        params={"vars": ["MISSING_VAR"]},
    ))
    # Gate blocks
    passed, _ = gates_engine.check("build")
    assert not passed

    # Remove gate
    removed = gates_engine.remove_gate("build", "env_check")
    assert removed

    # Now passes
    passed, _ = gates_engine.check("build")
    assert passed


def test_add_unknown_gate_type_raises(gates_engine):
    with pytest.raises(ValueError, match="Unknown gate type"):
        gates_engine.add_gate(Gate(step="x", gate_type="not_a_real_gate"))


# ── Persistence ───────────────────────────────────────────────────────────────

def test_gates_persist_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSIST_VAR", "yes")
    gp = tmp_path / "preconditions.json"
    fp = tmp_path / "failures.jsonl"

    e1 = PreconditionGates(gates_path=gp, failures_path=fp)
    e1.add_gate(Gate(step="sync", gate_type="env_check", params={"vars": ["PERSIST_VAR"]}))

    # Load in a fresh instance
    e2 = PreconditionGates(gates_path=gp, failures_path=fp)
    passed, reason = e2.check("sync")
    assert passed


# ── Auto-promotion ────────────────────────────────────────────────────────────

def test_auto_promote_after_threshold(gates_engine, monkeypatch):
    monkeypatch.delenv("AUTO_VAR", raising=False)
    params = {"vars": ["AUTO_VAR"]}

    # Record failures up to (threshold - 1) — should NOT promote yet
    for _ in range(gates_engine.threshold - 1):
        gates_engine.record_failure("auto_step", "env_check", params)

    promoted = gates_engine.promote_patterns()
    assert len(promoted) == 0

    # One more failure → threshold reached
    gates_engine.record_failure("auto_step", "env_check", params)
    promoted = gates_engine.promote_patterns()
    assert len(promoted) == 1
    assert promoted[0].step == "auto_step"
    assert promoted[0].gate_type == "env_check"
    assert promoted[0].auto_promoted is True


def test_auto_promote_does_not_duplicate(gates_engine, monkeypatch):
    monkeypatch.delenv("DUP_VAR", raising=False)
    params = {"vars": ["DUP_VAR"]}

    # Exceed threshold
    for _ in range(gates_engine.threshold + 2):
        gates_engine.record_failure("dup_step", "env_check", params)

    p1 = gates_engine.promote_patterns()
    assert len(p1) == 1

    # Second promotion run — should not add again
    p2 = gates_engine.promote_patterns()
    assert len(p2) == 0


def test_gate_summary(gates_engine, monkeypatch):
    monkeypatch.setenv("S_VAR", "x")
    gates_engine.add_gate(Gate(step="s1", gate_type="env_check", params={"vars": ["S_VAR"]}))
    gates_engine.record_failure("s2", "file_exists", {"paths": ["/tmp/missing"]})

    summary = gates_engine.gate_summary()
    assert summary["total_gates"] == 1
    assert "s1" in summary["gated_steps"]
    assert summary["total_failures_logged"] == 1


# ── list_gates ────────────────────────────────────────────────────────────────

def test_list_gates_all(gates_engine):
    gates_engine.add_gate(Gate(step="a", gate_type="diff_clean", params={"path": "."}))
    gates_engine.add_gate(Gate(step="b", gate_type="file_exists", params={"paths": ["/etc/hosts"]}))
    all_gates = gates_engine.list_gates()
    assert len(all_gates) == 2


def test_list_gates_filtered(gates_engine):
    gates_engine.add_gate(Gate(step="a", gate_type="diff_clean", params={"path": "."}))
    gates_engine.add_gate(Gate(step="b", gate_type="file_exists", params={"paths": ["/etc/hosts"]}))
    a_gates = gates_engine.list_gates(step="a")
    assert len(a_gates) == 1
    assert a_gates[0].step == "a"


# ── Workflow integration: gate blocks step instead of counting as failure ──────

def test_gate_block_is_not_a_failure(gates_engine, monkeypatch):
    """
    Simulate a workflow runner: if gate blocks, step is skipped, not failed.
    """
    monkeypatch.delenv("WORKFLOW_KEY", raising=False)
    gates_engine.add_gate(Gate(
        step="risky_step",
        gate_type="env_check",
        params={"vars": ["WORKFLOW_KEY"]},
    ))

    step_failed = False
    step_skipped = False

    passed, reason = gates_engine.check("risky_step")
    if not passed:
        step_skipped = True   # gate blocked → skip, not failure
    else:
        try:
            raise RuntimeError("step execution error")
        except RuntimeError:
            step_failed = True

    assert step_skipped is True
    assert step_failed is False
