# SPDX-License-Identifier: Apache-2.0
"""``tokenpak home migrate``: merge a split home, never touch the legacy tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from tokenpak.cli.commands import home_migrate
from tokenpak.cli.commands.home_cmd import cmd_home_migrate
from tokenpak.cli.exit_codes import EXIT_BUSY, EXIT_FAILURE

JOURNAL = """
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, started_at REAL NOT NULL);
CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    entry_type TEXT NOT NULL, content TEXT NOT NULL DEFAULT '', content_hash TEXT);
"""


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(home_migrate, "busy_reasons", lambda *a, **k: [])
    legacy = tmp_path / ".tokenpak"
    canonical = tmp_path / ".tpk"
    legacy.mkdir()
    return legacy, canonical


def tree_hash(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            out[str(p.relative_to(root)) + "/"] = ""
    return out


def run(apply=False, as_json=False):
    return cmd_home_migrate(argparse.Namespace(apply=apply, as_json=as_json))


def make_db(path: Path, ddl: str, rows: list[tuple[str, tuple]] = ()):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    for sql, params in rows:
        conn.execute(sql, params)
    conn.commit()
    conn.close()


def count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


E = "INSERT INTO entries (session_id, entry_type, content, content_hash) VALUES (?,?,?,?)"


def test_dry_run_changes_nothing(homes, capsys):
    legacy, canonical = homes
    (legacy / "config.yaml").write_text("a: 1\n")
    make_db(legacy / "companion" / "journal.db", JOURNAL, [(E, ("s", "t", "x", "h1"))])
    canonical.mkdir()
    (canonical / "config.yaml").write_text("a: 2\n")
    before = (tree_hash(legacy), tree_hash(canonical))
    assert run() == 0
    assert (tree_hash(legacy), tree_hash(canonical)) == before
    out = capsys.readouterr().out
    assert "dry run" in out and "--apply" in out
    assert "CONFLICT" in out and "COPY" in out


def test_copy_into_empty_canonical_private_dirs(homes):
    legacy, canonical = homes
    (legacy / "config.yaml").write_text("a: 1\n")
    make_db(legacy / "companion" / "journal.db", JOURNAL, [(E, ("s", "t", "x", "h1"))])
    assert run(apply=True) == 0
    assert (canonical / "config.yaml").read_text() == "a: 1\n"
    assert count(canonical / "companion" / "journal.db", "entries") == 1
    assert stat.S_IMODE((canonical / "companion").stat().st_mode) == 0o700


def test_journal_merge_dedupes_by_content_hash_not_id(homes):
    legacy, canonical = homes
    make_db(
        legacy / "companion" / "journal.db",
        JOURNAL,
        [(E, ("s", "t", "same", "h1")), (E, ("s", "t", "only-legacy", "h2"))],
    )
    make_db(
        canonical / "companion" / "journal.db",
        JOURNAL,
        [(E, ("s", "t", "same", "h1")), (E, ("s", "t", "only-canon", "h3"))],
    )
    assert run(apply=True) == 0
    db = canonical / "companion" / "journal.db"
    assert count(db, "entries") == 3  # h1 once, h2 added, h3 kept; ids collided
    conn = sqlite3.connect(db)
    hashes = sorted(r[0] for r in conn.execute("SELECT content_hash FROM entries"))
    conn.close()
    assert hashes == ["h1", "h2", "h3"]
    # backup of the modified target exists
    backups = list((canonical / "backups").glob("home-migrate-*/companion/journal.db"))
    assert len(backups) == 1 and count(backups[0], "entries") == 2


def test_ledger_merge_by_primary_key(homes):
    legacy, canonical = homes
    ddl = "CREATE TABLE ledger (k TEXT PRIMARY KEY, v INTEGER);"
    ins = "INSERT INTO ledger VALUES (?,?)"
    make_db(legacy / "execution_ledger.db", ddl, [(ins, ("a", 1)), (ins, ("b", 2))])
    make_db(canonical / "execution_ledger.db", ddl, [(ins, ("a", 99)), (ins, ("c", 3))])
    assert run(apply=True) == 0
    conn = sqlite3.connect(canonical / "execution_ledger.db")
    rows = dict(conn.execute("SELECT k, v FROM ledger").fetchall())
    conn.close()
    assert rows == {"a": 99, "b": 2, "c": 3}  # canonical wins on a key clash


def test_schema_mismatch_is_conflict_and_not_copied(homes, capsys):
    legacy, canonical = homes
    make_db(
        legacy / "monitor.db",
        "CREATE TABLE t (a TEXT PRIMARY KEY);",
        [("INSERT INTO t VALUES ('x')", ())],
    )
    make_db(canonical / "monitor.db", "CREATE TABLE t (a TEXT PRIMARY KEY, b TEXT);")
    assert run(apply=True) == 0
    assert count(canonical / "monitor.db", "t") == 0
    assert "CONFLICT" in capsys.readouterr().out


def test_json_conflict_keeps_canonical_and_writes_legacy_copy(homes):
    legacy, canonical = homes
    (legacy / "fleet.yaml").write_text("who: legacy\n")
    (legacy / "pinned_blocks.json").write_text('{"a": 1}')
    canonical.mkdir()
    (canonical / "fleet.yaml").write_text("who: canonical\n")
    (canonical / "pinned_blocks.json").write_text('{\n "a": 1\n}')  # same content, other format
    assert run(apply=True) == 0
    assert (canonical / "fleet.yaml").read_text() == "who: canonical\n"
    assert (canonical / "fleet.yaml.legacy").read_text() == "who: legacy\n"
    assert not (canonical / "pinned_blocks.json.legacy").exists()


def test_license_rules_and_mode(homes):
    legacy, canonical = homes
    (legacy / "license.json").write_text('{"k": "legacy"}')
    os.chmod(legacy / "license.json", 0o644)
    assert run(apply=True) == 0
    assert stat.S_IMODE((canonical / "license.json").stat().st_mode) == 0o600
    # canonical wins when both exist and differ
    (legacy / "license.json").write_text('{"k": "newer-legacy"}')
    assert run(apply=True) == 0
    assert json.loads((canonical / "license.json").read_text()) == {"k": "legacy"}
    assert not (canonical / "license.json.legacy").exists()


def test_non_state_entries_untouched_and_listed(homes, capsys):
    legacy, canonical = homes
    (legacy / "config.yaml").write_text("a: 1\n")
    (legacy / "codex-sessions").mkdir()
    (legacy / "codex-sessions" / "big.bin").write_bytes(b"x" * 10)
    (legacy / "license-server.env").write_text("SECRET=1")
    assert run(apply=True) == 0
    out = capsys.readouterr().out
    assert "codex-sessions" in out and "license-server.env" in out
    assert "not TokenPak state" in out
    assert not (canonical / "codex-sessions").exists()
    assert not (canonical / "license-server.env").exists()


def test_refuses_when_busy_and_changes_nothing(homes, monkeypatch, capsys):
    legacy, canonical = homes
    (legacy / "config.yaml").write_text("a: 1\n")
    monkeypatch.setattr(
        home_migrate, "busy_reasons", lambda *a, **k: ["a companion session is active"]
    )
    assert run(apply=True) == EXIT_BUSY
    assert not canonical.exists()
    assert "Nothing was changed" in capsys.readouterr().err


def test_locked_target_db_is_busy(homes, monkeypatch):
    legacy, canonical = homes
    ddl = "CREATE TABLE t (a TEXT PRIMARY KEY);"
    make_db(legacy / "monitor.db", ddl, [("INSERT INTO t VALUES ('x')", ())])
    make_db(canonical / "monitor.db", ddl)
    monkeypatch.undo() if False else None
    # Use the real gate for locks only: no proxy, no open-file scan.
    from tokenpak.cli.commands import update_apply

    monkeypatch.setattr(update_apply, "idle_gate", lambda **k: (None, {}))
    monkeypatch.setattr(home_migrate, "_open_by_others", lambda roots: [])
    monkeypatch.setattr(home_migrate, "busy_reasons", _real_busy_reasons)
    holder = sqlite3.connect(canonical / "monitor.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        assert run(apply=True) == EXIT_BUSY
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert count(canonical / "monitor.db", "t") == 0


_real_busy_reasons = home_migrate.busy_reasons


def test_idempotent_rerun_and_legacy_byte_identical(homes, capsys):
    legacy, canonical = homes
    (legacy / "config.yaml").write_text("a: 1\n")
    (legacy / "requests.jsonl").write_text('{"i":1}\n{"i":2}\n')
    make_db(
        legacy / "companion" / "journal.db",
        JOURNAL,
        [(E, ("s", "t", "x", "h1")), (E, ("s", "t", "n", None))],
    )
    canonical.mkdir()
    (canonical / "requests.jsonl").write_text('{"i":2}\n{"i":3}\n')
    make_db(canonical / "companion" / "journal.db", JOURNAL, [(E, ("s", "t", "y", "h9"))])
    before = tree_hash(legacy)
    assert run(apply=True) == 0
    assert tree_hash(legacy) == before
    assert (canonical / "requests.jsonl").read_text().splitlines() == [
        '{"i":2}',
        '{"i":3}',
        '{"i":1}',
    ]
    snapshot = tree_hash(canonical / "companion"), (canonical / "requests.jsonl").read_text()
    capsys.readouterr()
    assert run(apply=True, as_json=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert all(i["action"] in ("SKIP-identical",) for i in report["items"]), report["items"]
    assert all(i["rows"] == 0 for i in report["items"])
    assert (
        tree_hash(canonical / "companion"),
        (canonical / "requests.jsonl").read_text(),
    ) == snapshot
    assert tree_hash(legacy) == before


def test_wal_db_with_uncheckpointed_rows_is_merged(homes):
    legacy, canonical = homes
    path = legacy / "companion" / "journal.db"
    path.parent.mkdir()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.executescript(JOURNAL)
    conn.execute(E, ("s", "t", "in-wal", "hw"))
    conn.commit()
    try:
        wal = Path(str(path) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        before = tree_hash(legacy)
        assert run(apply=True) == 0
        assert tree_hash(legacy) == before
    finally:
        conn.close()
    assert count(canonical / "companion" / "journal.db", "entries") == 1


def test_refuses_with_tokenpak_home_set(homes, monkeypatch, capsys):
    monkeypatch.setenv("TOKENPAK_HOME", "/nonexistent-elsewhere")
    assert run(apply=True) == EXIT_FAILURE
    assert "TOKENPAK_HOME" in capsys.readouterr().err


def test_no_legacy_home_is_nothing_to_migrate(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TOKENPAK_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert run(apply=True) == 0
    assert "Nothing to migrate" in capsys.readouterr().out


def test_runtime_and_logs_are_skipped(homes):
    legacy, canonical = homes
    (legacy / "config.yaml").write_text("a: 1\n")
    (legacy / "watchdog.log").write_text("noise")
    (legacy / "proxy.pid").write_text("123")
    (legacy / "pro").mkdir()
    (legacy / "pro" / "daemon.sock-info").write_text("{}")
    (legacy / "pro" / "plan.json").write_text("{}")
    assert run(apply=True) == 0
    assert not (canonical / "watchdog.log").exists()
    assert not (canonical / "proxy.pid").exists()
    assert not (canonical / "pro" / "daemon.sock-info").exists()
    assert (canonical / "pro" / "plan.json").exists()


def test_product_state_registry_covers_ledgers():
    from tokenpak import _paths

    names = _paths.product_state_names()
    for n in (
        "execution_ledger.db",
        "routing_ledger.db",
        "monitor.db",
        "cost.db",
        "companion",
        "pro",
    ):
        assert n in names
    assert "codex-sessions" not in names


def test_doctor_reports_split_home(homes):
    from tokenpak import _paths

    legacy, canonical = homes
    (legacy / "config.yaml").write_text("a: 1\n")
    assert not _paths.is_split_home()
    canonical.mkdir()
    (canonical / "config.yaml").write_text("a: 2\n")
    assert _paths.is_split_home()


def _items(capsys):
    run(apply=False, as_json=True)
    return {i["path"]: i for i in json.loads(capsys.readouterr().out)["items"]}


def test_symlink_is_recreated_not_followed(homes, tmp_path, capsys):
    legacy, canonical = homes
    live = tmp_path / "elsewhere" / "live-monitor.db"
    make_db(
        live,
        "CREATE TABLE requests (a TEXT PRIMARY KEY);",
        [("INSERT INTO requests VALUES ('x')", ())],
    )
    (legacy / "monitor.db").symlink_to(live)
    before = (tree_hash(live.parent), os.readlink(legacy / "monitor.db"))
    items = _items(capsys)
    assert items["monitor.db"]["action"] == "LINK"
    assert not canonical.exists()  # dry run
    assert run(apply=True) == 0
    link = canonical / "monitor.db"
    assert link.is_symlink() and os.readlink(link) == str(live)
    assert (
        tree_hash(live.parent),
        os.readlink(legacy / "monitor.db"),
    ) == before  # nothing read through
    capsys.readouterr()
    items = _items(capsys)
    assert items["monitor.db"]["action"] == "SKIP-identical"


def test_symlink_conflict_with_different_entry(homes, tmp_path, capsys):
    legacy, canonical = homes
    (legacy / "monitor.db").symlink_to(tmp_path / "a.db")
    canonical.mkdir()
    (canonical / "monitor.db").symlink_to(tmp_path / "b.db")
    (legacy / "telemetry.db").symlink_to(tmp_path / "a.db")
    (canonical / "telemetry.db").write_bytes(b"plain file")
    items = _items(capsys)
    assert items["monitor.db"]["action"] == "CONFLICT"
    assert items["telemetry.db"]["action"] == "CONFLICT"
    assert run(apply=True) == 0
    assert os.readlink(canonical / "monitor.db") == str(tmp_path / "b.db")
    assert (canonical / "telemetry.db").read_bytes() == b"plain file"


def test_relative_symlink_inside_home_stays_relative(homes, capsys):
    legacy, canonical = homes
    cap = legacy / "companion" / "capsules"
    cap.mkdir(parents=True)
    (cap / "abc.md").write_text("capsule")
    (cap / "active.md").symlink_to("abc.md")
    assert run(apply=True) == 0
    link = canonical / "companion" / "capsules" / "active.md"
    assert os.readlink(link) == "abc.md"
    assert link.resolve() == (canonical / "companion" / "capsules" / "abc.md").resolve()
    assert "stays inside the home" in capsys.readouterr().out
    items = _items(capsys)
    assert items["companion/capsules/active.md"]["action"] == "SKIP-identical"


def test_relative_symlink_escaping_home_becomes_absolute(homes, tmp_path):
    legacy, canonical = homes
    (tmp_path / "outside.db").write_bytes(b"x")
    (legacy / "monitor.db").symlink_to("../outside.db")
    assert run(apply=True) == 0
    assert os.readlink(canonical / "monitor.db") == str(tmp_path / "outside.db")


FTS = """
CREATE TABLE docs (id INTEGER PRIMARY KEY, body TEXT);
CREATE VIRTUAL TABLE docs_fts USING fts5(body);
"""


def _fts_db(path: Path, bodies: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(FTS)
    for b in bodies:
        conn.execute("INSERT INTO docs (body) VALUES (?)", (b,))
        conn.execute("INSERT INTO docs_fts (body) VALUES (?)", (b,))
    conn.commit()
    conn.close()


def test_virtual_table_db_copies_then_rerun_is_identical(homes, capsys):
    legacy, canonical = homes
    _fts_db(legacy / "companion" / "recall.db", ["alpha", "beta"])
    assert run(apply=True) == 0
    capsys.readouterr()
    items = _items(capsys)
    assert items["companion/recall.db"]["action"] == "SKIP-identical"
    assert run(apply=True) == 0
    assert not (canonical / "companion" / "recall.db.legacy").exists()


def test_virtual_table_db_diverged_keeps_canonical_and_writes_legacy(homes, capsys):
    legacy, canonical = homes
    _fts_db(legacy / "companion" / "recall.db", ["alpha", "beta"])
    _fts_db(canonical / "companion" / "recall.db", ["alpha", "gamma"])
    before = tree_hash(legacy)
    items = _items(capsys)
    assert items["companion/recall.db"]["action"] == "CONFLICT"
    assert not (canonical / "companion" / "recall.db.legacy").exists()  # dry run
    assert run(apply=True) == 0
    assert tree_hash(legacy) == before
    assert count(canonical / "companion" / "recall.db", "docs") == 2
    side = canonical / "companion" / "recall.db.legacy"
    conn = sqlite3.connect(side)
    assert sorted(r[0] for r in conn.execute("SELECT body FROM docs")) == ["alpha", "beta"]
    conn.close()
    capsys.readouterr()
    assert run(apply=True) == 0  # rerun does not rewrite the same legacy copy


def _boundary(capsys):
    from tokenpak.cli.commands.doctor import run_doctor

    run_doctor(output_json=True)
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{") :])
    checks = data["checks"]
    rows = checks.values() if isinstance(checks, dict) else checks
    return next(
        c
        for c in rows
        if c.get("name") == "home_boundary" or "boundary" in str(c.get("message", ""))
    )


def _split_fixture(legacy, canonical):
    (legacy / "config.yaml").write_text("a: 1\n")
    canonical.mkdir()
    (canonical / "config.yaml").write_text("a: 2\n")


def test_receipt_written_only_on_successful_apply(homes, monkeypatch):
    from tokenpak import _paths

    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    assert run() == 0  # dry run
    assert not _paths.migration_receipt_path().exists()
    monkeypatch.setattr(home_migrate, "busy_reasons", lambda *a, **k: ["busy"])
    assert run(apply=True) == EXIT_BUSY
    assert not _paths.migration_receipt_path().exists()
    monkeypatch.setattr(home_migrate, "busy_reasons", lambda *a, **k: [])
    assert run(apply=True) == 0
    receipt = _paths.read_migration_receipt()
    assert receipt and receipt["source"] == str(legacy) and receipt["summary"]
    assert stat.S_IMODE(_paths.migration_receipt_path().stat().st_mode) == 0o600
    assert _paths.is_migrated_home() and _paths.is_split_home()


def test_doctor_split_then_migrated_info_then_newer_legacy_warning(homes, capsys):
    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    row = _boundary(capsys)
    assert row["status"] == "warn" and "split home" in row["message"]
    assert run(apply=True) == 0
    capsys.readouterr()
    row = _boundary(capsys)
    assert row["status"] == "pass" and "migrated on" in row["message"]
    future = os.path.getmtime(canonical / "home-migrated.json") + 100
    os.utime(legacy / "config.yaml", (future, future))
    row = _boundary(capsys)
    assert row["status"] == "warn" and "after migration" in row["message"]
    assert "tokenpak home migrate" in row.get("detail", "")
