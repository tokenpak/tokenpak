#!/usr/bin/env python3
"""Upgrade seeded release schemas using current code, without historical execution.

The offline manifest pins the last six published minor-release snapshots. Each
baseline/store gets an independent database, exact historical-row preservation,
current-schema comparison and a second initialization for idempotence. This is
a forward migration test, not proof that older binaries can open upgraded data;
rollback requires the pre-upgrade backup and its matching runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "tests/release_gate/fixtures/migration-baselines/manifest.json"
DEFAULT_BASELINES = 6
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
SQL_TOKEN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[^\s]")
Materializer = Callable[[Path], None]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def identifier(name: str) -> str:
    require(bool(IDENTIFIER.fullmatch(name)), f"unsupported identifier: {name!r}")
    return '"' + name + '"'


def sql_tokens(sql: str) -> tuple[str, ...]:
    tokens = [token if token[0] in "'\"" else token.lower() for token in SQL_TOKEN.findall(sql)]
    if tokens[-1:] == [";"]:
        tokens.pop()
    # SQLite removes this clause when storing CREATE statements.
    return tuple(
        token
        for index, token in enumerate(tokens)
        if not any(
            tokens[start : start + 3] == ["if", "not", "exists"]
            for start in range(max(0, index - 2), index + 1)
        )
    )


def table_definition(sql: str) -> tuple:
    """Retain constraints/defaults while ignoring historical column order."""
    tokens = sql_tokens(sql)
    start = tokens.index("(")
    clauses, clause, depth = [], [], 0
    for index in range(start + 1, len(tokens)):
        token = tokens[index]
        if token == ")" and depth == 0:
            clauses.append(tuple(clause))
            return tuple(sorted(clauses)), tokens[index + 1 :]
        if token == "," and depth == 0:
            clauses.append(tuple(clause))
            clause = []
        else:
            clause.append(token)
            depth += (token == "(") - (token == ")")
    raise ValueError("unterminated table definition")


def objects(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()


def schema(conn: sqlite3.Connection) -> dict:
    result = {}
    for kind, name, sql in objects(conn):
        require(kind in {"table", "index"}, f"unsupported schema object: {kind}/{name}")
        if kind == "table":
            columns = [
                (
                    row[1],
                    row[2].upper(),
                    row[3],
                    sql_tokens(row[4]) if row[4] is not None else None,
                    row[5],
                )
                for row in conn.execute(f"PRAGMA table_info({identifier(name)})")
            ]
            result[name] = {
                "kind": kind,
                "columns": sorted(columns),
                "definition": table_definition(sql),
                "foreign_keys": conn.execute(
                    f"PRAGMA foreign_key_list({identifier(name)})"
                ).fetchall(),
            }
        else:
            result[name] = {"kind": kind, "definition": sql_tokens(sql)}
    return {"objects": result, "user_version": conn.execute("PRAGMA user_version").fetchone()[0]}


def capture(conn: sqlite3.Connection, columns: dict[str, list[str]] | None = None) -> dict:
    """Keep every value and SQLite storage type; do not depend on row order."""
    if columns is None:
        columns = {
            name: sorted(row[1] for row in conn.execute(f"PRAGMA table_info({identifier(name)})"))
            for kind, name, _ in objects(conn)
            if kind == "table"
        }
    result = {}
    for table, names in columns.items():
        fields = ", ".join(f"{identifier(name)}, typeof({identifier(name)})" for name in names)
        rows = conn.execute(f"SELECT {fields} FROM {identifier(table)}").fetchall()
        encoded = [
            [
                (value.hex() if isinstance(value, bytes) else value, row[index + 1])
                for index, value in enumerate(row)
                if index % 2 == 0
            ]
            for row in rows
        ]
        result[table] = {"columns": names, "rows": sorted(encoded, key=lambda row: json.dumps(row))}
    return result


def integrity(conn: sqlite3.Connection) -> None:
    require(
        conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "database integrity failed"
    )
    require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "foreign-key integrity failed")


def create_baseline(conn: sqlite3.Connection, ddl: list[dict]) -> None:
    """Admit only bounded table/index DDL, using one execute per object."""
    require(bool(ddl), "empty historical schema")
    names = set()
    for obj in ddl:
        kind, name, sql = obj["type"], obj["name"], obj["sql"]
        identifier(name)
        require(name not in names, f"duplicate historical object: {name}")
        names.add(name)
        require(kind in {"table", "index"}, f"unsupported historical DDL: {kind}")
        prefix = ["create", kind] if kind == "table" else ["create", "index"]
        tokens = list(sql_tokens(sql))
        require(";" not in tokens, "multiple historical SQL statements are forbidden")
        if tokens[:3] == ["create", "unique", "index"]:
            tokens.pop(1)
        require(
            tokens[:2] == prefix and tokens[2:3] == [name.lower()], f"DDL identity mismatch: {name}"
        )

    def authorize(action, arg1, arg2, _database, _trigger):
        if action in {
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_CREATE_INDEX,
            sqlite3.SQLITE_REINDEX,
        }:
            allowed = (
                arg1 in names
                or arg1 == "sqlite_sequence"
                or (
                    action == sqlite3.SQLITE_CREATE_INDEX
                    and arg2 in names
                    and arg1.startswith(f"sqlite_autoindex_{arg2}_")
                )
            )
        elif action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE}:
            allowed = arg1 == "sqlite_master"
        elif action == sqlite3.SQLITE_READ:
            allowed = arg1 in names or arg1 in {"sqlite_master", "sqlite_sequence"}
        elif action == sqlite3.SQLITE_FUNCTION:
            allowed = arg2 == "date"
        else:
            allowed = False
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    try:
        for obj in sorted(ddl, key=lambda obj: (obj["type"] != "table", obj["name"])):
            conn.execute(obj["sql"])
    finally:
        # Disabling with None is supported only from Python 3.11. Older
        # interpreters require a callable even after the bounded DDL phase.
        conn.set_authorizer(None if sys.version_info >= (3, 11) else lambda *_: sqlite3.SQLITE_OK)
    actual = {(kind, name): sql_tokens(sql) for kind, name, sql in objects(conn)}
    expected = {(obj["type"], obj["name"]): sql_tokens(obj["sql"]) for obj in ddl}
    require(actual == expected, "historical DDL did not materialize exactly")


def seed_value(table: str, column: tuple, row: int) -> object:
    _, name, declared_type, not_null, _default, primary_key = column
    if row == 2 and not not_null and not primary_key:
        return None
    kind = declared_type.upper()
    if kind == "TEXT":
        # Harmless valid headers avoid an intentional credential-redaction migration.
        if (
            name == "raw_request_headers"
            or name.endswith("_json")
            or name in {"payload", "provider_usage_raw"}
        ):
            return "{}"
        if name == "actions":
            return "[]"
        # Distinct valid dates avoid the intentional duplicate-alert collapse.
        if name in {"timestamp", "date", "started_at"}:
            return f"2026-01-0{row}T00:00:00"
        if name in {"trace_id", "request_id", "session_id", "ledger_key", "reservation_id"}:
            return f"synthetic-{name}-{row}"
        return f"synthetic-{table}-{name}-{row}-é"
    if kind == "INTEGER":
        return row
    if kind == "REAL":
        return row + 0.25
    if kind == "BLOB":
        return b"synthetic\x00\xff" + bytes([row])
    raise ValueError(f"unsupported historical seed type: {table}.{name} {kind}")


def seed(conn: sqlite3.Connection) -> dict:
    for kind, table, _ in objects(conn):
        if kind != "table":
            continue
        columns = conn.execute(f"PRAGMA table_info({identifier(table)})").fetchall()
        require(bool(columns), f"unseedable table: {table}")
        names = ", ".join(identifier(column[1]) for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        # This published table has the explicit singleton CHECK(id=1).
        count = 1 if table == "guard_accounting_domain" else 2
        for row in range(1, count + 1):
            conn.execute(
                f"INSERT INTO {identifier(table)} ({names}) VALUES ({placeholders})",
                [seed_value(table, column, row) for column in columns],
            )
    conn.commit()
    result = capture(conn)
    require(
        bool(result) and all(value["rows"] for value in result.values()),
        "unseeded historical table",
    )
    integrity(conn)
    return result


def load_baselines(manifest_path: Path, store_paths: set[str]) -> tuple[dict, list[tuple]]:
    manifest = json.loads(manifest_path.read_text())
    require(manifest["version"] == 1, "unsupported baseline manifest")
    require(
        manifest["repository"] == "https://github.com/tokenpak/tokenpak",
        "noncanonical baseline repository",
    )
    require(
        manifest["snapshot_path"] == "tokenpak/_snapshots/telemetry-schema.json",
        "unexpected snapshot path",
    )
    rows = manifest["baselines"]
    require(len(rows) == DEFAULT_BASELINES, "the gate requires all six baselines")
    versions, result = [], []
    for row in rows:
        version = re.fullmatch(r"v(\d+)\.(\d+)\.0", row["tag"])
        require(version is not None, "invalid minor baseline tag")
        require(int(version[1]) == manifest["major"], "baseline crosses major boundary")
        versions.append(int(version[2]))
        for key in ("tag_object", "commit", "tree", "blob"):
            require(re.fullmatch(r"[0-9a-f]{40}", row[key]) is not None, f"invalid baseline {key}")
        require(re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None, "invalid snapshot digest")
        require(row["file"] == row["sha256"] + ".json", "unexpected snapshot filename")
        path = manifest_path.parent / row["file"]
        require(not path.is_symlink(), "snapshot must be a regular fixture")
        raw = path.read_bytes()
        require(
            len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"],
            "snapshot byte mismatch",
        )
        require(
            hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() == row["blob"],
            "snapshot Git blob mismatch",
        )
        snapshot = json.loads(raw)
        stores = snapshot["stores"]
        require(snapshot["version"] == "1.0", "unsupported snapshot format")
        require(
            len(stores) == len(store_paths) and {store["path"] for store in stores} == store_paths,
            "incomplete historical store set",
        )
        for store in stores:
            require(
                store.get("exists") is True and bool(store.get("ddl", {}).get("objects")),
                "missing historical store schema",
            )
            require("error" not in store["ddl"], "historical snapshot error")
        result.append((row, stores))
    require(
        versions == list(range(versions[-1] - DEFAULT_BASELINES + 1, versions[-1] + 1)),
        "noncontiguous baseline set",
    )
    require(manifest["publication_anchor"] == rows[-1]["tag"], "publication anchor mismatch")
    return manifest, result


def check_upgrade(
    db_path: Path, ddl: list[dict], materialize: Materializer, expected_schema: dict
) -> dict:
    require(not db_path.exists(), "baseline database must be fresh")
    with closing(sqlite3.connect(db_path)) as conn:
        create_baseline(conn, ddl)
        before = seed(conn)
    columns = {table: value["columns"] for table, value in before.items()}
    materialize(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        integrity(conn)
        require(capture(conn, columns) == before, "historical rows changed during migration")
        first_schema, first_rows = schema(conn), capture(conn)
        require(first_schema == expected_schema, "upgraded schema differs from current schema")
    materialize(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        integrity(conn)
        require(
            schema(conn) == first_schema and capture(conn) == first_rows,
            "migration is not idempotent",
        )
    return {
        "result": "PASS",
        "historical_tables": len(before),
        "historical_rows": sum(len(value["rows"]) for value in before.values()),
        "seed_sha256": digest(before),
        "schema_sha256": digest(first_schema),
        "migrated_rows_sha256": digest(first_rows),
        "preservation": "PASS",
        "idempotence": "PASS",
        "integrity": "PASS",
    }


def current_stores() -> dict[str, Materializer]:
    # Reuse the schema generator's complete current owner registry. Never load
    # historical Python or resolve an installed package ahead of this checkout.
    sys.path.insert(0, str(REPO_ROOT))
    path = REPO_ROOT / "scripts/release_gate/gen_telemetry_schema.py"
    spec = importlib.util.spec_from_file_location("migration_current_schema", path)
    require(spec is not None and spec.loader is not None, "current schema registry unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        store["path"]: module._MATERIALIZERS[store["materializer"]]
        for store in module.TRACKED_STORES
    }


def run_matrix(manifest_path: Path = DEFAULT_MANIFEST) -> dict:
    previous = {key: os.environ.get(key) for key in ("HOME", "TOKENPAK_SNAPSHOT_GEN")}
    try:
        with tempfile.TemporaryDirectory(prefix="tokenpak-migration-matrix-") as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            os.environ.update(HOME=str(home), TOKENPAK_SNAPSHOT_GEN="1")
            materializers = current_stores()
            manifest, baselines = load_baselines(manifest_path, set(materializers))
            fresh = {}
            for index, (store, materialize) in enumerate(materializers.items()):
                path = root / f"current-{index}.db"
                materialize(path)
                with closing(sqlite3.connect(path)) as conn:
                    integrity(conn)
                    fresh[store] = schema(conn)
            cases = []
            for row, stores in baselines:
                for index, store in enumerate(stores):
                    label = f"{row['tag']}:{store['path']}"
                    print(f"migration_multihop: {label}", file=sys.stderr)
                    try:
                        result = check_upgrade(
                            root / f"{row['tag']}-{index}.db",
                            store["ddl"]["objects"],
                            materializers[store["path"]],
                            fresh[store["path"]],
                        )
                    except Exception as exc:
                        raise ValueError(f"{label}: {exc}") from exc
                    cases.append({"baseline": row, "store": store["path"], **result})
            require(
                len(cases) == DEFAULT_BASELINES * len(materializers), "incomplete migration matrix"
            )
            return {
                "result": "PASS",
                "publication_anchor": manifest["publication_anchor"],
                "case_count": len(cases),
                "cases": cases,
            }
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baselines", type=int, choices=[DEFAULT_BASELINES], default=DEFAULT_BASELINES
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        print(json.dumps(run_matrix(args.manifest), sort_keys=True, indent=2))
    except Exception as exc:
        print(
            json.dumps({"result": "FAIL", "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
