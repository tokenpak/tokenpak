# SPDX-License-Identifier: Apache-2.0
"""Tests for the UserPromptSubmit pre_send hook.

Tests both the bash hook (pre_send.sh) and the Python fallback (pre_send.py)
as subprocesses — pipes JSON to stdin, checks exit code and stderr output.
No Claude Code required.

Input format (6-field, from COMP-02 probe results):
    session_id, transcript_path, cwd, permission_mode, hook_event_name, prompt
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).parent.parent.parent)
_BASH_HOOK = str(Path(_REPO_ROOT) / "tokenpak" / "companion" / "hooks" / "pre_send.sh")

# Canonical 6-field hook input used by Claude Code (from COMP-02 probe results)
_SIX_FIELD_INPUT = {
    "session_id": "test-session-id",
    "transcript_path": "",
    "cwd": "/tmp",
    "permission_mode": "default",
    "hook_event_name": "UserPromptSubmit",
    "prompt": "hello world",
}


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
        json.dumps({"type": "user", "content": "hello " * 100})
        + "\n"
        + json.dumps({"type": "assistant", "content": "world " * 100})
        + "\n"
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
    transcript_path.write_text(json.dumps({"type": "user", "content": "test " * 200}) + "\n")

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
    transcript_path.write_text(json.dumps({"type": "user", "content": "hello " * 500}) + "\n")

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
    transcript_path.write_text(json.dumps({"type": "user", "content": "hello " * 500}) + "\n")

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


# ---------------------------------------------------------------------------
# Python fallback — journal write
# ---------------------------------------------------------------------------


def test_python_hook_writes_journal_entry(tmp_path):
    """Python hook writes a journal entry to journal.db when tokens_est > 0."""
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(json.dumps({"type": "user", "content": "hello " * 500}) + "\n")

    result = _run_hook(
        {
            "session_id": "journal-test",
            "transcript_path": str(transcript_path),
            "cwd": "/tmp",
            "permission_mode": "default",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "write me something",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_SHOW_COST": "1", "TOKENPAK_COMPANION_BUDGET": "0"},
    )
    assert result.returncode == 0

    journal_db = tmp_path / "journal.db"
    assert journal_db.exists(), "journal.db not written"
    conn = sqlite3.connect(str(journal_db))
    rows = conn.execute("SELECT session_id, entry_type FROM entries").fetchall()
    conn.close()
    assert len(rows) >= 1
    session_ids = [r[0] for r in rows]
    assert "journal-test" in session_ids


def test_python_hook_six_field_input(tmp_path):
    """Python hook accepts the canonical 6-field input format from COMP-02."""
    result = _run_hook(
        dict(_SIX_FIELD_INPUT),
        tmp_path=tmp_path,
    )
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Bash hook — runner helper
# ---------------------------------------------------------------------------


def _run_bash_hook(
    hook_input: dict,
    tmp_path: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the bash pre_send.sh hook with the given JSON input."""
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", _BASH_HOOK],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )


def _make_transcript(path: Path, size_bytes: int = 5000) -> Path:
    """Create a JSONL transcript with approximately size_bytes of content."""
    content = json.dumps({"type": "user", "content": "hello world " * 10}) + "\n"
    line_size = len(content.encode())
    lines = max(1, size_bytes // line_size)
    path.write_text(content * lines)
    return path


# ---------------------------------------------------------------------------
# Bash hook — allow path
# ---------------------------------------------------------------------------


def test_bash_hook_allow_empty_transcript(tmp_path):
    """Bash hook exits 0 when transcript_path is empty (no tokens to estimate)."""
    result = _run_bash_hook(dict(_SIX_FIELD_INPUT), tmp_path=tmp_path)
    assert result.returncode == 0


def test_bash_hook_allow_no_budget_set(tmp_path):
    """Bash hook exits 0 when budget is not set."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(hook_input, tmp_path=tmp_path)
    assert result.returncode == 0


def test_bash_hook_allow_under_budget(tmp_path):
    """Bash hook exits 0 when estimated cost is within a large budget."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=4000)  # ~1000 tokens, ~$0.003
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_sqlite = fake_bin / "sqlite3"
    fake_sqlite.write_text("#!/usr/bin/env sh\nexit 0\n")
    fake_sqlite.chmod(0o755)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "TOKENPAK_COMPANION_BUDGET": "100.00",
        },
    )
    assert result.returncode == 0


def test_bash_hook_disabled_exits_zero(tmp_path):
    """Bash hook with TOKENPAK_COMPANION_ENABLED=0 exits 0 unconditionally."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_ENABLED": "0"},
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_bash_hook_missing_transcript_exits_zero(tmp_path):
    """Bash hook with nonexistent transcript_path exits 0 (fail-open)."""
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = "/nonexistent/transcript.jsonl"
    result = _run_bash_hook(hook_input, tmp_path=tmp_path)
    assert result.returncode == 0


def test_bash_hook_six_field_input(tmp_path):
    """Bash hook accepts the canonical 6-field input from COMP-02."""
    result = _run_bash_hook(dict(_SIX_FIELD_INPUT), tmp_path=tmp_path)
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Bash hook — block path (exit 2)
# ---------------------------------------------------------------------------


def test_bash_hook_blocks_when_over_budget(tmp_path):
    """Bash hook exits 2 when budget is set to a tiny value and transcript is large."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)  # ~50k tokens, ~$0.15
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.0001"},
    )
    assert result.returncode == 2


def test_bash_hook_block_outputs_json_decision(tmp_path):
    """Blocked bash hook prints hookSpecificOutput JSON to stdout."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.0001"},
    )
    assert result.returncode == 2
    decision = json.loads(result.stdout.strip())
    assert decision["hookSpecificOutput"]["decision"] == "block"
    assert decision["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_bash_hook_block_stderr_contains_budget_message(tmp_path):
    """Blocked bash hook prints a budget exceeded message to stderr."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.0001"},
    )
    assert result.returncode == 2
    assert "budget" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Bash hook — stderr format
# ---------------------------------------------------------------------------


def test_bash_hook_stderr_format(tmp_path):
    """Bash hook stderr matches: 'tokenpak: ~N,NNN tokens  est $X.XXXX'."""
    import re

    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(hook_input, tmp_path=tmp_path)
    assert result.returncode == 0
    # Pattern: "tokenpak: ~50,000 tokens  est $0.1500"
    assert re.search(r"tokenpak: ~[\d,]+ tokens  est \$[\d.]+", result.stderr)


def test_bash_hook_stderr_thousands_separators(tmp_path):
    """Bash hook stderr token count uses comma thousands separators for >= 1000."""
    transcript_path = tmp_path / "session.jsonl"
    # 200k bytes → ~50k tokens → "50,000" (has comma)
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(hook_input, tmp_path=tmp_path)
    assert result.returncode == 0
    assert "," in result.stderr, f"Expected thousands separator in: {result.stderr!r}"


def test_bash_hook_show_cost_disabled_no_stderr(tmp_path):
    """Bash hook with TOKENPAK_COMPANION_SHOW_COST=0 produces no stderr."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_SHOW_COST": "0"},
    )
    assert result.returncode == 0
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Performance: bash < 50ms on 200k transcript
# ---------------------------------------------------------------------------


def test_bash_hook_completes_under_50ms_on_200k(tmp_path):
    """Bash hook completes in < 50ms with a 200k-byte transcript (target: 30ms)."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)

    # Warm-up then time three runs, use median
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        result = _run_bash_hook(hook_input, tmp_path=tmp_path)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result.returncode == 0
        times.append(elapsed_ms)
    median_ms = sorted(times)[1]
    assert median_ms < 50, f"Bash hook took {median_ms:.1f}ms (limit: 50ms, times: {times})"


def test_performance_bash_faster_than_python(tmp_path):
    """Bash hook should be faster than the Python fallback on a 200k transcript."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)

    # Time bash hook (median of 3)
    bash_times = []
    for _ in range(3):
        t0 = time.perf_counter()
        _run_bash_hook(hook_input, tmp_path=tmp_path)
        bash_times.append((time.perf_counter() - t0) * 1000)
    bash_median = sorted(bash_times)[1]

    # Time Python hook (median of 3)
    python_times = []
    for _ in range(3):
        t0 = time.perf_counter()
        _run_hook(hook_input, tmp_path=tmp_path)
        python_times.append((time.perf_counter() - t0) * 1000)
    python_median = sorted(python_times)[1]

    assert bash_median < python_median, (
        f"Bash ({bash_median:.1f}ms) not faster than Python ({python_median:.1f}ms)"
    )
