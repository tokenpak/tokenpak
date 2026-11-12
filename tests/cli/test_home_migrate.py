# SPDX-License-Identifier: Apache-2.0
"""``tokenpak home migrate``: merge a split home, never touch the legacy tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import time
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
    assert run(apply=True) == EXIT_FAILURE
    assert count(canonical / "monitor.db", "t") == 0
    assert "CONFLICT" in capsys.readouterr().out


def test_json_conflict_keeps_canonical_and_writes_legacy_copy(homes):
    legacy, canonical = homes
    (legacy / "fleet.yaml").write_text("who: legacy\n")
    (legacy / "pinned_blocks.json").write_text('{"a": 1}')
    canonical.mkdir()
    (canonical / "fleet.yaml").write_text("who: canonical\n")
    (canonical / "pinned_blocks.json").write_text('{\n "a": 1\n}')  # same content, other format
    assert run(apply=True) == EXIT_FAILURE  # fleet.yaml is left for the user to review
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
    assert run(apply=True) == EXIT_FAILURE
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
    assert run(apply=True) == EXIT_FAILURE
    assert tree_hash(legacy) == before
    assert count(canonical / "companion" / "recall.db", "docs") == 2
    side = canonical / "companion" / "recall.db.legacy"
    conn = sqlite3.connect(side)
    assert sorted(r[0] for r in conn.execute("SELECT body FROM docs")) == ["alpha", "beta"]
    conn.close()
    capsys.readouterr()
    assert run(apply=True) == EXIT_FAILURE  # still unresolved; legacy copy not rewritten


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
    """Both homes hold state and nothing conflicts, so an apply finishes cleanly."""
    (legacy / "config.yaml").write_text("a: 1\n")
    canonical.mkdir()
    (canonical / "config.yaml").write_text("a: 1\n")


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
    assert receipt["status"] == "complete" and receipt["unresolved"] == []
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


def _config_fixture(legacy, canonical, legacy_text, canonical_text):
    (legacy / "config.yaml").write_text(legacy_text)
    canonical.mkdir()
    (canonical / "config.yaml").write_text(canonical_text)


def test_config_merge_adds_legacy_only_sections_and_keeps_comments(homes, capsys):
    import yaml

    legacy, canonical = homes
    _config_fixture(
        legacy,
        canonical,
        "port: 1\nspend_guard:\n  enabled: true\n  warn_tokens: 5\nproxy:\n  mode: hybrid\n",
        "# mine\nport: 2\n",
    )
    item = _items(capsys)["config.yaml"]
    assert item["action"] == "MERGE" and "2 settings added: spend_guard, proxy" in item["detail"]
    assert "canonical kept for port" in item["detail"]
    assert run(apply=True) == EXIT_FAILURE  # the differing port is still for the user to settle
    text = (canonical / "config.yaml").read_text()
    assert text.startswith("# mine\nport: 2\n")
    data = yaml.safe_load(text)
    assert data["port"] == 2 and data["spend_guard"]["warn_tokens"] == 5
    assert data["proxy"] == {"mode": "hybrid"}
    assert (canonical / "config.yaml.legacy").read_text() == (legacy / "config.yaml").read_text()
    backups = list((canonical / "backups").rglob("config.yaml"))
    assert [b.read_text() for b in backups] == ["# mine\nport: 2\n"]
    capsys.readouterr()
    assert _items(capsys)["config.yaml"]["action"] == "CONFLICT"  # only the differing value left


def test_config_merge_nested_keeps_canonical_values(homes, capsys):
    import yaml

    legacy, canonical = homes
    _config_fixture(
        legacy,
        canonical,
        "pro:\n  multipak:\n    enabled: false\n  daemon:\n    port: 9\n",
        "pro:\n  multipak:\n    enabled: true\n",
    )
    assert run(apply=True) == EXIT_FAILURE
    data = yaml.safe_load((canonical / "config.yaml").read_text())
    assert data == {"pro": {"multipak": {"enabled": True}, "daemon": {"port": 9}}}
    capsys.readouterr()
    assert _items(capsys)["config.yaml"]["action"] == "CONFLICT"


def test_config_merge_rerun_is_identical_when_values_agree(homes, capsys):
    legacy, canonical = homes
    _config_fixture(legacy, canonical, "a: 1\nb: 2\n", "a: 1\n")
    assert run(apply=True) == 0
    capsys.readouterr()
    assert _items(capsys)["config.yaml"]["action"] == "SKIP-identical"
    assert not (canonical / "config.yaml.legacy").exists()


def test_config_unparseable_falls_back_to_legacy_copy(homes):
    legacy, canonical = homes
    _config_fixture(legacy, canonical, "a: [1\n", "a: 2\n")
    assert run(apply=True) == EXIT_FAILURE
    assert (canonical / "config.yaml").read_text() == "a: 2\n"
    assert (canonical / "config.yaml.legacy").read_text() == "a: [1\n"


def _doctor_row(capsys, name):
    from tokenpak.cli.commands.doctor import run_doctor

    run_doctor(output_json=True)
    out = capsys.readouterr().out
    checks = json.loads(out[out.index("{") :])["checks"]
    rows = checks.values() if isinstance(checks, dict) else checks
    return next((c for c in rows if c.get("check") == name), None)


def test_doctor_names_ignored_settings_until_migrated(homes, capsys):
    legacy, canonical = homes
    _config_fixture(legacy, canonical, "a: 1\nspend_guard:\n  enabled: true\n", "a: 1\n")
    row = _doctor_row(capsys, "home_config")
    assert row and row["status"] == "warn" and "spend_guard" in row["detail"]
    assert run(apply=True) == 0
    capsys.readouterr()
    assert _doctor_row(capsys, "home_config") is None


def test_doctor_quiet_after_migration_when_setting_removed_on_purpose(homes, capsys):
    legacy, canonical = homes
    _config_fixture(legacy, canonical, "a: 1\nb: 2\n", "a: 1\n")
    assert run(apply=True) == 0
    (canonical / "config.yaml").write_text("a: 1\n")
    capsys.readouterr()
    assert _doctor_row(capsys, "home_config") is None


# --------------------------------------------------------------------------
# Outcome: an apply with unresolved items is not a finished migration
# --------------------------------------------------------------------------

LEDGER = "CREATE TABLE ledger (k TEXT PRIMARY KEY, v INTEGER);"
LEDGER_WIDE = "CREATE TABLE ledger (k TEXT PRIMARY KEY, v INTEGER, note TEXT);"
LEDGER_ROW = "INSERT INTO ledger (k, v) VALUES (?, ?)"


def ledger_keys(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT k FROM ledger"))
    finally:
        conn.close()


def _schema_conflict(legacy, canonical):
    """Same database name in both homes with different schemas: nothing can be merged."""
    (legacy / "config.yaml").write_text("a: 1\n")
    canonical.mkdir()
    (canonical / "config.yaml").write_text("a: 1\n")
    make_db(
        legacy / "execution_ledger.db", LEDGER, [(LEDGER_ROW, ("a", 1)), (LEDGER_ROW, ("b", 2))]
    )
    make_db(canonical / "execution_ledger.db", LEDGER_WIDE, [(LEDGER_ROW, ("c", 3))])


def _file_conflict(legacy, canonical):
    (legacy / "fleet.yaml").write_text("who: legacy\n")
    (canonical / "fleet.yaml").write_text("who: canonical\n")


def _boundary_row(capsys):
    capsys.readouterr()
    return _doctor_row(capsys, "home_boundary")


def test_schema_conflict_apply_is_not_done_and_keeps_the_only_copy(homes, capsys):
    legacy, canonical = homes
    _schema_conflict(legacy, canonical)
    before = tree_hash(legacy)
    assert run(apply=True) == EXIT_FAILURE
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert tree_hash(legacy) == before
    assert ledger_keys(legacy / "execution_ledger.db") == ["a", "b"]  # the only copy of a and b
    assert ledger_keys(canonical / "execution_ledger.db") == ["c"]  # nothing merged in
    assert not (canonical / "execution_ledger.db.legacy").exists()
    assert "✅" not in text and "Done." not in text
    assert "you can remove the legacy home" not in text
    assert "not finished: 1 item still needs review" in captured.err
    assert "Keep it" in captured.err and "tokenpak home migrate --apply" in captured.err
    assert "Nothing was copied for these" in captured.err
    assert "schema differs in table ledger" in captured.err
    assert "saved beside it" not in text and "legacy copy beside it" not in text


def test_conflict_guidance_separates_nothing_copied_from_saved_beside(homes, capsys):
    legacy, canonical = homes
    _schema_conflict(legacy, canonical)
    _file_conflict(legacy, canonical)
    assert run(apply=True) == EXIT_FAILURE
    err = capsys.readouterr().err
    assert "2 items still need review" in err
    assert "Nothing was copied for these" in err
    assert "the legacy one is saved beside it" in err
    assert "fleet.yaml  as fleet.yaml.legacy" in err
    assert "execution_ledger.db  as" not in err
    assert (canonical / "fleet.yaml.legacy").read_text() == "who: legacy\n"
    assert not (canonical / "execution_ledger.db.legacy").exists()


def test_json_apply_reports_the_incomplete_outcome(homes, capsys):
    legacy, canonical = homes
    _schema_conflict(legacy, canonical)
    _file_conflict(legacy, canonical)
    assert run(apply=True, as_json=True) == EXIT_FAILURE
    report = json.loads(capsys.readouterr().out)
    assert {"applied", "legacy", "canonical", "items", "summary", "busy"} <= set(report)
    assert report["applied"] is True and report["outcome"] == "incomplete"
    assert report["receipt"] == "written" and report["summary"]["CONFLICT"] == 2
    assert set(report["items"][0]) == {"path", "action", "detail", "rows"}
    by_path = {u["path"]: u for u in report["unresolved"]}
    assert set(by_path) == {"execution_ledger.db", "fleet.yaml"}
    assert by_path["execution_ledger.db"]["legacy_copy"] is None
    assert "nothing copied" in by_path["execution_ledger.db"]["detail"]
    assert by_path["fleet.yaml"]["legacy_copy"] == "fleet.yaml.legacy"


def test_clean_apply_is_complete_and_repeatable(homes, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    (legacy / "requests.jsonl").write_text('{"i":1}\n')
    assert run(apply=True) == 0
    captured = capsys.readouterr()
    assert "✅ Done." in captured.out and captured.err == ""
    for _ in range(2):
        assert run(apply=True, as_json=True) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["applied"] is True and report["outcome"] == "complete"
        assert report["unresolved"] == [] and report["receipt"] == "written"
    receipt = _paths.read_migration_receipt()
    assert receipt["status"] == "complete" and receipt["unresolved"] == []
    assert _paths.is_migrated_home() and not _paths.is_migration_incomplete()


def test_dry_run_explains_conflicts_without_writing(homes, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _schema_conflict(legacy, canonical)
    _file_conflict(legacy, canonical)
    before = (tree_hash(legacy), tree_hash(canonical))
    assert run() == 0
    out = capsys.readouterr().out
    assert (tree_hash(legacy), tree_hash(canonical)) == before
    assert not _paths.migration_receipt_path().exists()
    assert "Needs review (2)" in out
    assert "Nothing would be copied for these" in out
    assert "--apply saves the legacy one beside it" in out
    assert "fleet.yaml  as fleet.yaml.legacy" in out
    assert "execution_ledger.db  as" not in out
    assert "records the migration as incomplete" in out
    assert "✅" not in out and "Done." not in out
    assert run(as_json=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False and report["outcome"] == "planned"
    assert report["receipt"] is None and len(report["unresolved"]) == 2


def test_config_merge_that_kept_differing_values_is_unresolved(homes, capsys):
    legacy, canonical = homes
    _config_fixture(legacy, canonical, "port: 1\nspend_guard:\n  enabled: true\n", "port: 2\n")
    assert run(apply=True, as_json=True) == EXIT_FAILURE
    report = json.loads(capsys.readouterr().out)
    assert report["summary"] == {"MERGE": 1}  # no CONFLICT in the counts, still unresolved
    assert report["outcome"] == "incomplete"
    assert [u["path"] for u in report["unresolved"]] == ["config.yaml"]
    assert report["unresolved"][0]["legacy_copy"] == "config.yaml.legacy"
    row = _boundary_row(capsys)
    assert row["status"] == "warn" and "migration incomplete: 1 unresolved" in row["message"]


def test_incomplete_apply_records_receipt_and_doctor_warns(homes, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _schema_conflict(legacy, canonical)
    assert run(apply=True) == EXIT_FAILURE
    receipt = _paths.read_migration_receipt()
    assert receipt["status"] == "incomplete" and receipt["summary"]["CONFLICT"] == 1
    assert [u["path"] for u in receipt["unresolved"]] == ["execution_ledger.db"]
    assert receipt["unresolved"][0]["legacy_copy"] is None
    assert stat.S_IMODE(_paths.migration_receipt_path().stat().st_mode) == 0o600
    assert _paths.is_split_home() and _paths.is_migration_incomplete()
    assert not _paths.is_migrated_home()
    row = _boundary_row(capsys)
    assert row["status"] == "warn" and "migration incomplete" in row["message"]
    assert "execution_ledger.db" in row["detail"] and "Keep it" in row["detail"]
    assert "migrated on" not in row["message"]


def _old_receipt(legacy, canonical, summary):
    """A receipt as earlier releases wrote it: no status and no unresolved list."""
    body = {
        "migrated_at_utc": "2026-10-01T00:00:00Z",
        "epoch": time.time() + 3600,
        "source": str(legacy),
        "target": str(canonical),
        "summary": summary,
        "tokenpak_version": "1.31.0",
    }
    (canonical / "home-migrated.json").write_text(json.dumps(body))


def test_doctor_warns_for_old_receipt_whose_summary_has_conflicts(homes, capsys):
    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    _old_receipt(legacy, canonical, {"COPY": 1, "CONFLICT": 2})
    row = _boundary_row(capsys)
    assert row["status"] == "warn"
    assert "migration incomplete: 2 unresolved" in row["message"]


def test_doctor_still_passes_for_old_receipt_without_conflicts(homes, capsys):
    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    _old_receipt(legacy, canonical, {"COPY": 1, "SKIP-identical": 3})
    row = _boundary_row(capsys)
    assert row["status"] == "pass" and "migrated on 2026-10-01T00:00:00Z" in row["message"]


def test_stale_success_receipt_cannot_hide_a_later_incomplete_apply(homes, capsys):
    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    assert run(apply=True) == 0
    assert _boundary_row(capsys)["status"] == "pass"
    # Later a legacy database appears whose schema cannot be merged.
    make_db(legacy / "execution_ledger.db", LEDGER, [(LEDGER_ROW, ("a", 1))])
    make_db(canonical / "execution_ledger.db", LEDGER_WIDE, [(LEDGER_ROW, ("c", 3))])
    assert run(apply=True) == EXIT_FAILURE
    row = _boundary_row(capsys)
    assert row["status"] == "warn" and "migration incomplete" in row["message"]


def test_apply_that_stops_midway_leaves_no_success_receipt(homes, monkeypatch, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    assert run(apply=True) == 0
    assert _paths.migration_receipt_path().exists()

    def boom(*args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(home_migrate, "build", boom)
    assert run(apply=True) == EXIT_FAILURE
    assert not _paths.migration_receipt_path().exists()
    row = _boundary_row(capsys)
    assert row["status"] == "warn" and "split home" in row["message"]


def test_busy_refusal_leaves_an_earlier_receipt_alone(homes, monkeypatch):
    from tokenpak import _paths

    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    assert run(apply=True) == 0
    before = _paths.migration_receipt_path().read_bytes()
    monkeypatch.setattr(home_migrate, "busy_reasons", lambda *a, **k: ["busy"])
    assert run(apply=True) == EXIT_BUSY
    assert _paths.migration_receipt_path().read_bytes() == before


def test_apply_refuses_when_the_earlier_receipt_cannot_be_cleared(homes, capsys):
    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    (legacy / "fleet.yaml").write_text("who: legacy\n")
    (canonical / "home-migrated.json").mkdir()  # cannot be unlinked as a file
    assert run(apply=True) == EXIT_FAILURE
    err = capsys.readouterr().err
    assert "could not clear the earlier migration receipt" in err
    assert "Nothing was changed" in err
    assert not (canonical / "fleet.yaml").exists()


def test_receipt_write_failure_is_reported_not_claimed_as_done(homes, monkeypatch, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _split_fixture(legacy, canonical)
    assert run(apply=True) == 0  # an earlier success receipt exists
    (legacy / "requests.jsonl").write_text('{"i":1}\n')

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(home_migrate, "_write_receipt", full)
    capsys.readouterr()
    assert run(apply=True) == EXIT_FAILURE
    captured = capsys.readouterr()
    assert "receipt could not be written (disk full)" in captured.err
    assert "✅" not in captured.out + captured.err and "Done." not in captured.out
    assert (canonical / "requests.jsonl").read_text() == '{"i":1}\n'  # the merge itself ran
    assert _paths.read_migration_receipt() is None  # and the earlier receipt does not stand in
    assert "split home" in _boundary_row(capsys)["message"]

    (legacy / "requests.jsonl").write_text('{"i":1}\n{"i":2}\n')
    assert run(apply=True, as_json=True) == EXIT_FAILURE
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["outcome"] == "unrecorded" and report["receipt"] == "failed"
    assert report["unresolved"] == [] and "disk full" in captured.err


def test_conflict_with_failed_receipt_write_still_says_not_finished(homes, monkeypatch, capsys):
    legacy, canonical = homes
    _schema_conflict(legacy, canonical)

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(home_migrate, "_write_receipt", full)
    assert run(apply=True, as_json=True) == EXIT_FAILURE
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["outcome"] == "incomplete" and report["receipt"] == "failed"
    assert run(apply=True) == EXIT_FAILURE
    err = capsys.readouterr().err
    assert "not finished" in err and "disk full" in err


def test_retry_after_resolving_the_conflict_finishes_and_stays_finished(homes, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _schema_conflict(legacy, canonical)
    assert run(apply=True) == EXIT_FAILURE
    assert _boundary_row(capsys)["status"] == "warn"
    # The user rebuilds the canonical table with the legacy schema, keeping its rows.
    conn = sqlite3.connect(canonical / "execution_ledger.db")
    conn.executescript(
        "CREATE TABLE ledger_new (k TEXT PRIMARY KEY, v INTEGER);"
        "INSERT INTO ledger_new (k, v) SELECT k, v FROM ledger;"
        "DROP TABLE ledger;"
        "ALTER TABLE ledger_new RENAME TO ledger;"
    )
    conn.close()
    assert run(apply=True) == 0
    assert ledger_keys(canonical / "execution_ledger.db") == ["a", "b", "c"]
    assert ledger_keys(legacy / "execution_ledger.db") == ["a", "b"]
    assert _paths.read_migration_receipt()["status"] == "complete"
    row = _boundary_row(capsys)
    assert row["status"] == "pass" and "migrated on" in row["message"]
    assert run(apply=True, as_json=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "complete"
    assert {i["action"] for i in report["items"]} == {"SKIP-identical"}


def test_receipt_completeness_rules():
    from tokenpak import _paths

    complete = _paths.migration_receipt_complete
    assert not complete(None)
    assert complete({"summary": {"COPY": 2}})  # written before status existed
    assert not complete({"summary": {"CONFLICT": 1}})
    assert not complete({"unresolved": [{"path": "x"}]})
    assert complete({"status": "complete", "unresolved": [], "summary": {}})
    assert not complete({"status": "incomplete", "summary": {}})
    assert not complete({"status": "something-else", "summary": {}})
    assert not complete({"status": "complete", "summary": {"CONFLICT": 1}})
    assert _paths.migration_unresolved({"unresolved": [{}, {}], "summary": {"CONFLICT": 1}}) == 2
    assert _paths.migration_unresolved(None) == 0
    assert _paths._RECEIPT_CONFLICT == home_migrate.CONFLICT


# --------------------------------------------------------------------------
# Migration-only state: cost.db is carried by migrate but is not a resolver
# state file, so these homes are not a "split home" and have no config.yaml.
# --------------------------------------------------------------------------


def _cost_conflict(legacy, canonical):
    make_db(
        legacy / "cost.db",
        "CREATE TABLE t (a TEXT PRIMARY KEY);",
        [("INSERT INTO t VALUES ('legacy-only')", ())],
    )
    make_db(canonical / "cost.db", "CREATE TABLE t (a TEXT PRIMARY KEY, b TEXT);")


def _assert_keeps_legacy(row):
    detail = row["detail"].lower()
    assert row["status"] == "warn"
    assert "keep" in detail and "tokenpak home migrate" in detail
    assert "remove" not in detail  # no deletion advice for an unfinished migration


def test_cost_db_only_conflict_is_incomplete_and_doctor_keeps_legacy(homes, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _cost_conflict(legacy, canonical)
    assert not _paths.is_split_home()  # nothing here decides which home reads resolve to
    assert run(apply=True, as_json=True) == EXIT_FAILURE
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "incomplete" and report["receipt"] == "written"
    assert count(canonical / "cost.db", "t") == 0  # nothing was merged
    assert count(legacy / "cost.db", "t") == 1  # the only copy of that row
    assert not (canonical / "cost.db.legacy").exists()
    assert _paths.read_migration_receipt()["status"] == "incomplete"
    assert _paths.is_migration_incomplete() and not _paths.is_split_home()
    row = _boundary_row(capsys)
    assert "migration incomplete" in row["message"] and "cost.db" in row["detail"]
    _assert_keeps_legacy(row)


def test_old_incomplete_receipt_is_honoured_without_resolver_state(homes, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _cost_conflict(legacy, canonical)
    _old_receipt(legacy, canonical, {"COPY": 1, "CONFLICT": 1})
    assert not _paths.is_split_home() and _paths.is_migration_incomplete()
    row = _boundary_row(capsys)
    assert "migration incomplete: 1 unresolved" in row["message"]
    _assert_keeps_legacy(row)


def test_old_incomplete_receipt_with_state_only_in_one_home(homes, capsys):
    legacy, canonical = homes
    make_db(legacy / "cost.db", "CREATE TABLE t (a TEXT PRIMARY KEY);")
    canonical.mkdir()
    (canonical / "config.yaml").write_text("a: 1\n")  # canonical alone holds resolver state
    _old_receipt(legacy, canonical, {"CONFLICT": 1})
    row = _boundary_row(capsys)
    assert "migration incomplete" in row["message"]
    _assert_keeps_legacy(row)


def test_cost_db_conflict_with_failed_receipt_write_still_keeps_legacy(homes, monkeypatch, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    _cost_conflict(legacy, canonical)
    _old_receipt(legacy, canonical, {"COPY": 1})  # an earlier success the new run must not leave

    def full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(home_migrate, "_write_receipt", full)
    assert run(apply=True, as_json=True) == EXIT_FAILURE
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "incomplete" and report["receipt"] == "failed"
    assert not _paths.migration_receipt_path().exists()  # no receipt of any kind remains
    assert count(legacy / "cost.db", "t") == 1 and count(canonical / "cost.db", "t") == 0
    assert not _paths.is_migration_incomplete()  # nothing recorded, so doctor cannot rely on it
    _assert_keeps_legacy(_boundary_row(capsys))


def test_both_homes_present_fallback_keeps_legacy(homes, capsys):
    legacy, canonical = homes
    _cost_conflict(legacy, canonical)  # never migrated, no receipt
    _assert_keeps_legacy(_boundary_row(capsys))


def test_incomplete_receipt_is_ignored_for_explicit_home_or_missing_legacy(homes, monkeypatch):
    import shutil

    from tokenpak import _paths

    legacy, canonical = homes
    _cost_conflict(legacy, canonical)
    assert run(apply=True) == EXIT_FAILURE
    assert _paths.is_migration_incomplete()
    monkeypatch.setenv("TOKENPAK_HOME", str(canonical.parent / "scoped"))
    assert not _paths.is_migration_incomplete()  # a scoped home is a closed world
    monkeypatch.delenv("TOKENPAK_HOME")
    assert _paths.is_migration_incomplete()
    shutil.rmtree(legacy)  # the user's own decision; nothing is left to protect
    assert not _paths.is_migration_incomplete()


def test_incomplete_receipt_outranks_legacy_active_guidance(homes, capsys):
    from tokenpak import _paths

    legacy, canonical = homes
    # An unreadable legacy database and no canonical state: reads resolve to the legacy home.
    (legacy / "monitor.db").write_bytes(b"SQLite format 3\x00" + b"not a valid SQLite database")
    before = tree_hash(legacy)
    assert run(apply=True, as_json=True) == EXIT_FAILURE
    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "incomplete" and report["receipt"] == "written"
    assert [u["path"] for u in report["unresolved"]] == ["monitor.db"]
    assert "unreadable" in report["unresolved"][0]["detail"]
    assert tree_hash(legacy) == before  # the only copy is untouched
    assert _paths.is_legacy_active() and _paths.is_migration_incomplete()
    row = _boundary_row(capsys)
    assert "migration incomplete" in row["message"] and "monitor.db" in row["detail"]
    assert "absent" not in row["detail"]  # not the "canonical home is absent" advisory
    _assert_keeps_legacy(row)
