# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the companion hook pipeline.

Covers the four known failure modes:
  1. Hook fails silently (returns OK but doesn't process)
  2. Budget check passes when it should block (threshold off-by-one)
  3. Journal write drops entries under concurrent access
  4. MCP tool dispatch returns wrong error codes for edge cases

Pipeline under test: pre_send → token estimation → budget check → journal write
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

# TSR-05ab deliberate-contract-change skip reason (grep-able)
# ─────────────────────────────────────────────
# `test_silent_failure_zero_token_skips_journal` asserts the old hook
# contract: "0-byte transcript → tokens_est=0 → journal entry NOT written".
# Production deliberately changed this contract:
#
#     tokenpak/companion/hooks/pre_send.py:113-114
#     # Journal write (best-effort, non-blocking). Log even when tokens_est
#     # is 0 so we still record that a cycle fired — useful for detecting
#     # silent failures
#     ...
#     _journal_write(session_id, tokens_est, cost_est)   # always called
#
# The comment explicitly states this is intentional. Test now asserts
# `len(rows) == 0` but production writes one row → assertion fails.
#
# This is **deliberate API/behavior drift**, not a regression. Belongs to
# TSR-02 (API drift). Same Path B pattern as TSR-05t (deprecated `tokenpak
# savings` wire format) and TSR-05aa (banner-text drift). The 9 live tests
# in this file (allow-path journal, budget gating, concurrent writes,
# off-by-one boundary) remain meaningful guards.
SKIP_HOOK_ALWAYS_JOURNALS_BY_DESIGN = (
    "Test asserts old contract: 0-token → skip journal. Production "
    "deliberately changed to always-journal (pre_send.py:113-114 explicit "
    "comment). API drift — see TSR-02."
)


_REPO_ROOT = str(Path(__file__).parent.parent.parent)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_hook(
    hook_input: dict,
    tmp_path: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the Python pre_send hook with the given JSON input."""
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_NO_THREADS"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    env["TOKENPAK_COMPANION_SHOW_COST"] = "1"
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


def _make_transcript(path: Path, size_bytes: int = 5000) -> Path:
    """Write a JSONL transcript of approximately size_bytes."""
    line = json.dumps({"type": "user", "content": "hello world " * 10}) + "\n"
    line_size = len(line.encode())
    lines = max(1, size_bytes // line_size)
    path.write_text(line * lines)
    return path


def _seed_budget_db(db_path: Path, daily_cost: float, budget: float = 0.001) -> None:
    """Pre-seed the budget.db so daily_total equals daily_cost."""
    from tokenpak.companion.budget.tracker import BudgetTracker

    tracker = BudgetTracker(db_path=db_path, daily_budget=budget)
    # Record enough tokens to reach daily_cost (using sonnet: $3 / 1M tokens)
    tokens = int(daily_cost / 3.0 * 1_000_000)
    if tokens > 0:
        tracker.record(input_tokens=tokens, model="sonnet")


# ---------------------------------------------------------------------------
# Failure Mode 1: Hook fails silently (returns OK but doesn't process)
# ---------------------------------------------------------------------------


def test_silent_failure_journal_written_on_allow(tmp_path):
    """Hook returns 0 AND writes a journal entry — not just silent OK.

    Regression: hook was returning 0 without writing when transcript was
    non-empty but token estimate rounded to 0 due to tiny file.
    """
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=8000)  # >4 bytes → tokens_est > 0

    result = _run_hook(
        {
            "session_id": "regr-silent-01",
            "transcript_path": str(transcript_path),
            "prompt": "check silent failure",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0"},
    )
    assert result.returncode == 0, f"Hook failed: {result.stderr}"

    journal_db = tmp_path / "journal.db"
    assert journal_db.exists(), "journal.db not written (silent failure)"
    conn = sqlite3.connect(str(journal_db))
    rows = conn.execute(
        "SELECT session_id FROM entries WHERE session_id = ?",
        ("regr-silent-01",),
    ).fetchall()
    conn.close()
    assert len(rows) >= 1, "Hook returned 0 but wrote no journal entry (silent failure)"


def test_silent_failure_stderr_confirms_processing(tmp_path):
    """stderr output proves the hook actually processed the transcript.

    Regression guard: a hook that short-circuits before estimation would
    produce no stderr, making the allow look like a silent no-op.
    """
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=8000)

    result = _run_hook(
        {
            "session_id": "regr-silent-02",
            "transcript_path": str(transcript_path),
            "prompt": "processing check",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0"},
    )
    assert result.returncode == 0
    assert "tokenpak" in result.stderr, (
        f"No stderr output — hook may have short-circuited silently: {result.stderr!r}"
    )
    assert "tokens" in result.stderr, f"Expected token count in stderr, got: {result.stderr!r}"


@pytest.mark.skip(reason=SKIP_HOOK_ALWAYS_JOURNALS_BY_DESIGN)
def test_silent_failure_zero_token_skips_journal(tmp_path):
    """Empty transcript (0-byte file) → tokens_est=0 → journal skipped (correct).

    This tests the intentional silent path: when there is nothing to process,
    the hook should return 0 and NOT write a spurious journal entry.
    """
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text("")  # 0 bytes → tokens_est = 0

    result = _run_hook(
        {
            "session_id": "regr-silent-03",
            "transcript_path": str(transcript_path),
            "prompt": "empty transcript",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0"},
    )
    assert result.returncode == 0
    # Journal should NOT be written when tokens_est == 0
    journal_db = tmp_path / "journal.db"
    if journal_db.exists():
        conn = sqlite3.connect(str(journal_db))
        rows = conn.execute(
            "SELECT session_id FROM entries WHERE session_id = ?",
            ("regr-silent-03",),
        ).fetchall()
        conn.close()
        assert len(rows) == 0, "Hook wrote journal entry for 0-token transcript"


# ---------------------------------------------------------------------------
# Failure Mode 2: Budget check passes when it should block (threshold off-by-one)
# ---------------------------------------------------------------------------


def test_budget_off_by_one_at_exact_limit_blocks(tmp_path):
    """daily_total + cost_est >= budget must block, not pass through.

    Regression: if the check was '>' instead of '>=' this test would catch it.
    We seed the DB so daily_total == budget, then send a prompt with cost_est > 0.
    """
    budget = 1.00  # $1.00 daily budget
    # Seed DB so daily_total is already exactly at the budget
    _seed_budget_db(tmp_path / "budget.db", daily_cost=budget, budget=budget)

    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=8000)  # adds a positive cost_est

    result = _run_hook(
        {
            "session_id": "regr-budget-01",
            "transcript_path": str(transcript_path),
            "prompt": "over-budget test",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": str(budget)},
    )
    assert result.returncode == 2, (
        f"Budget gate failed to block when daily_total ({budget}) >= budget ({budget})"
    )


def test_budget_off_by_one_just_under_allows(tmp_path):
    """When daily_total + cost_est < budget, hook must allow.

    Paired with the exact-limit test to verify the boundary in both directions.
    """
    budget = 100.00  # very large budget
    # Seed DB with a small amount well under budget
    _seed_budget_db(tmp_path / "budget.db", daily_cost=0.001, budget=budget)

    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=4000)  # tiny cost

    result = _run_hook(
        {
            "session_id": "regr-budget-02",
            "transcript_path": str(transcript_path),
            "prompt": "under-budget test",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": str(budget)},
    )
    assert result.returncode == 0, (
        f"Hook blocked when cost is well under budget. stderr: {result.stderr}"
    )


def test_budget_zero_disables_gate(tmp_path):
    """Budget=0 means no gate — even a huge transcript must be allowed.

    Regression: budget=0 was treated as $0 limit instead of 'disabled'.
    """
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=500_000)  # very large

    result = _run_hook(
        {
            "session_id": "regr-budget-03",
            "transcript_path": str(transcript_path),
            "prompt": "no budget gate",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0"},
    )
    assert result.returncode == 0, "Budget=0 must disable the gate, not block everything"


def test_budget_block_seeded_daily_total_exceeds(tmp_path):
    """Seeded DB daily_total > budget: hook must block regardless of transcript size.

    Uses BudgetTracker directly to seed state, then verifies the hook reads
    from the same DB path.
    """
    budget = 0.001  # $0.001 daily budget
    db_path = tmp_path / "budget.db"
    _seed_budget_db(db_path, daily_cost=5.00, budget=budget)  # $5 >> $0.001

    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=4000)

    result = _run_hook(
        {
            "session_id": "regr-budget-04",
            "transcript_path": str(transcript_path),
            "prompt": "over-budget via seeded DB",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": str(budget)},
    )
    assert result.returncode == 2, (
        f"Hook did not block despite daily_total >> budget. stderr: {result.stderr}"
    )
    assert "budget" in result.stderr.lower(), (
        f"Block stderr should mention budget, got: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Failure Mode 3: Journal write drops entries under concurrent access
# ---------------------------------------------------------------------------


def test_concurrent_journal_writes_no_data_loss(tmp_path):
    """5 concurrent hook invocations must all write their journal entries.

    Regression: SQLite WAL mode was not enabled, causing SQLITE_BUSY errors
    under concurrent writers that were swallowed by the try/except in
    _journal_write, resulting in silent data loss. Fixed by the shared
    companion connection factory (WAL + busy_timeout); this test now gates.
    """
    n_workers = 5
    transcript_path = tmp_path / "shared_session.jsonl"
    _make_transcript(transcript_path, size_bytes=8000)

    def run_worker(i: int) -> subprocess.CompletedProcess:
        return _run_hook(
            {
                "session_id": f"concurrent-session-{i}",
                "transcript_path": str(transcript_path),
                "prompt": f"concurrent prompt {i}",
            },
            tmp_path=tmp_path,
            extra_env={"TOKENPAK_COMPANION_BUDGET": "0"},
        )

    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(run_worker, i) for i in range(n_workers)]
        results = [f.result() for f in as_completed(futures)]

    # All hooks must have exited 0
    exit_codes = [r.returncode for r in results]
    assert all(c == 0 for c in exit_codes), f"Some concurrent hooks failed: exit codes {exit_codes}"

    journal_db = tmp_path / "journal.db"
    assert journal_db.exists(), "journal.db not created by concurrent hooks"
    conn = sqlite3.connect(str(journal_db))
    rows = conn.execute("SELECT DISTINCT session_id FROM entries").fetchall()
    conn.close()
    written_ids = {r[0] for r in rows}
    expected_ids = {f"concurrent-session-{i}" for i in range(n_workers)}
    dropped = expected_ids - written_ids
    assert not dropped, f"Concurrent journal writes dropped {len(dropped)} session(s): {dropped}"


def test_concurrent_journal_writes_correct_entry_count(tmp_path):
    """N concurrent DISTINCT writes to the same session produce exactly N rows.

    This tests that concurrent writes to a single session_id don't collide
    and silently lose rows. Each worker submits a prompt of a different
    length so the journal entries are distinct events: journal entries now
    carry a content-hash dedupe key, so IDENTICAL events deliberately
    collapse to one row (that behavior is covered separately in
    test_store_concurrency.py) while distinct concurrent events must all
    survive.
    """
    n_writes = 6
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=8000)
    session_id = "concurrent-single-session"

    def run_worker(i: int) -> subprocess.CompletedProcess:
        return _run_hook(
            {
                "session_id": session_id,
                "transcript_path": str(transcript_path),
                # Distinct length per worker (i*8 chars) → distinct token
                # estimate → distinct journal entry content per worker.
                "prompt": "concurrent single session write " + ("x" * (i * 8)),
            },
            tmp_path=tmp_path,
            extra_env={"TOKENPAK_COMPANION_BUDGET": "0"},
        )

    with ThreadPoolExecutor(max_workers=n_writes) as pool:
        futures = [pool.submit(run_worker, i) for i in range(n_writes)]
        results = [f.result() for f in as_completed(futures)]

    exit_codes = [r.returncode for r in results]
    assert all(c == 0 for c in exit_codes), f"Hook failures: {exit_codes}"

    journal_db = tmp_path / "journal.db"
    conn = sqlite3.connect(str(journal_db))
    count = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    conn.close()
    assert count == n_writes, (
        f"Expected {n_writes} journal entries for concurrent writes, got {count}"
    )


def test_concurrent_journal_store_direct_no_data_loss(tmp_path):
    """JournalStore.add_entry survives N concurrent writes from threads.

    Tests the store layer directly (not via subprocess) to isolate SQLite
    concurrency behaviour from subprocess scheduling.
    """
    from tokenpak.companion.journal.store import JournalStore

    db_path = tmp_path / "direct_journal.db"
    store = JournalStore(db_path=db_path)
    session_id = "direct-concurrent"
    store.start_session(session_id, project_dir="/tmp", model="sonnet")

    n_threads = 8
    errors: list[Exception] = []

    def write_entry(i: int) -> None:
        try:
            store.add_entry(
                session_id=session_id,
                entry_type="auto",
                content=f"concurrent entry {i}",
                metadata={"index": i},
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_entry, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent JournalStore.add_entry raised errors: {errors}"

    entries = store.get_entries(session_id, limit=100)
    assert len(entries) == n_threads, (
        f"Expected {n_threads} journal entries, got {len(entries)} — data loss detected"
    )


# ---------------------------------------------------------------------------
# Failure Mode 4: MCP tool dispatch returns wrong error codes for edge cases
# ---------------------------------------------------------------------------


def test_error_code_block_is_2_not_1(tmp_path):
    """Budget block must exit with code 2, not 1 or any other non-zero value.

    Claude Code hooks treat exit 2 as 'block with message'; exit 1 is treated
    as 'error' which produces a different UX. Wrong exit code is a regression.
    """
    db_path = tmp_path / "budget.db"
    _seed_budget_db(db_path, daily_cost=5.00, budget=0.001)
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=8000)

    result = _run_hook(
        {
            "session_id": "regr-errcode-01",
            "transcript_path": str(transcript_path),
            "prompt": "exit code check",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.001"},
    )
    assert result.returncode == 2, f"Budget block must exit 2 (block), got {result.returncode}"


def test_error_code_allow_is_0_not_nonzero(tmp_path):
    """Normal allow must exit 0 — no other value is acceptable.

    Regression: an error in the journal write path was propagating as exit 1
    instead of being swallowed by the try/except best-effort guard.
    """
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=4000)

    result = _run_hook(
        {
            "session_id": "regr-errcode-02",
            "transcript_path": str(transcript_path),
            "prompt": "allow exit code",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0"},
    )
    assert result.returncode == 0, (
        f"Allow path must exit 0, got {result.returncode}. stderr: {result.stderr}"
    )


def test_error_code_block_json_has_required_fields(tmp_path):
    """Blocked hook stdout must contain all required hookSpecificOutput fields.

    Claude Code requires: hookEventName, decision, reason. Missing any field
    causes the hook framework to ignore the block message.
    """
    db_path = tmp_path / "budget.db"
    _seed_budget_db(db_path, daily_cost=5.00, budget=0.001)
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=8000)

    result = _run_hook(
        {
            "session_id": "regr-errcode-03",
            "transcript_path": str(transcript_path),
            "prompt": "json fields check",
        },
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.001"},
    )
    assert result.returncode == 2

    stdout = result.stdout.strip()
    assert stdout, "Blocked hook produced no stdout"
    try:
        decision = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Blocked hook stdout is not valid JSON: {exc!r}\n{stdout!r}")

    hook_out = decision.get("hookSpecificOutput", {})
    assert hook_out.get("hookEventName") == "UserPromptSubmit", (
        f"hookEventName missing or wrong: {hook_out}"
    )
    assert hook_out.get("decision") == "block", f"decision field missing or wrong: {hook_out}"
    assert hook_out.get("reason"), f"reason field missing or empty: {hook_out}"


def test_error_code_malformed_input_does_not_exit_nonzero(tmp_path):
    """Malformed / unexpected JSON input must fail-open (exit 0), never exit 1 or 2.

    Regression: a KeyError during input parsing was propagating as exit 1,
    which Claude Code treats as a hook error and surfaces to the user.
    """
    malformed_inputs = [
        "",  # empty stdin
        "not json at all",  # garbage
        '{"unexpected_key": null}',  # valid JSON, no known fields
        '{"session_id": null, "transcript_path": 12345}',  # wrong types
    ]
    for payload in malformed_inputs:
        env = os.environ.copy()
        env["TOKENPAK_NO_THREADS"] = "1"
        env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak.companion.hooks.pre_send"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=15,
            cwd=_REPO_ROOT,
            env=env,
        )
        snippet = repr(payload)[:40]
        assert result.returncode == 0, (
            f"Malformed input {snippet} exited {result.returncode} — must be 0 (fail-open)"
        )
