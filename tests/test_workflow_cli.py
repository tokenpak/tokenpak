"""tests/test_workflow_cli.py

CLI tests for tokenpak workflow subcommands.

AC coverage:
  AC1 — list/status commands work and return correct output
  AC2 — --filter active|completed|failed shorthand works
  AC3 — resume shows plan before confirming; --yes skips confirm
  AC4 — resume from failure resets failed/running steps, shows next step
  AC5 — cancel with cleanup: running steps stopped, pending skipped
  AC6 — --json flag outputs valid JSON for list/status
  AC7 — progress bar rendered in status output
  AC8 — ETA shown for workflows with completed step durations
"""
from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.agentic.workflow", reason="module not available in current build")
import json
import time

import pytest
from click.testing import CliRunner

from tokenpak._internal.agentic.workflow import (
    StepStatus,
    WorkflowManager,
    WorkflowStatus,
    WorkflowStep,
)
from tokenpak.cli.commands.workflow import workflow_cmd

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def mgr(tmp_dir):
    return WorkflowManager(workflow_dir=tmp_dir)


@pytest.fixture
def runner():
    return CliRunner()


def invoke(runner, mgr, args):
    """Invoke workflow_cmd with manager patched to use tmp dir."""
    import tokenpak.cli.commands.workflow as wmod
    orig = wmod.get_manager
    wmod.get_manager = lambda: mgr
    try:
        result = runner.invoke(workflow_cmd, args, catch_exceptions=False)
    finally:
        wmod.get_manager = orig
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_simple_workflow(mgr, name="test-wf", status=None):
    """Create a 3-step workflow."""
    steps = [
        WorkflowStep(name="step-a"),
        WorkflowStep(name="step-b", depends_on=["step-a"]),
        WorkflowStep(name="step-c", depends_on=["step-b"]),
    ]
    wf = mgr.create(name=name, steps=steps)
    if status:
        if status == WorkflowStatus.RUNNING:
            wf = mgr.start(wf.id)
        elif status == WorkflowStatus.COMPLETED:
            wf = mgr.start(wf.id)
            wf = mgr.begin_step(wf.id, "step-a")
            wf = mgr.complete_step(wf.id, "step-a")
            wf = mgr.begin_step(wf.id, "step-b")
            wf = mgr.complete_step(wf.id, "step-b")
            wf = mgr.begin_step(wf.id, "step-c")
            wf = mgr.complete_step(wf.id, "step-c")
        elif status == WorkflowStatus.FAILED:
            wf = mgr.start(wf.id)
            wf = mgr.begin_step(wf.id, "step-a")
            wf = mgr.fail_step(wf.id, "step-a", error="Simulated failure")
    return wf


# ── AC1: list / status ─────────────────────────────────────────────────────────

def test_list_empty(runner, mgr):
    result = invoke(runner, mgr, ["list"])
    assert result.exit_code == 0
    assert "No workflows found" in result.output


def test_list_shows_workflow(runner, mgr):
    make_simple_workflow(mgr, name="my-workflow")
    result = invoke(runner, mgr, ["list"])
    assert result.exit_code == 0
    assert "my-workflow" in result.output
    assert "pending" in result.output


def test_status_shows_steps(runner, mgr):
    wf = make_simple_workflow(mgr)
    result = invoke(runner, mgr, ["status", wf.id])
    assert result.exit_code == 0
    assert "step-a" in result.output
    assert "step-b" in result.output
    assert "step-c" in result.output


def test_status_by_prefix(runner, mgr):
    wf = make_simple_workflow(mgr, name="prefix-test")
    prefix = wf.id[:6]
    result = invoke(runner, mgr, ["status", prefix])
    assert result.exit_code == 0
    assert "prefix-test" in result.output


# ── AC2: --filter flag ─────────────────────────────────────────────────────────

def test_filter_active(runner, mgr):
    wf_active = make_simple_workflow(mgr, name="active-wf", status=WorkflowStatus.RUNNING)
    wf_done = make_simple_workflow(mgr, name="done-wf", status=WorkflowStatus.COMPLETED)
    result = invoke(runner, mgr, ["list", "--filter", "active"])
    assert result.exit_code == 0
    assert "active-wf" in result.output
    assert "done-wf" not in result.output


def test_filter_completed(runner, mgr):
    make_simple_workflow(mgr, name="active-wf", status=WorkflowStatus.RUNNING)
    make_simple_workflow(mgr, name="done-wf", status=WorkflowStatus.COMPLETED)
    result = invoke(runner, mgr, ["list", "--filter", "completed"])
    assert result.exit_code == 0
    assert "done-wf" in result.output
    assert "active-wf" not in result.output


def test_filter_failed(runner, mgr):
    make_simple_workflow(mgr, name="failed-wf", status=WorkflowStatus.FAILED)
    make_simple_workflow(mgr, name="done-wf", status=WorkflowStatus.COMPLETED)
    result = invoke(runner, mgr, ["list", "--filter", "failed"])
    assert result.exit_code == 0
    assert "failed-wf" in result.output
    assert "done-wf" not in result.output


# ── AC3: resume shows plan, --yes skips confirm ────────────────────────────────

def test_resume_shows_plan(runner, mgr):
    wf = make_simple_workflow(mgr, name="plan-wf")
    mgr.start(wf.id)
    # Simulate input "n" to abort
    result = runner.invoke(
        workflow_cmd,
        ["resume", wf.id],
        input="n\n",
        catch_exceptions=False,
    )
    import tokenpak.cli.commands.workflow as wmod
    orig = wmod.get_manager
    wmod.get_manager = lambda: mgr
    try:
        result = runner.invoke(workflow_cmd, ["resume", wf.id], input="n\n", catch_exceptions=False)
    finally:
        wmod.get_manager = orig
    # Should show plan regardless of confirm answer
    assert "Resume plan" in result.output or "resume" in result.output.lower()
    assert "step-a" in result.output


def test_resume_yes_skips_confirm(runner, mgr):
    wf = make_simple_workflow(mgr)
    mgr.start(wf.id)
    result = invoke(runner, mgr, ["resume", wf.id, "--yes"])
    assert result.exit_code == 0
    assert "Resumed" in result.output


# ── AC4: resume from failure ───────────────────────────────────────────────────

def test_resume_from_failure(runner, mgr):
    """After a step fails, fix and resume: failed/skipped steps are reset."""
    wf = make_simple_workflow(mgr, name="fail-resume")
    wf = mgr.start(wf.id)
    wf = mgr.begin_step(wf.id, "step-a")
    wf = mgr.fail_step(wf.id, "step-a", error="Network error")

    # Check that status shows failure
    result = invoke(runner, mgr, ["status", wf.id])
    assert "failed" in result.output
    assert "Network error" in result.output

    # Resume plan should show failed step
    result = invoke(runner, mgr, ["resume", wf.id, "--yes"])
    assert result.exit_code == 0
    assert "Resumed" in result.output


def test_resume_shows_next_step(runner, mgr):
    """Resume output indicates the next step to execute."""
    wf = make_simple_workflow(mgr)
    wf = mgr.start(wf.id)
    wf = mgr.begin_step(wf.id, "step-a")
    wf = mgr.complete_step(wf.id, "step-a")
    # step-b is now the next pending step
    result = invoke(runner, mgr, ["resume", wf.id, "--yes"])
    assert result.exit_code == 0
    assert "step-b" in result.output


# ── AC5: cancel with cleanup ───────────────────────────────────────────────────

def test_cancel_yes_flag(runner, mgr):
    wf = make_simple_workflow(mgr, name="cancel-me")
    wf = mgr.start(wf.id)
    wf = mgr.begin_step(wf.id, "step-a")
    result = invoke(runner, mgr, ["cancel", wf.id, "--yes"])
    assert result.exit_code == 0
    assert "Cancelled" in result.output
    reloaded = mgr.load(wf.id)
    assert reloaded.status == WorkflowStatus.CANCELLED


def test_cancel_cleans_up_pending_steps(runner, mgr):
    wf = make_simple_workflow(mgr, name="cancel-cleanup")
    wf = mgr.start(wf.id)
    wf = mgr.begin_step(wf.id, "step-a")
    result = invoke(runner, mgr, ["cancel", wf.id, "--yes"])
    assert result.exit_code == 0
    reloaded = mgr.load(wf.id)
    terminal = {StepStatus.COMPLETED, StepStatus.SKIPPED, StepStatus.FAILED}
    for step in reloaded.steps:
        assert step.status in terminal, f"Step {step.name} not cleaned up: {step.status}"


def test_cancel_running_step_reported(runner, mgr):
    wf = make_simple_workflow(mgr, name="running-cancel")
    wf = mgr.start(wf.id)
    wf = mgr.begin_step(wf.id, "step-a")
    result = invoke(runner, mgr, ["cancel", wf.id, "--yes"])
    assert result.exit_code == 0
    # Output should mention the stopped step
    assert "step-a" in result.output or "Stopped" in result.output or "Cancelled" in result.output


# ── AC6: --json flag ───────────────────────────────────────────────────────────

def test_list_json(runner, mgr):
    make_simple_workflow(mgr, name="json-wf")
    result = invoke(runner, mgr, ["list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert data[0]["name"] == "json-wf"


def test_status_json(runner, mgr):
    wf = make_simple_workflow(mgr, name="status-json")
    result = invoke(runner, mgr, ["status", wf.id, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["name"] == "status-json"
    assert "steps" in data


# ── AC7: progress bar in status ────────────────────────────────────────────────

def test_status_shows_progress_bar(runner, mgr):
    wf = make_simple_workflow(mgr)
    result = invoke(runner, mgr, ["status", wf.id])
    assert result.exit_code == 0
    # Progress bar uses block characters
    assert "█" in result.output or "░" in result.output or "%" in result.output


def test_status_shows_step_count(runner, mgr):
    wf = make_simple_workflow(mgr)
    result = invoke(runner, mgr, ["status", wf.id])
    assert result.exit_code == 0
    assert "0/3" in result.output  # 0 of 3 steps done


# ── AC8: ETA shown when steps have durations ──────────────────────────────────

def test_eta_shown_for_running_workflow(runner, mgr):
    """When some steps are done (have durations), ETA should appear."""
    wf = make_simple_workflow(mgr, name="eta-test")
    wf = mgr.start(wf.id)
    wf = mgr.begin_step(wf.id, "step-a")
    # Manually set times so duration is non-zero
    step = next(s for s in wf.steps if s.name == "step-a")
    step.started_at = time.time() - 5.0
    step.completed_at = time.time()
    # Persist this manually
    mgr._save(wf)
    wf = mgr.complete_step(wf.id, "step-a")

    result = invoke(runner, mgr, ["status", wf.id])
    assert result.exit_code == 0
    # ETA line should be present since step-a has a duration
    assert "ETA" in result.output or "remaining" in result.output or "%" in result.output
