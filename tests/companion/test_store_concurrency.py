# SPDX-License-Identifier: Apache-2.0
"""Concurrency, dedupe, and truthful cost-accounting tests for the
companion journal/budget stores.

Covers the store-hardening contract:
  - WAL + busy_timeout on every companion DB opener (shared factory)
  - one canonical DDL (hook and store agree on schema)
  - content-hash dedupe keys: same event twice -> one row
  - pre-send cost estimates upsert one row per (session, day) so the daily
    gate reads true marginal spend, never a summed cumulative series
  - the daily gate prefers actual rows over estimates (no double count)
  - non-destructive start_session re-entry
  - bash hook variants write the session-binding marker atomically
  - dropped best-effort writes are logged, not silently swallowed
  - doctor warns when bash hooks are installed without the sqlite3 CLI or
    with missing script paths
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_CLAUDE_BASH_HOOK = str(
    _REPO_ROOT / "tokenpak" / "companion" / "hooks" / "pre_send.sh"
)
_CODEX_BASH_PRE_SEND = str(
    _REPO_ROOT / "tokenpak" / "companion" / "codex" / "hooks_pre_send.sh"
)
_CODEX_BASH_POST_TOOL = str(
    _REPO_ROOT / "tokenpak" / "companion" / "codex" / "hooks_post_tool_use.sh"
)

_SQLITE3_CLI = shutil.which("sqlite3")
_requires_sqlite3 = pytest.mark.skipif(
    _SQLITE3_CLI is None,
    reason="sqlite3 CLI not installed; bash hooks degrade to no-op for db ops",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_py_hook(hook_input: dict, tmp_path: Path, extra_env: dict | None = None):
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    env["TOKENPAK_COMPANION_BUDGET"] = "0"
    env["PYTHONPATH"] = str(_REPO_ROOT)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "tokenpak.companion.hooks.pre_send"],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=25,
        cwd=str(_REPO_ROOT),
        env=env,
    )


def _run_bash_hook(script: str, hook_input: dict, tmp_path: Path):
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    return subprocess.run(
        ["bash", script],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(_REPO_ROOT),
        env=env,
    )


def _make_transcript(path: Path, size_bytes: int) -> None:
    path.write_text("x" * size_bytes)


def _legacy_journal_db(db_path: Path) -> None:
    """Create a journal.db with the pre-dedupe schema (no content_hash)."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        textwrap.dedent(
            """
            CREATE TABLE sessions (
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
            CREATE TABLE entries (
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


# ---------------------------------------------------------------------------
# Connection factory: WAL + busy_timeout everywhere
# ---------------------------------------------------------------------------

def test_connect_applies_wal_and_busy_timeout(tmp_path):
    from tokenpak.companion import _sqlite

    conn = _sqlite.connect(tmp_path / "x.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
    conn.close()


def test_journal_store_db_is_wal(tmp_path):
    from tokenpak.companion.journal.store import JournalStore

    JournalStore(db_path=tmp_path / "journal.db")
    # WAL is a persistent property of the database file.
    conn = sqlite3.connect(str(tmp_path / "journal.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_budget_tracker_db_is_wal(tmp_path):
    from tokenpak.companion.budget.tracker import BudgetTracker

    BudgetTracker(db_path=tmp_path / "budget.db")
    conn = sqlite3.connect(str(tmp_path / "budget.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_hook_created_journal_matches_store_schema(tmp_path):
    """The hook and JournalStore must produce identical entries schemas —
    the divergent-DDL first-writer-wins race is fixed by sharing one DDL."""
    hook_dir = tmp_path / "hook_created"
    store_dir = tmp_path / "store_created"
    hook_dir.mkdir()
    store_dir.mkdir()

    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, 4000)
    result = _run_py_hook(
        {"session_id": "schema-1", "transcript_path": str(transcript), "prompt": "p"},
        tmp_path=hook_dir,
    )
    assert result.returncode == 0

    from tokenpak.companion.journal.store import JournalStore

    JournalStore(db_path=store_dir / "journal.db")

    def _cols(db: Path) -> list[tuple]:
        conn = sqlite3.connect(str(db))
        try:
            return [tuple(r) for r in conn.execute("PRAGMA table_info(entries)")]
        finally:
            conn.close()

    assert _cols(hook_dir / "journal.db") == _cols(store_dir / "journal.db")


# ---------------------------------------------------------------------------
# Two concurrent processes writing journal.db lose nothing
# ---------------------------------------------------------------------------

def test_two_processes_concurrent_store_writes_lose_nothing(tmp_path):
    db_path = tmp_path / "journal.db"
    n_per_proc = 20

    worker_code = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from tokenpak.companion.journal.store import JournalStore

        tag, db = sys.argv[1], sys.argv[2]
        store = JournalStore(db_path=Path(db))
        for i in range({n}):
            store.add_entry(
                session_id="proc-race",
                entry_type="auto",
                content=f"writer {{tag}} event {{i}}",
            )
        """
    ).format(n=n_per_proc)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", worker_code, tag, str(db_path)],
            cwd=str(_REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for tag in ("a", "b")
    ]
    for p in procs:
        out, err = p.communicate(timeout=25)
        assert p.returncode == 0, f"writer process failed: {err.decode()}"

    conn = sqlite3.connect(str(db_path))
    count = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE session_id = 'proc-race'"
    ).fetchone()[0]
    conn.close()
    assert count == 2 * n_per_proc, (
        f"Expected {2 * n_per_proc} rows from 2 concurrent processes, got {count}"
    )


# ---------------------------------------------------------------------------
# Dedupe: same event twice -> one row
# ---------------------------------------------------------------------------

def test_store_same_event_twice_one_row(tmp_path):
    from tokenpak.companion.journal.store import JournalStore

    store = JournalStore(db_path=tmp_path / "journal.db")
    for _ in range(2):
        store.add_entry("dup-sess", "auto", "same event", metadata={"k": 1})
    entries = store.get_entries("dup-sess", limit=10)
    assert len(entries) == 1

    # Distinct events (content or metadata differ) are all kept.
    store.add_entry("dup-sess", "auto", "same event", metadata={"k": 2})
    store.add_entry("dup-sess", "auto", "different event", metadata={"k": 1})
    assert len(store.get_entries("dup-sess", limit=10)) == 3


def test_hook_rerun_same_event_dedupes(tmp_path):
    """A retried/duplicated delivery of the same prompt event -> one journal
    row and one estimate cost row."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, 4000)
    hook_input = {
        "session_id": "dup-hook",
        "transcript_path": str(transcript),
        "prompt": "identical prompt",
    }
    for _ in range(2):
        result = _run_py_hook(hook_input, tmp_path=tmp_path)
        assert result.returncode == 0

    conn = sqlite3.connect(str(tmp_path / "journal.db"))
    journal_rows = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE session_id = 'dup-hook'"
    ).fetchone()[0]
    conn.close()
    assert journal_rows == 1

    conn = sqlite3.connect(str(tmp_path / "budget.db"))
    cost_rows = conn.execute(
        "SELECT COUNT(*), MAX(kind) FROM companion_costs WHERE session_id = 'dup-hook'"
    ).fetchone()
    conn.close()
    assert cost_rows[0] == 1
    assert cost_rows[1] == "estimate"


def test_savings_events_dedupe_but_distinct_kept(tmp_path):
    from tokenpak.companion.journal.store import JournalStore

    store = JournalStore(db_path=tmp_path / "journal.db")
    for _ in range(3):  # duplicated delivery of one savings event
        store.record_savings("sav-sess", "prune_context", 100, 0.0003)
    store.record_savings("sav-sess", "prune_context", 250, 0.00075)
    entries = store.get_entries("sav-sess", entry_type="companion_savings", limit=10)
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# Cost accounting: one estimate row per (session, day), gate prefers actuals
# ---------------------------------------------------------------------------

def test_estimate_upserts_single_row_per_session_day(tmp_path):
    """Growing transcript across prompts -> the estimate row refreshes in
    place; the daily gate reads the latest estimate, never the summed
    cumulative series, and never exceeds the final transcript estimate."""
    session = "grow-sess"
    transcript = tmp_path / "t.jsonl"
    sizes = (4000, 8000, 12000)
    per_prompt_costs = []
    for size in sizes:
        _make_transcript(transcript, size)
        result = _run_py_hook(
            {"session_id": session, "transcript_path": str(transcript), "prompt": "p"},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0
        tokens = size // 4 + len("p") // 4
        per_prompt_costs.append(tokens * 3.0 / 1_000_000)

    conn = sqlite3.connect(str(tmp_path / "budget.db"))
    rows = conn.execute(
        "SELECT estimated_cost, kind FROM companion_costs WHERE session_id = ?",
        (session,),
    ).fetchall()
    conn.close()
    assert len(rows) == 1, f"expected one upserted estimate row, got {rows}"
    assert rows[0][1] == "estimate"
    final_estimate = per_prompt_costs[-1]
    assert rows[0][0] == pytest.approx(final_estimate, abs=1e-9)

    from tokenpak.companion.budget.tracker import BudgetTracker

    tracker = BudgetTracker(db_path=tmp_path / "budget.db", daily_budget=0.0)
    daily = tracker.estimate(input_tokens=0).daily_total_usd
    assert daily == pytest.approx(final_estimate, abs=1e-4)
    assert daily < sum(per_prompt_costs), (
        "daily gate must not sum the cumulative estimate series"
    )


def test_daily_gate_prefers_actuals_over_estimates(tmp_path):
    """When a session has both a pre-send estimate row and actual rows, the
    gate sums only the actuals for that session (no double count). Sessions
    with only an estimate still contribute their estimate."""
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, 400_000)  # 100k tokens -> $0.30 estimate
    result = _run_py_hook(
        {"session_id": "mix-sess", "transcript_path": str(transcript), "prompt": "p"},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0

    from tokenpak.companion.budget.tracker import BudgetTracker

    tracker = BudgetTracker(db_path=tmp_path / "budget.db", daily_budget=0.0)
    # Two actual rows for the same session: 1M + 2M input tokens at $3/M.
    tracker.record(input_tokens=1_000_000, model="sonnet", session_id="mix-sess")
    tracker.record(input_tokens=2_000_000, model="sonnet", session_id="mix-sess")

    daily = tracker.estimate(input_tokens=0).daily_total_usd
    assert daily == pytest.approx(9.0, abs=1e-3), (
        "gate must sum actuals only for a session that has actuals "
        f"(got {daily}; estimate would add ~0.30)"
    )

    # An estimate-only session still counts via its latest estimate.
    result = _run_py_hook(
        {"session_id": "est-only", "transcript_path": str(transcript), "prompt": "p"},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0
    daily2 = tracker.estimate(input_tokens=0).daily_total_usd
    assert daily2 == pytest.approx(9.30, abs=1e-2)


def test_legacy_budget_rows_not_summed_per_prompt(tmp_path):
    """Legacy databases already contain the one-row-per-prompt cumulative
    estimate series (kind IS NULL, model=''). The gate must read the
    session's largest estimate, not the sum."""
    db_path = tmp_path / "budget.db"
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE companion_costs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, "
        "date TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', "
        "model TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0, "
        "cached_tokens INTEGER NOT NULL DEFAULT 0, "
        "output_tokens INTEGER NOT NULL DEFAULT 0, "
        "estimated_cost REAL NOT NULL DEFAULT 0.0)"
    )
    for i, cost in enumerate((1.0, 1.5, 2.0)):  # growing cumulative series
        conn.execute(
            "INSERT INTO companion_costs (timestamp, date, session_id, model, "
            "estimated_cost) VALUES (?, ?, 'legacy-sess', '', ?)",
            (float(i), today, cost),
        )
    conn.commit()
    conn.close()

    from tokenpak.companion.budget.tracker import BudgetTracker

    tracker = BudgetTracker(db_path=db_path, daily_budget=0.0)
    daily = tracker.estimate(input_tokens=0).daily_total_usd
    assert daily == pytest.approx(2.0, abs=1e-4), (
        f"expected latest estimate 2.0, not summed series 4.5 (got {daily})"
    )


# ---------------------------------------------------------------------------
# start_session re-entry is non-destructive
# ---------------------------------------------------------------------------

def test_start_session_reentry_preserves_totals(tmp_path):
    from tokenpak.companion.journal.store import JournalStore

    store = JournalStore(db_path=tmp_path / "journal.db")
    store.start_session("re-sess", project_dir="/proj/a", model="model-a")

    conn = sqlite3.connect(str(tmp_path / "journal.db"))
    conn.execute(
        "UPDATE sessions SET total_requests = 7, total_cost_usd = 1.25 "
        "WHERE session_id = 're-sess'"
    )
    conn.commit()
    conn.close()
    started_at = store.get_session("re-sess").started_at

    # Re-entry without arguments must keep everything.
    store.start_session("re-sess")
    rec = store.get_session("re-sess")
    assert rec.total_requests == 7
    assert rec.total_cost_usd == pytest.approx(1.25)
    assert rec.project_dir == "/proj/a"
    assert rec.model == "model-a"
    assert rec.started_at == pytest.approx(started_at)

    # Re-entry WITH new descriptive fields refreshes only those.
    store.start_session("re-sess", project_dir="/proj/b")
    rec = store.get_session("re-sess")
    assert rec.project_dir == "/proj/b"
    assert rec.model == "model-a"
    assert rec.total_requests == 7
    assert rec.started_at == pytest.approx(started_at)


# ---------------------------------------------------------------------------
# Legacy journal migration is additive and non-destructive
# ---------------------------------------------------------------------------

def test_legacy_journal_migrates_nondestructively(tmp_path):
    db_path = tmp_path / "journal.db"
    _legacy_journal_db(db_path)
    # Pre-existing duplicate rows (the historical defect) must survive.
    conn = sqlite3.connect(str(db_path))
    for _ in range(2):
        conn.execute(
            "INSERT INTO entries (session_id, timestamp, entry_type, content) "
            "VALUES ('old-sess', 1.0, 'auto', 'legacy duplicate')"
        )
    conn.commit()
    conn.close()

    from tokenpak.companion.journal.store import JournalStore

    store = JournalStore(db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
    legacy_count = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE session_id = 'old-sess'"
    ).fetchone()[0]
    conn.close()
    assert "content_hash" in cols
    assert legacy_count == 2, "migration must not dedupe or rewrite legacy rows"

    # New writes dedupe.
    for _ in range(2):
        store.add_entry("old-sess", "auto", "new event")
    conn = sqlite3.connect(str(db_path))
    new_count = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE session_id = 'old-sess' "
        "AND content = 'new event'"
    ).fetchone()[0]
    conn.close()
    assert new_count == 1


# ---------------------------------------------------------------------------
# Session-binding marker: bash variants write it atomically
# ---------------------------------------------------------------------------

def test_claude_bash_hook_writes_marker(tmp_path):
    transcript = tmp_path / "t.jsonl"
    _make_transcript(transcript, 4000)
    result = _run_bash_hook(
        _CLAUDE_BASH_HOOK,
        {"session_id": "bash-sess-1", "transcript_path": str(transcript)},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0
    marker = tmp_path / "run" / "current-session"
    assert marker.exists(), "bash pre_send variant must write the marker"
    assert marker.read_text() == "bash-sess-1"
    leftovers = list((tmp_path / "run").glob("current-session.*.tmp"))
    assert not leftovers, f"atomic tmp+mv must not leave residue: {leftovers}"


def test_claude_bash_hook_marker_written_even_on_zero_token_early_exit(tmp_path):
    """The marker write must happen before the zero-token early exit —
    otherwise the first prompt of a fresh session (no transcript yet)
    leaves a stale marker from the previous session."""
    result = _run_bash_hook(
        _CLAUDE_BASH_HOOK,
        {"session_id": "bash-sess-2", "transcript_path": ""},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0
    marker = tmp_path / "run" / "current-session"
    assert marker.exists()
    assert marker.read_text() == "bash-sess-2"


def test_codex_bash_hook_writes_marker(tmp_path):
    result = _run_bash_hook(
        _CODEX_BASH_PRE_SEND,
        {"session_id": "codex-sess-1", "transcript_path": "", "model": "m"},
        tmp_path=tmp_path,
    )
    assert result.returncode == 0
    marker = tmp_path / "run" / "current-session"
    assert marker.exists(), "codex bash pre_send variant must write the marker"
    assert marker.read_text() == "codex-sess-1"


def test_marker_overwritten_by_newer_session(tmp_path):
    for sid in ("first-sess", "second-sess"):
        result = _run_bash_hook(
            _CLAUDE_BASH_HOOK,
            {"session_id": sid, "transcript_path": ""},
            tmp_path=tmp_path,
        )
        assert result.returncode == 0
    assert (tmp_path / "run" / "current-session").read_text() == "second-sess"


# ---------------------------------------------------------------------------
# Codex bash hooks: dedupe key effective via sqlite3 CLI (CI runners)
# ---------------------------------------------------------------------------

@_requires_sqlite3
def test_codex_post_tool_use_dedupes_identical_events(tmp_path):
    _legacy_journal_db(tmp_path / "journal.db")  # hook must migrate additively

    payload = {
        "session_id": "codex-dedupe",
        "tool_name": "shell",
        "tool_use_id": "use-1",
        "turn_id": "turn-1",
        "tool_response": "ok",
    }
    for _ in range(2):  # duplicated delivery of the same tool-use event
        result = _run_bash_hook(_CODEX_BASH_POST_TOOL, payload, tmp_path=tmp_path)
        assert result.returncode == 0

    distinct = dict(payload, tool_use_id="use-2")
    result = _run_bash_hook(_CODEX_BASH_POST_TOOL, distinct, tmp_path=tmp_path)
    assert result.returncode == 0

    conn = sqlite3.connect(str(tmp_path / "journal.db"))
    rows = conn.execute(
        "SELECT content, content_hash FROM entries WHERE session_id = 'codex-dedupe'"
    ).fetchall()
    conn.close()
    assert len(rows) == 2, (
        f"identical events must collapse, distinct tool uses kept: {rows}"
    )
    assert all(r[1] for r in rows), "bash writers must populate content_hash"


# ---------------------------------------------------------------------------
# Dropped writes are logged, never silently swallowed
# ---------------------------------------------------------------------------

def test_dropped_journal_write_is_logged(tmp_path):
    """With journal.db exclusively locked longer than the busy timeout, the
    hook must still exit 0 (fail-open) but the drop must be visible in
    run/dropped-writes.log and on stderr."""
    from tokenpak.companion.journal.store import JournalStore

    JournalStore(db_path=tmp_path / "journal.db")  # create schema first

    blocker = sqlite3.connect(str(tmp_path / "journal.db"))
    blocker.execute("BEGIN IMMEDIATE")  # hold the write lock
    try:
        transcript = tmp_path / "t.jsonl"
        _make_transcript(transcript, 4000)
        result = _run_py_hook(
            {"session_id": "locked-sess", "transcript_path": str(transcript),
             "prompt": "p"},
            tmp_path=tmp_path,
        )
    finally:
        blocker.rollback()
        blocker.close()

    assert result.returncode == 0, "hook must stay fail-open under lock"
    log = tmp_path / "run" / "dropped-writes.log"
    assert log.exists(), "dropped write must be recorded in dropped-writes.log"
    assert "journal_entry" in log.read_text()
    assert "dropped" in result.stderr


# ---------------------------------------------------------------------------
# Doctor: companion hook integrity warnings
# ---------------------------------------------------------------------------

def _write_claude_hook_settings(home: Path, command: str) -> None:
    settings = {
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "", "hooks": [{"type": "command", "command": command}]}
            ]
        }
    }
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text(json.dumps(settings))


def test_doctor_warns_when_sqlite3_missing_with_bash_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    hook = tmp_path / "tokenpak" / "hooks" / "pre_send.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    _write_claude_hook_settings(tmp_path, f"bash {hook}")
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)

    from tokenpak.cli.commands.doctor import companion_hook_integrity

    results = companion_hook_integrity()
    assert any(
        status == "warn" and "sqlite3" in message for status, message, _ in results
    ), f"expected sqlite3 WARN, got {results}"


def test_doctor_warns_on_missing_hook_script_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    missing = tmp_path / "tokenpak" / "hooks" / "pre_send.sh"  # never created
    _write_claude_hook_settings(tmp_path, f"bash {missing}")
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/sqlite3")

    from tokenpak.cli.commands.doctor import companion_hook_integrity

    results = companion_hook_integrity()
    assert any(
        status == "warn" and "missing" in message for status, message, _ in results
    ), f"expected missing-path WARN, got {results}"


def test_doctor_reads_codex_hooks_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    missing = tmp_path / "tokenpak" / "codex" / "hooks_stop.sh"
    codex_cfg = {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": f"bash {missing}"}]}]
        }
    }
    (tmp_path / ".codex").mkdir(parents=True)
    (tmp_path / ".codex" / "hooks.json").write_text(json.dumps(codex_cfg))
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/sqlite3")

    from tokenpak.cli.commands.doctor import companion_hook_integrity

    results = companion_hook_integrity()
    assert any(
        status == "warn" and "missing" in message for status, message, _ in results
    ), f"expected missing-path WARN from codex config, got {results}"


def test_doctor_passes_with_healthy_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    hook = tmp_path / "tokenpak" / "hooks" / "pre_send.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    _write_claude_hook_settings(tmp_path, f"bash {hook}")
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/sqlite3")

    from tokenpak.cli.commands.doctor import companion_hook_integrity

    results = companion_hook_integrity()
    assert results and all(status == "pass" for status, _, _ in results), results


def test_doctor_passes_when_no_hooks_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)

    from tokenpak.cli.commands.doctor import companion_hook_integrity

    results = companion_hook_integrity()
    assert results and all(status == "pass" for status, _, _ in results), results
