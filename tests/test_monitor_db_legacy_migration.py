"""Tests for scripts/migrate_monitor_db.py — legacy monitor.db detect/merge/archive.

Covers the three supported legacy input states:
  * legacy at ``~/tokenpak/`` only
  * legacy at ``~/.tokenpak/`` only
  * legacy at both

plus dry-run-by-default, idempotency, archive-never-delete, dedupe, schema
mismatch surfacing, and the dangling-legacy-symlink case.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_monitor_db.py"


def _load_migrator():
    spec = importlib.util.spec_from_file_location("migrate_monitor_db", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migrator = _load_migrator()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost REAL,
    session_id TEXT DEFAULT ''
)
"""


def _make_db(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(_SCHEMA)
    conn.executemany(
        "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, "
        "estimated_cost, session_id) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    finally:
        conn.close()


_ROWS_A = [
    ("2026-06-01T10:00:00", "claude-opus-4-8", 100, 50, 0.01, "s1"),
    ("2026-06-01T11:00:00", "claude-sonnet-4-6", 200, 80, 0.02, "s2"),
]
_ROWS_B = [
    ("2026-06-02T09:00:00", "claude-haiku-4-5", 50, 20, 0.001, "s3"),
]


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


# ---------------------------------------------------------------------------
# Three legacy states
# ---------------------------------------------------------------------------


def test_legacy_at_predot_only(home):
    src = home / "tokenpak" / "monitor.db"
    _make_db(src, _ROWS_A)
    target = migrator.canonical_target(home)

    summary = migrator.migrate(target, migrator.legacy_candidates(home), apply=True)

    assert target.exists()
    assert _count(target) == 2
    # source renamed to a .legacy-* archive (never deleted)
    assert not src.exists()
    assert list((home / "tokenpak").glob("monitor.db.legacy-*"))
    assert any("legacy-" in a for _, a in summary["archived"])
    assert summary["rows_inserted"] == 2


def test_legacy_at_dotfile_only(home):
    src = home / ".tokenpak" / "monitor.db"
    _make_db(src, _ROWS_A)
    target = migrator.canonical_target(home)

    migrator.migrate(target, migrator.legacy_candidates(home), apply=True)

    assert _count(target) == 2
    assert not src.exists()
    # archive exists alongside
    archives = list((home / ".tokenpak").glob("monitor.db.legacy-*"))
    assert len(archives) == 1


def test_legacy_at_both_merges_and_dedupes(home):
    # Overlapping row between the two sources must dedupe to a single copy.
    overlap = ("2026-06-01T10:00:00", "claude-opus-4-8", 100, 50, 0.01, "s1")
    _make_db(home / "tokenpak" / "monitor.db", _ROWS_A)
    _make_db(home / ".tokenpak" / "monitor.db", [overlap] + _ROWS_B)
    target = migrator.canonical_target(home)

    summary = migrator.migrate(target, migrator.legacy_candidates(home), apply=True)

    # _ROWS_A (2) + _ROWS_B (1); overlap deduped -> 3 unique rows total
    assert _count(target) == 3
    assert summary["rows_inserted"] == 3
    assert not (home / "tokenpak" / "monitor.db").exists()
    assert not (home / ".tokenpak" / "monitor.db").exists()


# ---------------------------------------------------------------------------
# Safety contract
# ---------------------------------------------------------------------------


def test_dry_run_is_default_and_touches_nothing(home):
    src = home / "tokenpak" / "monitor.db"
    _make_db(src, _ROWS_A)
    target = migrator.canonical_target(home)

    summary = migrator.migrate(target, migrator.legacy_candidates(home))  # no apply

    assert summary["apply"] is False
    assert not target.exists()  # canonical NOT created
    assert src.exists()  # source untouched
    assert _count(src) == 2


def test_idempotent_second_run_is_noop(home):
    src = home / "tokenpak" / "monitor.db"
    _make_db(src, _ROWS_A)
    target = migrator.canonical_target(home)

    migrator.migrate(target, migrator.legacy_candidates(home), apply=True)
    rows_after_first = _count(target)

    summary2 = migrator.migrate(target, migrator.legacy_candidates(home), apply=True)

    assert _count(target) == rows_after_first
    assert summary2["rows_inserted"] == 0
    assert summary2["merged"] == []


def test_never_recreates_predot_target(home):
    src = home / ".tokenpak" / "monitor.db"
    _make_db(src, _ROWS_A)
    target = migrator.canonical_target(home)

    migrator.migrate(target, migrator.legacy_candidates(home), apply=True)

    # The pre-dot legacy location must never be (re)created by the migration.
    assert not (home / "tokenpak" / "monitor.db").exists()


# ---------------------------------------------------------------------------
# Schema mismatch + dangling symlink
# ---------------------------------------------------------------------------


def test_schema_mismatch_is_skipped_not_dropped(home):
    bad = home / "tokenpak" / "monitor.db"
    bad.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(bad))
    conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, junk TEXT)")
    conn.commit()
    conn.close()
    target = migrator.canonical_target(home)

    summary = migrator.migrate(target, migrator.legacy_candidates(home), apply=True)

    assert summary["skipped"]  # surfaced, not silently merged
    assert summary["exit_code"] == 2
    assert bad.exists()  # NOT archived, NOT deleted


def test_incompatible_existing_target_leaves_valid_source_untouched(home):
    source = home / "tokenpak" / "monitor.db"
    _make_db(source, _ROWS_A)
    target = migrator.canonical_target(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, junk TEXT)")
    conn.commit()
    conn.close()

    summary = migrator.migrate(
        target,
        migrator.legacy_candidates(home),
        apply=True,
        today="2026-07-19",
    )

    assert summary["exit_code"] == 2
    assert summary["rows_inserted"] == 0
    assert summary["archived"] == []
    assert source.exists()
    assert _count(source) == 2
    assert not list(source.parent.glob("monitor.db.legacy-*"))


def test_dangling_legacy_symlink_archived_not_followed(home):
    # ~/.tokenpak/monitor.db -> ~/tokenpak/monitor.db (target absent)
    (home / ".tokenpak").mkdir(parents=True)
    link = home / ".tokenpak" / "monitor.db"
    link.symlink_to(home / "tokenpak" / "monitor.db")
    target = migrator.canonical_target(home)

    summary = migrator.migrate(target, migrator.legacy_candidates(home), apply=True)

    assert not link.exists()  # archived in place
    assert not (home / "tokenpak" / "monitor.db").exists()  # never created
    assert any("legacy-" in a for _, a in summary["archived"])


def test_cli_main_dry_run_exit_zero(home):
    src = home / "tokenpak" / "monitor.db"
    _make_db(src, _ROWS_A)

    rc = migrator.main(["--home", str(home)])

    assert rc == 0
    assert src.exists()  # dry-run default: untouched
    assert not migrator.canonical_target(home).exists()
