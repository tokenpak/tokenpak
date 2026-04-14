# SPDX-License-Identifier: Apache-2.0
"""Tests for the UserPromptSubmit pre_send hook.

Tests the hook as a subprocess — pipes JSON to stdin, checks exit code
and stderr output.  No Claude Code required.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).parent.parent.parent)


def _run_hook(
    hook_input: dict,
    tmp_path: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the pre_send hook subprocess with the given JSON input."""
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_NO_THREADS"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "tokenpak.companion.hooks.pre_send"],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )


# ---------------------------------------------------------------------------
# Allow path (exit 0)
# ---------------------------------------------------------------------------

def test_hook_allow_normal_prompt(tmp_path):
    """Normal prompt with no transcript path exits 0 (allow)."""
    result = _run_hook(
        {"session_id": "test-session", "transcript_path": "", "prompt": "hello"},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0


def test_hook_disabled_exits_zero(tmp_path):
    """Disabled companion always exits 0 regardless of input."""
    result = _run_hook(
        {"session_id": "s1", "transcript_path": "", "prompt": "hello"},
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_ENABLED": "0"},
    )
    assert result.returncode == 0


def test_hook_allow_with_session_id(tmp_path):
    """Hook with a valid session_id and no transcript exits 0."""
    result = _run_hook(
        {"session_id": "abc-123", "transcript_path": "", "prompt": "what is 2+2"},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Fail-open paths (exit 0)
# ---------------------------------------------------------------------------

def test_hook_empty_stdin_exits_zero(tmp_path):
    """Empty stdin (no JSON) is a fail-open: exits 0."""
    env = os.environ.copy()
    env["TOKENPAK_NO_THREADS"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.companion.hooks.pre_send"],
        input="",
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )
    assert result.returncode == 0


def test_hook_invalid_json_stdin_exits_zero(tmp_path):
    """Invalid JSON is a fail-open: exits 0."""
    env = os.environ.copy()
    env["TOKENPAK_NO_THREADS"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "tokenpak.companion.hooks.pre_send"],
        input="{this is not valid json",
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )
    assert result.returncode == 0


def test_hook_missing_fields_exits_zero(tmp_path):
    """JSON with no recognized fields fails open: exits 0."""
    result = _run_hook(
        {"unexpected_field": "value"},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Block path (exit 2)
# ---------------------------------------------------------------------------

def test_hook_blocks_when_over_budget(tmp_path):
    """Hook exits 2 when daily budget is exceeded.

    Strategy: create a transcript JSONL with content so tokens_est > 0,
    then seed the budget DB with a large cost so daily_total >> budget.
    """
    # Create a minimal transcript JSONL so parse_transcript returns tokens_est > 0
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        json.dumps({"type": "user", "content": "hello " * 100}) + "\n"
        + json.dumps({"type": "assistant", "content": "world " * 100}) + "\n"
    )

    # Seed budget DB to exceed the tiny budget
    from tokenpak.companion.budget.tracker import BudgetTracker
    tracker = BudgetTracker(
        db_path=tmp_path / "budget.db",
        daily_budget=0.001,
    )
    tracker.record(input_tokens=1_000_000, model="sonnet")  # ~$3 >> $0.001 budget

    result = _run_hook(
        {
            "session_id": "block-test",
            "transcript_path": str(transcript_path),
            "prompt": "another expensive prompt",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.001"},
    )
    assert result.returncode == 2
    # Block reason should appear in stderr
    assert "budget" in result.stderr.lower() or "tokenpak" in result.stderr.lower()


def test_hook_block_outputs_json_decision(tmp_path):
    """Blocked hook prints hookSpecificOutput JSON to stdout."""
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        json.dumps({"type": "user", "content": "test " * 200}) + "\n"
    )

    from tokenpak.companion.budget.tracker import BudgetTracker
    tracker = BudgetTracker(db_path=tmp_path / "budget.db", daily_budget=0.001)
    tracker.record(input_tokens=1_000_000, model="sonnet")

    result = _run_hook(
        {
            "session_id": "block-test-2",
            "transcript_path": str(transcript_path),
            "prompt": "blocked",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.001"},
    )
    assert result.returncode == 2
    # stdout should have a JSON decision block
    decision = json.loads(result.stdout.strip())
    assert decision["hookSpecificOutput"]["decision"] == "block"
    assert decision["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


# ---------------------------------------------------------------------------
# Show-cost output
# ---------------------------------------------------------------------------

def test_hook_show_cost_writes_to_stderr(tmp_path):
    """With show_cost enabled and a transcript, hook writes estimate to stderr."""
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        json.dumps({"type": "user", "content": "hello " * 500}) + "\n"
    )

    result = _run_hook(
        {
            "session_id": "cost-test",
            "transcript_path": str(transcript_path),
            "prompt": "how much?",
        },
        tmp_path=tmp_path,
        extra_env={
            "TOKENPAK_COMPANION_SHOW_COST": "1",
            "TOKENPAK_COMPANION_BUDGET": "0",  # no budget gate
        },
    )
    assert result.returncode == 0
    assert "tokenpak" in result.stderr


def test_hook_show_cost_disabled_no_stderr(tmp_path):
    """With show_cost disabled, hook produces no stderr output."""
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        json.dumps({"type": "user", "content": "hello " * 500}) + "\n"
    )

    result = _run_hook(
        {
            "session_id": "no-cost-test",
            "transcript_path": str(transcript_path),
            "prompt": "quiet",
        },
        tmp_path=tmp_path,
        extra_env={
            "TOKENPAK_COMPANION_SHOW_COST": "0",
            "TOKENPAK_COMPANION_BUDGET": "0",
        },
    )
    assert result.returncode == 0
    assert result.stderr == ""
