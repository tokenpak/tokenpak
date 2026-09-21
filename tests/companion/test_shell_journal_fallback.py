# SPDX-License-Identifier: Apache-2.0
"""Exercise the actual shell hook without an external sqlite3 executable."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tokenpak.companion import _sqlite
from tokenpak.companion.journal.store import JournalStore

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "tokenpak/companion/hooks/pre_send.sh"


def _wait_for_intents(directory, count):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending = list((directory / "run" / _sqlite.PRE_SEND_PENDING_DIR).glob("*.json"))
        if len(pending) == count:
            return
        time.sleep(0.01)
    pytest.fail(f"expected {count} journal intents")


def _run(
    tmp_path, *, budget="0", transcript=True, session="first-session", model="fixture's-model"
):
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    for name in ("cat", "jq", "sed", "dirname", "mkdir", "mv", "stat", "date", "awk"):
        target = shutil.which(name)
        if target and not (binaries / name).exists():
            (binaries / name).symlink_to(target)
    path = tmp_path / "transcript.jsonl"
    path.write_text("private transcript body" * 100)
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("TOKENPAK_")},
        "HOME": str(tmp_path),
        "PATH": str(binaries),
        "TOKENPAK_COMPANION_PYTHON": sys.executable,
        "TOKENPAK_COMPANION_JOURNAL_DIR": str(tmp_path / "journal"),
        "TOKENPAK_COMPANION_ASYNC_FLUSH": "0",
        "TOKENPAK_COMPANION_BUDGET": budget,
        "TOKENPAK_COMPANION_SHOW_COST": "0",
    }
    payload = {
        "session_id": session,
        "model": model,
        "prompt": "private prompt body",
        "transcript_path": str(path) if transcript else "",
    }
    return subprocess.run(
        [shutil.which("bash"), str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=10,
    )


@pytest.mark.parametrize("transcript", [True, False])
def test_first_submission_is_visible_without_sqlite_and_replay_is_idempotent(tmp_path, transcript):
    for _ in range(2):
        result = _run(tmp_path, transcript=transcript)
        assert result.returncode == 0, result.stderr
        assert result.stdout == result.stderr == ""
    directory = tmp_path / "journal"
    _wait_for_intents(directory, 2)
    assert _sqlite.flush_pre_send_events(directory) == 2
    store = JournalStore(directory / "journal.db")
    sessions = store.recent_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == "first-session"
    assert sessions[0].entry_count == 1
    assert sessions[0].total_requests == 0
    assert sessions[0].total_cost_usd == 0
    assert sessions[0].ended_at is None
    with sqlite3.connect(directory / "journal.db") as connection:
        rows = connection.execute("SELECT content, metadata_json FROM entries").fetchall()
    assert len(rows) == 1 and "fixture's-model" in rows[0][0]
    assert "private" not in repr(rows)
    assert not (directory / "budget.db").exists()
    assert _sqlite.flush_pre_send_events(directory) == 0


def test_missing_sqlite_keeps_configured_budget_refusal(tmp_path):
    result = _run(tmp_path, budget="10")
    assert result.returncode == 2
    assert json.loads(result.stdout)["hookSpecificOutput"]["decision"] == "block"
    assert "sqlite3 missing" in result.stderr
    assert not list((tmp_path / "journal/run").glob("pre-send-pending/*.json"))


def test_invalid_session_does_not_write_journal(tmp_path):
    result = _run(tmp_path, session="bad'identity")
    assert result.returncode == 0
    assert not (tmp_path / "journal").exists()


def test_existing_session_metadata_and_usage_are_preserved(tmp_path):
    directory = tmp_path / "journal"
    store = JournalStore(directory / "journal.db")
    store.start_session("first-session", project_dir="existing-project", model="existing-model")
    before = store.recent_sessions()[0]
    assert _run(tmp_path).returncode == 0
    _wait_for_intents(directory, 1)
    assert _sqlite.flush_pre_send_events(directory) == 1
    after = store.recent_sessions()[0]
    for key in ("started_at", "project_dir", "model", "total_requests", "total_cost_usd"):
        assert getattr(after, key) == getattr(before, key)
