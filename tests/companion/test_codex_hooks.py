"""Codex-side hook tests (audit deltas hooks #3 + hooks #5 + hooks #1).

Mirrors the shape of tests/companion/test_hooks.py but targets the
Codex-side bash hooks under tokenpak/companion/codex/.

Covered audit deltas:

- hooks #3 — JSON hookSpecificOutput block on UserPromptSubmit budget
  block. Landed on PR-45 (commit ad968849d4); tests preserved here.
- hooks #5 — declarative event table at module top (no per-event
  function body). Asserted via the new
  ``test_hook_events_is_declarative_table`` set.
- hooks #1 — SessionStart, PreToolUse, PostToolUse hooks wired. Each
  has a fixture under tests/fixtures/codex/ (happy-path + alternate)
  per the L2a packet's fixture_policy.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import textwrap
from pathlib import Path

import pytest

from tokenpak.companion.codex import hooks as codex_hooks

_SQLITE3_CLI = shutil.which("sqlite3")
_requires_sqlite3 = pytest.mark.skipif(
    _SQLITE3_CLI is None,
    reason="sqlite3 CLI not installed; bash hooks degrade to no-op for db ops",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CODEX_DIR = _REPO_ROOT / "tokenpak" / "companion" / "codex"
_CODEX_BASH_HOOK = str(_CODEX_DIR / "hooks_pre_send.sh")
_SESSION_START_HOOK = str(_CODEX_DIR / "hooks_session_start.sh")
_PRE_TOOL_USE_HOOK = str(_CODEX_DIR / "hooks_pre_tool_use.sh")
_POST_TOOL_USE_HOOK = str(_CODEX_DIR / "hooks_post_tool_use.sh")
_STOP_HOOK = str(_CODEX_DIR / "hooks_stop.sh")
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "codex"

_SIX_FIELD_INPUT = {
    "session_id": "test-session-codex-hooks",
    "transcript_path": "",
    "cwd": "/tmp",
    "hook_event_name": "UserPromptSubmit",
    "model": "sonnet",
    "prompt": "hello",
}


def _make_transcript(path: Path, size_bytes: int = 5000) -> Path:
    content = json.dumps({"type": "user", "content": "hello world " * 10}) + "\n"
    line_size = len(content.encode())
    lines = max(1, size_bytes // line_size)
    path.write_text(content * lines)
    return path


def _run_codex_bash_hook(
    hook_input: dict,
    tmp_path: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", _CODEX_BASH_HOOK],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )


def _run_script(
    script: str,
    fixture_name: str,
    tmp_path: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    if extra_env:
        env.update(extra_env)
    payload = (_FIXTURES / fixture_name).read_text()
    return subprocess.run(
        ["bash", script],
        input=payload,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )


def _seed_journal_db(tmp_path: Path) -> Path:
    db = tmp_path / "journal.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        textwrap.dedent(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                started_at REAL NOT NULL,
                ended_at REAL,
                project_dir TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                total_requests INTEGER NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0.0,
                total_input_tokens INTEGER NOT NULL DEFAULT 0,
                total_output_tokens INTEGER NOT NULL DEFAULT 0,
                capsule_path TEXT
            );
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
    )
    conn.commit()
    conn.close()
    return db


def _seed_budget_db(tmp_path: Path, daily_cost: float) -> Path:
    db = tmp_path / "budget.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            date TEXT NOT NULL,
            session_id TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost REAL NOT NULL DEFAULT 0.0
        )
        """
    )
    from datetime import date

    today = date.today().isoformat()
    conn.execute(
        "INSERT INTO companion_costs (timestamp, date, estimated_cost) VALUES (?, ?, ?)",
        (0.0, today, daily_cost),
    )
    conn.commit()
    conn.close()
    return db


# ──────────────────────────────────────────────────────────────
# hooks #3 — pre-existing PR-45 contract, unchanged.
# ──────────────────────────────────────────────────────────────


def test_codex_bash_hook_block_outputs_json_decision(tmp_path):
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_codex_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.0001"},
    )
    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    decision = json.loads(result.stdout.strip())
    assert decision["hookSpecificOutput"]["decision"] == "block"
    assert decision["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "budget" in decision["hookSpecificOutput"]["reason"].lower()


def test_codex_bash_hook_block_stderr_preserved(tmp_path):
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_codex_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.0001"},
    )
    assert result.returncode == 2
    assert "budget" in result.stderr.lower()


def test_codex_bash_hook_allow_no_budget(tmp_path):
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=20_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_codex_bash_hook(hook_input, tmp_path=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# ──────────────────────────────────────────────────────────────
# hooks #5 — declarative event table at module top.
# ──────────────────────────────────────────────────────────────


def test_hook_events_is_declarative_table():
    """`_TOKENPAK_HOOK_EVENTS` lives at module top and exposes all 5 events.

    Adding a new event must be possible by appending to the table —
    no per-event code branch should be needed. Closes audit delta
    hooks #5.
    """
    table = codex_hooks._TOKENPAK_HOOK_EVENTS
    assert isinstance(table, dict)
    assert set(table.keys()) == {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
    }
    for event, group in table.items():
        assert "hooks" in group, f"{event}: missing hooks key"
        assert isinstance(group["hooks"], list) and group["hooks"], f"{event}: hooks list empty"
        for entry in group["hooks"]:
            assert entry.get("type") == "command", f"{event}: non-command hook"
            assert codex_hooks.TOKENPAK_HOOK_MARKER in entry.get("command", ""), (
                f"{event}: command missing tokenpak marker"
            )


def test_tokenpak_hook_events_accessor_returns_table():
    """Back-compat accessor returns the same dict as the module constant."""
    assert codex_hooks._tokenpak_hook_events() is codex_hooks._TOKENPAK_HOOK_EVENTS


def test_generate_hooks_json_includes_all_five_events():
    """generate_hooks_json() emits Codex's documented shape for all events."""
    hooks_json = codex_hooks.generate_hooks_json()
    assert set(hooks_json["hooks"].keys()) == {
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
    }
    for event, groups in hooks_json["hooks"].items():
        assert isinstance(groups, list) and len(groups) == 1, event
        assert "hooks" in groups[0], event


# ──────────────────────────────────────────────────────────────
# hooks #1 — SessionStart fixture replay.
# ──────────────────────────────────────────────────────────────


def test_session_start_hook_emits_banner_on_startup(tmp_path):
    result = _run_script(_SESSION_START_HOOK, "hook_session_start_startup.json", tmp_path)
    assert result.returncode == 0
    assert "tokenpak" in result.stderr.lower()
    assert "startup" in result.stderr.lower()


def test_session_start_hook_emits_banner_on_resume(tmp_path):
    result = _run_script(_SESSION_START_HOOK, "hook_session_start_resume.json", tmp_path)
    assert result.returncode == 0
    assert "resume" in result.stderr.lower()


def test_session_start_hook_is_quiet_on_clear(tmp_path):
    result = _run_script(_SESSION_START_HOOK, "hook_session_start_clear.json", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert result.stderr.strip() == ""


@_requires_sqlite3
def test_session_start_hook_surfaces_prior_capsule(tmp_path):
    """If a prior session for this cwd has capsule_path, emit it via JSON."""
    db = _seed_journal_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO sessions (session_id, started_at, project_dir, capsule_path) "
        "VALUES (?, ?, ?, ?)",
        ("prior-session", 1.0, "/tmp/tp-l2a-fixture", "/tmp/cap.json"),
    )
    conn.commit()
    conn.close()
    result = _run_script(_SESSION_START_HOOK, "hook_session_start_resume.json", tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout.strip())
    assert "/tmp/cap.json" in payload["systemMessage"]
    assert payload["continue"] is True


@_requires_sqlite3
def test_session_start_hook_does_not_surface_prior_capsule_on_clear(tmp_path):
    """`/clear` must not inject hook output that can disrupt the TUI redraw."""
    db = _seed_journal_db(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO sessions (session_id, started_at, project_dir, capsule_path) "
        "VALUES (?, ?, ?, ?)",
        ("prior-session", 1.0, "/tmp/tp-l2a-fixture", "/tmp/cap.json"),
    )
    conn.commit()
    conn.close()
    result = _run_script(_SESSION_START_HOOK, "hook_session_start_clear.json", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    assert result.stderr.strip() == ""


@_requires_sqlite3
def test_session_start_hook_journals_when_db_present(tmp_path):
    db = _seed_journal_db(tmp_path)
    result = _run_script(_SESSION_START_HOOK, "hook_session_start_startup.json", tmp_path)
    assert result.returncode == 0
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT content FROM entries WHERE session_id = ?",
        ("fixture-session-start-startup",),
    ).fetchall()
    conn.close()
    assert rows, "SessionStart hook should have written a journal entry"
    assert any("session started" in r[0] for r in rows)


# ──────────────────────────────────────────────────────────────
# hooks #1 — PreToolUse fixture replay.
# ──────────────────────────────────────────────────────────────


def test_pre_tool_use_hook_allows_when_no_budget(tmp_path):
    _seed_journal_db(tmp_path)
    result = _run_script(_PRE_TOOL_USE_HOOK, "hook_pre_tool_use_allow.json", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


@_requires_sqlite3
def test_pre_tool_use_hook_blocks_when_over_budget(tmp_path):
    _seed_journal_db(tmp_path)
    _seed_budget_db(tmp_path, daily_cost=1.0)
    result = _run_script(
        _PRE_TOOL_USE_HOOK,
        "hook_pre_tool_use_budget_block.json",
        tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.5"},
    )
    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    payload = json.loads(result.stdout.strip())
    spec = payload["hookSpecificOutput"]
    assert spec["hookEventName"] == "PreToolUse"
    assert spec["permissionDecision"] == "deny"
    assert "budget" in spec["permissionDecisionReason"].lower()
    assert "budget" in result.stderr.lower()


@_requires_sqlite3
def test_pre_tool_use_hook_journals_trace_stamp(tmp_path):
    db = _seed_journal_db(tmp_path)
    result = _run_script(_PRE_TOOL_USE_HOOK, "hook_pre_tool_use_allow.json", tmp_path)
    assert result.returncode == 0
    # The trace stamp insert is best-effort and runs in the background;
    # poll briefly so a slow runner doesn't race the test.
    import time

    for _ in range(20):
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT content FROM entries WHERE session_id = ?",
            ("fixture-pre-tool-allow",),
        ).fetchall()
        conn.close()
        if rows:
            break
        time.sleep(0.05)
    assert rows, "PreToolUse should have stamped a journal entry"
    assert any("pre_tool" in r[0] for r in rows)


# ──────────────────────────────────────────────────────────────
# hooks #1 — PostToolUse fixture replay.
# ──────────────────────────────────────────────────────────────


@_requires_sqlite3
def test_post_tool_use_hook_journals_token_out(tmp_path):
    db = _seed_journal_db(tmp_path)
    result = _run_script(_POST_TOOL_USE_HOOK, "hook_post_tool_use_basic.json", tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""  # no hard-cap by default
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT content FROM entries WHERE session_id = ?",
        ("fixture-post-tool-basic",),
    ).fetchall()
    conn.close()
    assert rows, "PostToolUse should have written a journal entry"
    assert any("post_tool" in r[0] for r in rows)
    assert any("tokens out" in r[0] for r in rows)


def test_post_tool_use_hook_hardcap_emits_additional_context(tmp_path):
    _seed_journal_db(tmp_path)
    result = _run_script(
        _POST_TOOL_USE_HOOK,
        "hook_post_tool_use_over_budget.json",
        tmp_path,
        extra_env={"TOKENPAK_COMPANION_RESPONSE_HARDCAP_TOKENS": "10"},
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout.strip())
    spec = payload["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse"
    assert "hard cap" in spec["additionalContext"].lower()


# ──────────────────────────────────────────────────────────────
# Stop hook timeout regression.
# ──────────────────────────────────────────────────────────────


def test_stop_hook_bounds_slow_sqlite(tmp_path):
    """Stop must exit 0 even when sqlite is slow or wedged."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sqlite = fake_bin / "sqlite3"
    fake_sqlite.write_text("#!/usr/bin/env bash\nsleep 20\n")
    fake_sqlite.chmod(0o755)
    (tmp_path / "journal.db").touch()
    (tmp_path / "budget.db").touch()

    result = _run_script(
        _STOP_HOOK,
        "hook_stop_basic.json",
        tmp_path,
        extra_env={
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "TOKENPAK_COMPANION_SQLITE_TIMEOUT_SECONDS": "1",
        },
    )

    assert result.returncode == 0
    assert "session closeout" in result.stderr


# ──────────────────────────────────────────────────────────────
# ensure_hooks_feature_enabled — invokes the current Codex feature flag.
#
# Codex renamed the lifecycle-hooks feature flag to ``hooks``; the older
# name is no longer a recognized feature, so enabling it is a silent
# no-op that leaves hooks inactive. These tests pin the flag the
# companion passes to ``codex features enable``.
# ──────────────────────────────────────────────────────────────


def test_ensure_hooks_feature_enabled_uses_current_flag(monkeypatch):
    captured_cmd: "list[str]" = []

    class _Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        captured_cmd.extend(cmd)
        return _Completed()

    monkeypatch.setattr(codex_hooks.subprocess, "run", fake_run)
    # Avoid touching the real ~/.codex/config.toml during the warning-suppress step.
    monkeypatch.setattr(codex_hooks, "_suppress_unstable_warning", lambda: None)

    assert codex_hooks.ensure_hooks_feature_enabled() is True
    assert captured_cmd[:4] == ["codex", "features", "enable", "hooks"]
    assert "codex_hooks" not in captured_cmd


def test_ensure_hooks_feature_enabled_returns_false_on_nonzero(monkeypatch):
    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "unknown feature"

    monkeypatch.setattr(codex_hooks.subprocess, "run", lambda *a, **k: _Completed())
    monkeypatch.setattr(codex_hooks, "_suppress_unstable_warning", lambda: None)

    assert codex_hooks.ensure_hooks_feature_enabled() is False
