"""Gate fidelity: incomplete baselines and destructive upgrades cannot pass."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release_gate/migration_multihop.py"
_SPEC = importlib.util.spec_from_file_location("migration_multihop_under_test", _SCRIPT)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("migration gate unavailable")
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

STORE_PATHS = {"~/.tpk/telemetry.db", "~/.tokenpak/spend_guard.db", "~/.tpk/monitor.db"}
OLD_SQL = "CREATE TABLE samples (id INTEGER PRIMARY KEY, label TEXT NOT NULL, data BLOB NOT NULL, ratio REAL, count INTEGER)"
DDL = [{"type": "table", "name": "samples", "sql": OLD_SQL}]


@pytest.fixture
def manifest_path(tmp_path):
    destination = tmp_path / "fixtures"
    shutil.copytree(gate.DEFAULT_MANIFEST.parent, destination)
    return destination / "manifest.json"


def write_manifest(path, manifest):
    path.write_text(json.dumps(manifest))


def replace_snapshot(path, transform):
    manifest = json.loads(path.read_text())
    row = manifest["baselines"][0]
    snapshot = json.loads((path.parent / row["file"]).read_bytes())
    transform(snapshot)
    raw = json.dumps(snapshot).encode()
    row.update(
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
        blob=hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),
    )
    row["file"] = row["sha256"] + ".json"
    (path.parent / row["file"]).write_bytes(raw)
    write_manifest(path, manifest)


def current_schema(tmp_path):
    with sqlite3.connect(tmp_path / "current.db") as conn:
        conn.execute(OLD_SQL)
        conn.execute("ALTER TABLE samples ADD COLUMN observed TEXT")
        return gate.schema(conn)


def migrate(path):
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
        if "observed" not in columns:
            conn.execute("ALTER TABLE samples ADD COLUMN observed TEXT")


def test_published_fixture_matrix_is_complete_and_each_table_is_seedable(tmp_path):
    manifest, baselines = gate.load_baselines(gate.DEFAULT_MANIFEST, STORE_PATHS)
    assert manifest["publication_anchor"] == "v1.28.0"
    assert [row["tag"] for row, _ in baselines] == [f"v1.{minor}.0" for minor in range(23, 29)]
    assert len({row["sha256"] for row, _ in baselines}) == 2
    cases = 0
    for row, stores in baselines:
        for index, store in enumerate(stores):
            with sqlite3.connect(tmp_path / f"{row['tag']}-{index}.db") as conn:
                gate.create_baseline(conn, store["ddl"]["objects"])
                seeded = gate.seed(conn)
                expected = {
                    obj["name"] for obj in store["ddl"]["objects"] if obj["type"] == "table"
                }
                assert set(seeded) == expected
                assert all(
                    len(value["rows"]) == (1 if name == "guard_accounting_domain" else 2)
                    for name, value in seeded.items()
                )
                cases += 1
    assert cases == 18


def test_late_ddl_failure_removes_partial_baseline_without_ending_caller_transaction(tmp_path):
    with sqlite3.connect(tmp_path / "atomic.db") as conn:
        conn.execute("BEGIN")
        with pytest.raises(sqlite3.OperationalError, match="no such column"):
            gate.create_baseline(
                conn,
                DDL
                + [
                    {
                        "type": "index",
                        "name": "bad_index",
                        "sql": "CREATE INDEX bad_index ON samples(missing_column)",
                    }
                ],
            )
        assert conn.in_transaction
        assert gate.objects(conn) == []
        # The authorizer must also be restored after a rejected declaration.
        conn.execute("CREATE TABLE caller_work (value TEXT)")
        conn.execute("INSERT INTO caller_work VALUES ('retained')")
        conn.commit()
        assert conn.execute("SELECT value FROM caller_work").fetchall() == [("retained",)]


@pytest.mark.parametrize("change", ["missing", "tampered"])
def test_missing_or_tampered_snapshot_refuses(manifest_path, change):
    manifest = json.loads(manifest_path.read_text())
    path = manifest_path.parent / manifest["baselines"][0]["file"]
    if change == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((ValueError, FileNotFoundError)):
        gate.load_baselines(manifest_path, STORE_PATHS)


@pytest.mark.parametrize("change", ["subset", "duplicate", "anchor", "blob", "repository"])
def test_incomplete_or_inconsistent_manifest_refuses(manifest_path, change):
    manifest = json.loads(manifest_path.read_text())
    if change == "subset":
        manifest["baselines"].pop()
    elif change == "duplicate":
        manifest["baselines"][1] = manifest["baselines"][0]
    elif change == "anchor":
        manifest["publication_anchor"] = "v1.27.0"
    elif change == "blob":
        manifest["baselines"][0]["blob"] = "0" * 40
    else:
        manifest["repository"] = "https://example.invalid/project"
    write_manifest(manifest_path, manifest)
    with pytest.raises(ValueError):
        gate.load_baselines(manifest_path, STORE_PATHS)


@pytest.mark.parametrize("change", ["missing", "absent", "empty"])
def test_incomplete_historical_store_refuses(manifest_path, change):
    def transform(snapshot):
        if change == "missing":
            snapshot["stores"].pop()
        elif change == "absent":
            snapshot["stores"][0]["exists"] = False
        else:
            snapshot["stores"][0]["ddl"]["objects"] = []

    replace_snapshot(manifest_path, transform)
    with pytest.raises(ValueError):
        gate.load_baselines(manifest_path, STORE_PATHS)


def test_preservation_includes_typed_values_and_second_initialization(tmp_path):
    result = gate.check_upgrade(tmp_path / "baseline.db", DDL, migrate, current_schema(tmp_path))
    assert result["result"] == "PASS"
    assert result["historical_tables"] == 1
    assert result["historical_rows"] == 2
    with sqlite3.connect(tmp_path / "baseline.db") as conn:
        assert conn.execute("SELECT count(*) FROM samples WHERE observed IS NULL").fetchone() == (
            2,
        )
        assert conn.execute(
            "SELECT typeof(data), typeof(ratio), typeof(count) FROM samples ORDER BY id"
        ).fetchall() == [
            ("blob", "real", "integer"),
            ("blob", "null", "null"),
        ]


@pytest.mark.parametrize("change", ["delete", "mutate", "duplicate", "type"])
def test_destructive_first_migration_cannot_pass(tmp_path, change):
    def destructive(path):
        migrate(path)
        with sqlite3.connect(path) as conn:
            if change == "delete":
                conn.execute("DELETE FROM samples WHERE id=1")
            elif change == "mutate":
                conn.execute("UPDATE samples SET label='changed' WHERE id=1")
            elif change == "duplicate":
                conn.execute(
                    "INSERT INTO samples (id,label,data) SELECT 3,label,data FROM samples WHERE id=1"
                )
            else:
                conn.execute("UPDATE samples SET data=CAST(data AS TEXT) WHERE id=1")

    with pytest.raises((ValueError, sqlite3.Error)):
        gate.check_upgrade(tmp_path / "baseline.db", DDL, destructive, current_schema(tmp_path))


def test_noop_migration_cannot_hide_missing_current_column(tmp_path):
    with pytest.raises(ValueError, match="upgraded schema differs"):
        gate.check_upgrade(
            tmp_path / "baseline.db", DDL, lambda _path: None, current_schema(tmp_path)
        )


@pytest.mark.parametrize("change", ["row", "schema"])
def test_second_pass_mutation_cannot_pass(tmp_path, change):
    calls = 0

    def non_idempotent(path):
        nonlocal calls
        migrate(path)
        calls += 1
        if calls == 2:
            with sqlite3.connect(path) as conn:
                if change == "row":
                    conn.execute("UPDATE samples SET observed='invented' WHERE id=1")
                else:
                    conn.execute("CREATE INDEX unexpected ON samples(label)")

    with pytest.raises(ValueError, match="not idempotent"):
        gate.check_upgrade(tmp_path / "baseline.db", DDL, non_idempotent, current_schema(tmp_path))


@pytest.mark.parametrize(
    "kind,sql",
    [
        ("table", "CREATE TABLE samples AS SELECT 1 AS id"),
        ("table", "CREATE TABLE samples (id INTEGER); ATTACH ':memory:' AS other"),
        ("view", "CREATE VIEW samples AS SELECT 1"),
        ("table", "CREATE VIRTUAL TABLE samples USING fts5(value)"),
        ("table", "CREATE TABLE different (id INTEGER)"),
    ],
)
def test_unsupported_or_executable_historical_ddl_refuses(kind, sql):
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises((ValueError, sqlite3.Error)):
            gate.create_baseline(conn, [{"type": kind, "name": "samples", "sql": sql}])


def test_multiple_statements_refuse_before_any_schema_or_attachment_change():
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(ValueError, match="multiple historical SQL statements"):
            gate.create_baseline(
                conn,
                [
                    {
                        "type": "table",
                        "name": "samples",
                        "sql": "CREATE TABLE samples (id INTEGER); ATTACH ':memory:' AS other",
                    }
                ],
            )
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []
        assert [row[1] for row in conn.execute("PRAGMA database_list")] == ["main"]


def test_ddl_authorizer_is_released_after_rejected_statement():
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(sqlite3.DatabaseError):
            gate.create_baseline(
                conn,
                [
                    {
                        "type": "table",
                        "name": "samples",
                        "sql": "CREATE TABLE samples AS SELECT 1 AS id",
                    }
                ],
            )
        conn.execute("CREATE TABLE after_refusal (value INTEGER)")
        conn.execute("INSERT INTO after_refusal VALUES (7)")
        assert conn.execute("SELECT value FROM after_refusal").fetchall() == [(7,)]


def test_unknown_seed_type_is_not_silently_skipped():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, value CUSTOM NOT NULL)")
        with pytest.raises(ValueError, match="unsupported historical seed type"):
            gate.seed(conn)


def test_schema_comparison_preserves_constraints_but_ignores_column_order():
    def shape(sql):
        with sqlite3.connect(":memory:") as conn:
            conn.execute(sql)
            return gate.schema(conn)

    original = shape(
        "CREATE TABLE samples (id INTEGER PRIMARY KEY CHECK(id > 0), label TEXT DEFAULT 'a b')"
    )
    reordered = shape(
        "CREATE TABLE samples (label TEXT DEFAULT 'a b', id INTEGER PRIMARY KEY CHECK ( id > 0 ))"
    )
    changed_check = shape(
        "CREATE TABLE samples (id INTEGER PRIMARY KEY CHECK(id >= 0), label TEXT DEFAULT 'a b')"
    )
    changed_default = shape(
        "CREATE TABLE samples (id INTEGER PRIMARY KEY CHECK(id > 0), label TEXT DEFAULT 'ab')"
    )
    assert original == reordered
    assert original != changed_check
    assert original != changed_default


def test_cli_cannot_report_success_for_failure_or_subset(monkeypatch, capsys):
    def fail(_manifest):
        raise ValueError("missing required baseline")

    monkeypatch.setattr(gate, "run_matrix", fail)
    monkeypatch.setattr("sys.argv", [str(_SCRIPT)])
    assert gate.main() == 1
    assert '"result": "FAIL"' in capsys.readouterr().err
    monkeypatch.setattr("sys.argv", [str(_SCRIPT), "--baselines", "1"])
    with pytest.raises(SystemExit) as error:
        gate.main()
    assert error.value.code != 0
