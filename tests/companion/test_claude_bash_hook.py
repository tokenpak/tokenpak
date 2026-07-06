# SPDX-License-Identifier: Apache-2.0
"""Focused regressions for the Claude companion bash pre-send hook."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _REPO_ROOT / "tokenpak" / "companion" / "hooks" / "pre_send.sh"
_BASH = shutil.which("bash") or "bash"
_SQLITE3 = shutil.which("sqlite3")


def _make_transcript(path: Path, size_bytes: int) -> Path:
    path.write_bytes(b"x" * size_bytes)
    return path


def _run_hook(
    payload: dict,
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    env["TOKENPAK_COMPANION_SHOW_COST"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )


def _path_without_sqlite(tmp_path: Path) -> str:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name in ("awk", "cat", "date", "rev", "sed", "stat"):
        target = shutil.which(name)
        if target:
            (fake_bin / name).symlink_to(target)
    return str(fake_bin)


def _seed_journal_db(tmp_path: Path) -> Path:
    db = tmp_path / "journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp REAL NOT NULL,
            entry_type TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.commit()
    conn.close()
    return db


def test_claude_bash_hook_uses_model_rate_snapshot(tmp_path):
    transcript = _make_transcript(tmp_path / "session.jsonl", size_bytes=400_000)
    rates = tmp_path / "model_rates.tsv"
    rates.write_text("claude-opus\t15\nclaude-sonnet\t3\n")

    result = _run_hook(
        {
            "session_id": "claude-rate-snapshot",
            "transcript_path": str(transcript),
            "model": "claude-opus-4-8",
        },
        tmp_path,
        extra_env={"TOKENPAK_COMPANION_RATES_FILE": str(rates)},
    )

    assert result.returncode == 0, result.stderr
    assert "~100,000 tokens" in result.stderr
    assert "est $1.500000" in result.stderr
    assert "(claude-opus-4-8)" in result.stderr


def test_claude_bash_hook_blocks_configured_budget_when_sqlite_missing(tmp_path):
    transcript = _make_transcript(tmp_path / "session.jsonl", size_bytes=4_000)

    result = _run_hook(
        {"transcript_path": str(transcript), "model": "claude-sonnet-4-6"},
        tmp_path,
        extra_env={
            "PATH": _path_without_sqlite(tmp_path),
            "TOKENPAK_COMPANION_BUDGET": "10",
        },
    )

    assert result.returncode == 2, result.stderr
    assert "sqlite3 missing" in result.stderr
    payload = json.loads(result.stdout.strip())
    assert payload["hookSpecificOutput"]["decision"] == "block"
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_claude_bash_hook_allows_no_budget_without_sqlite(tmp_path):
    transcript = _make_transcript(tmp_path / "session.jsonl", size_bytes=4_000)

    result = _run_hook(
        {"transcript_path": str(transcript), "model": "claude-sonnet-4-6"},
        tmp_path,
        extra_env={"PATH": _path_without_sqlite(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    assert "est $" in result.stderr


@pytest.mark.skipif(_SQLITE3 is None, reason="sqlite3 CLI not installed")
def test_claude_bash_hook_writes_auto_journal_when_db_present(tmp_path):
    db = _seed_journal_db(tmp_path)
    transcript = _make_transcript(tmp_path / "session.jsonl", size_bytes=8_000)

    result = _run_hook(
        {
            "session_id": "claude-journal-row",
            "transcript_path": str(transcript),
            "model": "claude-sonnet-4-6",
        },
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    rows = []
    for _ in range(20):
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT entry_type, content FROM entries WHERE session_id = ?",
            ("claude-journal-row",),
        ).fetchall()
        conn.close()
        if rows:
            break
        time.sleep(0.05)

    assert rows
    assert rows[0][0] == "auto"
    assert "prompt submitted" in rows[0][1]
    assert "model: claude-sonnet-4-6" in rows[0][1]
