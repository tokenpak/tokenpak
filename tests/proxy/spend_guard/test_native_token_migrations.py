"""Additive token-accounting upgrades preserve legacy usage and unresolved holds.

The frozen v1.28.0 DDL is loaded independently of the current implementation.
Migrations run only against copies of synthetic databases. The pre-workload
variant exercises the earlier coverage-column upgrade followed by token fields;
it is a structural baseline, not a substitute for a six-release migration gate.
These are forward-only upgrades: rollback requires the pre-upgrade database
copy together with its matching application version, not a destructive downgrade.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.spend_guard.reservation import (
    Projection,
    ReservationStore,
    ReservationUnavailable,
    _schema,
)
from tokenpak.proxy.spend_guard.rolling_caps import RollingCapsConfig

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/native_token_pre_migration_v1280.json"
_LEDGER_ID = "a" * 32
_RESERVATION_ID = "tpr_" + "b" * 32
_COVERAGE_ID = "tpc_" + "c" * 32


@pytest.fixture(autouse=True)
def _retire_monitor_writer():
    assert monitor_module._stop_db_write_queue(timeout=20)
    yield
    assert monitor_module._stop_db_write_queue(timeout=20)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            monitor_module._DB_CONNECTION.close()
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_previous(path, store_name, *, with_workload=True):
    fixture = json.loads(_FIXTURE.read_text())
    assert fixture["source_commit"] == "faf63ac6a944cda0a811bae821d6b01872430218"
    store = next(s for s in fixture["stores"] if s["path"].endswith("/" + store_name))
    with closing(sqlite3.connect(path)) as conn:
        for obj in sorted(store["ddl"]["objects"], key=lambda obj: obj["type"] != "table"):
            sql = obj["sql"]
            if obj["name"] == "budget_guard_coverage" and not with_workload:
                previous = ",\n            workload_json TEXT"
                assert sql.count(previous) == 1
                sql = sql.replace(previous, "")
            conn.execute(sql)
        conn.commit()
    path.chmod(0o600)


def _rows(path, table):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]


def _columns(path, table):
    with closing(sqlite3.connect(path)) as conn:
        return {row[1]: tuple(row) for row in conn.execute(f"PRAGMA table_info({table})")}


def _dump(path):
    with closing(sqlite3.connect(path)) as conn:
        return tuple(conn.iterdump())


def _assert_old_columns_unchanged(before, after):
    assert len(before) == len(after)
    for old, new in zip(before, after):
        assert {key: new[key] for key in old} == old


def _seed_monitor(path, audit_hash):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "INSERT INTO guard_accounting_domain VALUES (1, ?, ?)",
            (_LEDGER_ID, audit_hash),
        )
        conn.execute(
            "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, "
            "estimated_cost, cache_read_tokens, cache_creation_tokens, session_id, "
            "agent_id, guard_usage_complete, guard_price_json) "
            "VALUES (?, 'legacy-model', 3, 2, 0.5, 1, 0, 'session-a', 'agent-a', 0, NULL)",
            (time.strftime("%Y-%m-%dT%H:%M:%S"),),
        )
        conn.commit()


def _copied_domain(tmp_path, *, with_workload=True):
    old_monitor = tmp_path / "original-monitor.db"
    old_guard = tmp_path / "original-guard.db"
    monitor_path = tmp_path / "monitor.db"
    guard_path = tmp_path / "guard.db"
    _create_previous(old_monitor, "monitor.db")
    _create_previous(old_guard, "spend_guard.db", with_workload=with_workload)
    audit_hash = hashlib.sha256(str(guard_path.resolve()).encode()).hexdigest()
    _seed_monitor(old_monitor, audit_hash)
    ledger_key = hashlib.sha256(
        (str(monitor_path.resolve()) + ":" + _LEDGER_ID).encode()
    ).hexdigest()
    now = time.time()
    with closing(sqlite3.connect(old_guard)) as conn:
        conn.execute(
            "INSERT INTO budget_reservations (reservation_id, session_id, agent_id, "
            "created_at, expires_at, reserved_input_tokens, reserved_output_tokens, "
            "reserved_cost_usd, ledger_key, owner_instance_id, request_id, "
            "reserved_cache_read_tokens) VALUES (?, 'session-a', 'agent-a', ?, ?, "
            "3, 2, 4.0, ?, 'owner-a', 'legacy-request', 1)",
            (_RESERVATION_ID, now, now + 600, ledger_key),
        )
        conn.execute(
            "INSERT INTO budget_guard_coverage (coverage_id, ledger_key, owner_instance_id, "
            "session_id, started_at, attempts, reservation_id, state) "
            "VALUES (?, ?, 'owner-a', 'session-a', ?, 1, ?, 'sending')",
            (_COVERAGE_ID, ledger_key, now, _RESERVATION_ID),
        )
        if with_workload:
            conn.execute(
                "UPDATE budget_guard_coverage SET workload_json=?",
                ('{"legacy_workload":"unclassified"}',),
            )
        conn.execute("INSERT INTO budget_reservation_generation VALUES (?, 7)", (ledger_key,))
        conn.commit()
    originals = {path: _sha(path) for path in (old_monitor, old_guard)}
    shutil.copyfile(old_monitor, monitor_path)
    shutil.copyfile(old_guard, guard_path)
    guard_path.chmod(0o600)
    monitor = Monitor(monitor_path)
    store = ReservationStore(guard_path, monitor_path)
    assert store.ledger_id == _LEDGER_ID and store.ledger_key == ledger_key
    return store, monitor, originals


def _unchanged_originals(originals):
    assert {path: _sha(path) for path in originals} == originals


def _token_caps():
    return RollingCapsConfig(
        per_agent_max_cost_usd=0,
        per_fleet_max_cost_usd=0,
        per_agent_max_tokens_total=0,
        per_fleet_max_tokens_total=14,
        per_agent_max_cache_read_tokens=0,
        per_fleet_max_cache_read_tokens=0,
    )


def test_previous_monitor_upgrade_defaults_incomplete_and_preserves_ledger(tmp_path):
    original = tmp_path / "original.db"
    working = tmp_path / "working.db"
    _create_previous(original, "monitor.db")
    _seed_monitor(original, "d" * 64)
    with closing(sqlite3.connect(original)) as conn:
        conn.execute(
            "INSERT INTO requests (timestamp, model, input_tokens, output_tokens, "
            "guard_usage_complete, guard_price_json) "
            "VALUES ('2026-09-01T00:00:00', 'legacy-priced-model', 8, 4, 1, ?)",
            ('{"legacy_price":"preserved"}',),
        )
        conn.commit()
    before = _rows(original, "requests")
    domain = _rows(original, "guard_accounting_domain")
    original_hash = _sha(original)
    assert "guard_token_usage_complete" not in _columns(original, "requests")
    shutil.copyfile(original, working)

    Monitor(working)

    migrated = _rows(working, "requests")
    _assert_old_columns_unchanged(before, migrated)
    assert [row["guard_token_usage_complete"] for row in migrated] == [0, 0]
    assert _rows(working, "guard_accounting_domain") == domain
    column = _columns(working, "requests")["guard_token_usage_complete"]
    assert column[2:5] == ("INTEGER", 1, "0")
    with closing(sqlite3.connect(working)) as conn:
        conn.execute(
            "INSERT INTO requests (timestamp, model) VALUES ('2026-09-01T00:00:01', 'new-row')"
        )
        assert conn.execute(
            "SELECT guard_token_usage_complete FROM requests WHERE model='new-row'"
        ).fetchone() == (0,)
        conn.execute("UPDATE requests SET guard_token_usage_complete=1 WHERE model='new-row'")
        conn.commit()
    after_first = _dump(working)
    Monitor(working)
    assert _dump(working) == after_first
    assert _sha(original) == original_hash


def test_older_monitor_upgrade_preserves_unknown_usage_across_additions(tmp_path):
    original = tmp_path / "older-original.db"
    working = tmp_path / "older-working.db"
    with closing(sqlite3.connect(original)) as conn:
        conn.execute(
            "CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER, "
            "output_tokens INTEGER)"
        )
        conn.execute(
            "INSERT INTO requests VALUES (1, '2026-09-01T00:00:00', 'unknown-usage', NULL, NULL)"
        )
        conn.commit()
    before = _rows(original, "requests")
    original_hash = _sha(original)
    shutil.copyfile(original, working)
    Monitor(working)
    migrated = _rows(working, "requests")
    _assert_old_columns_unchanged(before, migrated)
    assert migrated[0]["guard_usage_complete"] == 0
    assert migrated[0]["guard_token_usage_complete"] == 0
    assert migrated[0]["guard_price_json"] is None
    assert migrated[0]["started_at"] is None
    first = _dump(working)
    Monitor(working)
    assert _dump(working) == first
    assert _sha(original) == original_hash


@pytest.mark.parametrize("with_workload", [False, True], ids=["pre-workload", "v1.28.0"])
def test_legacy_reads_preserve_schema_and_refuse_token_reinterpretation(tmp_path, with_workload):
    store, monitor, originals = _copied_domain(tmp_path, with_workload=with_workload)
    guard_hash = _sha(store.path)
    before = _dump(store.path)
    assert "accounting_basis" not in _columns(store.path, "budget_reservations")
    assert store.read_active("agent-a")["fleet_tokens_total"] == 5
    tokens = ReservationStore(store.path, monitor.db_path, accounting_basis="provider_tokens")
    with pytest.raises(ReservationUnavailable, match="token accounting schema is unavailable"):
        tokens.read_active("agent-a")
    assert _dump(store.path) == before and _sha(store.path) == guard_hash
    _unchanged_originals(originals)


@pytest.mark.parametrize("with_workload", [False, True], ids=["pre-workload", "v1.28.0"])
def test_additive_guard_upgrade_keeps_holds_usage_and_evidence_defaults(tmp_path, with_workload):
    store, monitor, originals = _copied_domain(tmp_path, with_workload=with_workload)
    before_holds = _rows(store.path, "budget_reservations")
    before_coverage = _rows(store.path, "budget_guard_coverage")
    before_usage = _rows(monitor.db_path, "requests")
    before_domain = _rows(monitor.db_path, "guard_accounting_domain")

    coverage_id = store.begin_request("new-session", "owner-a")

    holds = _rows(store.path, "budget_reservations")
    _assert_old_columns_unchanged(before_holds, holds)
    assert holds[0]["status"] == "active" and holds[0]["accounting_basis"] is None
    assert holds[0]["actual_cost_usd"] is None and holds[0]["actual_tokens"] is None
    coverage = _rows(store.path, "budget_guard_coverage")
    _assert_old_columns_unchanged(before_coverage, coverage[:1])
    assert coverage[0]["accounting_basis"] is None
    assert coverage[0]["token_observation_json"] is None
    assert coverage[0]["workload_json"] == (
        '{"legacy_workload":"unclassified"}' if with_workload else None
    )
    assert coverage[1]["coverage_id"] == coverage_id
    assert coverage[1]["accounting_basis"] == "priced_usage"
    assert coverage[1]["workload_json"] is None
    assert coverage[1]["token_observation_json"] is None
    assert _rows(store.path, "budget_reservation_generation") == [
        {"ledger_key": store.ledger_key, "generation": 8, "accounting_basis": "priced_usage"}
    ]

    # Existing five recorded tokens plus the five-token hold must still deny
    # another five-token request under a fourteen-token cap after migration.
    ref, breach = store.reserve(
        session_id="session-a",
        agent_id="agent-a",
        owner_instance_id="owner-a",
        request_id="after-upgrade",
        projection=Projection(0, 3, 2, 1),
        caps=_token_caps(),
    )
    assert ref is None
    assert breach.settled_used == 5 and breach.reserved_active == 5
    assert _rows(store.path, "budget_reservations") == holds
    assert _rows(monitor.db_path, "requests") == before_usage
    assert _rows(monitor.db_path, "guard_accounting_domain") == before_domain
    first = _dump(store.path)
    with closing(sqlite3.connect(store.path)) as conn:
        _schema(conn)
        _schema(conn)
    reopened = ReservationStore(store.path, monitor.db_path)
    assert reopened.read_active("agent-a")["fleet_tokens_total"] == 5
    assert _dump(store.path) == first
    _unchanged_originals(originals)


@pytest.mark.parametrize("with_workload", [False, True], ids=["pre-workload", "v1.28.0"])
def test_token_basis_upgrade_refusal_preserves_unresolved_legacy_domain(tmp_path, with_workload):
    store, monitor, originals = _copied_domain(tmp_path, with_workload=with_workload)
    before = {
        table: _rows(store.path, table)
        for table in (
            "budget_reservations",
            "budget_guard_coverage",
            "budget_reservation_generation",
        )
    }
    ledger_before = _dump(monitor.db_path)
    tokens = ReservationStore(store.path, monitor.db_path, accounting_basis="provider_tokens")

    with pytest.raises(
        ReservationUnavailable, match="existing accounting domain cannot change basis"
    ):
        tokens.begin_request("new-token-session", "owner-a")

    # Schema provisioning commits independently; rejected accounting binding
    # must leave every old value intact and never label old holds token-only.
    for table, old_rows in before.items():
        after = _rows(store.path, table)
        _assert_old_columns_unchanged(old_rows, after)
        assert all(row["accounting_basis"] is None for row in after)
    assert _rows(store.path, "budget_guard_coverage")[0]["token_observation_json"] is None
    assert _dump(monitor.db_path) == ledger_before
    assert store.read_active("agent-a")["fleet_tokens_total"] == 5
    first_failure = _dump(store.path)
    with pytest.raises(
        ReservationUnavailable, match="existing accounting domain cannot change basis"
    ):
        tokens.begin_request("new-token-session", "owner-a")
    assert _dump(store.path) == first_failure
    _unchanged_originals(originals)
