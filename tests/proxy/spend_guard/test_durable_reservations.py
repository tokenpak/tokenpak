"""Budget admission and actual monitor commits share durable identities."""

from __future__ import annotations

import multiprocessing
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.spend_guard.reservation import (
    Projection,
    ReservationStore,
    ReservationUnavailable,
    pessimistic_output_reservation,
)
from tokenpak.proxy.spend_guard.rolling_caps import RollingCapsConfig


def _caps(**overrides):
    values = dict(
        per_agent_max_cost_usd=0,
        per_agent_max_tokens_total=0,
        per_agent_max_cache_read_tokens=0,
        per_fleet_max_cost_usd=0,
        per_fleet_max_tokens_total=0,
        per_fleet_max_cache_read_tokens=0,
    )
    values.update(overrides)
    return RollingCapsConfig(**values)


def _reserve(store, projection=None, **kwargs):
    args = dict(
        session_id="session-a",
        agent_id="agent-a",
        owner_instance_id="instance-a",
        request_id="request-a",
        projection=projection or Projection(4.0, 3, 2, 1),
        caps=_caps(per_fleet_max_cost_usd=10),
    )
    args.update(kwargs)
    return store.reserve(**args)


def _process_admit(audit, monitor, start, result):
    start.wait(10)
    ref, breach = _reserve(ReservationStore(audit, monitor))
    result.put(bool(ref) and breach is None)


@pytest.fixture
def domain(tmp_path):
    assert monitor_module._stop_db_write_queue(timeout=3)
    monitor = Monitor(tmp_path / "monitor.db")
    store = ReservationStore(tmp_path / "spend_guard.db", monitor.db_path)
    yield store, monitor
    assert monitor.stop(timeout=3)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            monitor_module._DB_CONNECTION.close()
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None


def _log(monitor, ref, cost=4.0, complete=True):
    monitor.log(
        model="test-model",
        input_tokens=3,
        output_tokens=2,
        cost=cost,
        cache_read_tokens=1,
        latency_ms=1,
        status_code=200,
        endpoint="/v1/messages",
        session_id="session-a",
        agent_id="agent-a",
        reservation_ref=ref,
        guard_usage_complete=complete,
    )
    assert monitor.flush(timeout=3)


def _rows(store):
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM budget_reservations")]


@pytest.mark.parametrize(
    "dimension",
    [
        "per_agent_max_cost_usd",
        "per_agent_max_tokens_total",
        "per_agent_max_cache_read_tokens",
        "per_fleet_max_cost_usd",
        "per_fleet_max_tokens_total",
        "per_fleet_max_cache_read_tokens",
    ],
)
def test_each_dimension_denies_without_inserting(domain, dimension):
    store, _ = domain
    projection = Projection(4.0, 2, 2, 4)
    caps = _caps(**{dimension: 7})
    assert _reserve(store, projection, caps=caps)[0]
    ref, breach = _reserve(store, projection, caps=caps)
    assert ref is None and breach.reserved_active == 4 and breach.settled_used == 0
    assert len(_rows(store)) == 1


def test_threads_enforce_joint_cap(domain):
    store, _ = domain
    # This test isolates concurrent cap admission from first-use schema I/O.
    # Schema migration and bounded lock refusal have their own checks below.
    warmup = store.begin_request("setup-session", "instance-a")
    store.finish_request(warmup)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _reserve(store), range(8)))
    assert sum(ref is not None for ref, _ in results) == 2
    assert len(_rows(store)) == 2


def test_contended_store_refuses_within_busy_timeout_without_reserving(domain):
    store, _ = domain
    warmup = store.begin_request("setup-session", "instance-a")
    store.finish_request(warmup)
    with sqlite3.connect(store.path) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute("BEGIN IMMEDIATE")
        pending = pool.submit(_reserve, store)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            pending.result(timeout=6)
        blocker.rollback()
    assert _rows(store) == []


def test_current_schema_does_not_require_a_writer_lock(domain):
    from tokenpak.proxy.spend_guard.reservation import _schema

    store, _ = domain
    coverage = store.begin_request("setup-session", "instance-a")
    store.finish_request(coverage)
    with sqlite3.connect(store.path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with sqlite3.connect(store.path.as_uri() + "?mode=rw", uri=True, timeout=0.1) as checker:
            _schema(checker)
        writer.rollback()


def test_existing_domain_binding_does_not_require_a_writer_lock(domain):
    store, monitor = domain
    coverage = store.begin_request("setup-session", "instance-a")
    store.finish_request(coverage)
    with sqlite3.connect(monitor.db_path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        store._bind_domain()
        writer.rollback()


def test_processes_enforce_joint_cap(domain):
    store, monitor = domain
    # Match the initialized request lifecycle used by the native send path.
    warmup = store.begin_request("setup-session", "instance-a")
    store.finish_request(warmup)
    assert monitor.stop(timeout=3)
    context = multiprocessing.get_context("spawn")
    start, result = context.Event(), context.Queue()
    workers = [
        context.Process(
            target=_process_admit, args=(str(store.path), str(monitor.db_path), start, result)
        )
        for _ in range(4)
    ]
    for worker in workers:
        worker.start()
    start.set()
    try:
        assert sum(result.get(timeout=20) for _ in workers) == 2
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=3)


@pytest.mark.parametrize("synchronous", [False, True])
def test_actual_commit_settles_full_hold(domain, synchronous):
    store, monitor = domain
    if synchronous:
        assert monitor.stop(timeout=3)
    ref, _ = _reserve(store)
    with pytest.raises(ReservationUnavailable, match="matching committed"):
        store.settle_after_commit(ref, Projection(1.0, 3, 2, 1))
    _log(monitor, ref, cost=1.0)
    (row,) = _rows(store)
    assert row["status"] == "settled" and row["actual_cost_usd"] == 1.0
    assert store.read_active("agent-a")["fleet_cost_usd"] == 0
    assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=5))[0]


def test_commit_before_notification_neither_undercounts_nor_doubles(domain, monkeypatch):
    store, monitor = domain
    ref, _ = _reserve(store)
    monkeypatch.setattr(monitor_module, "_notify_durable_guard_commit", lambda *_: None)
    _log(monitor, ref, cost=6.0)
    assert _rows(store)[0]["status"] == "active"
    assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=10))[0]
    assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=10))[1]


def test_failed_commit_keeps_hold(domain, monkeypatch):
    store, monitor = domain
    ref, _ = _reserve(store)
    monkeypatch.setattr(
        monitor_module,
        "_write_row",
        lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError("injected write failure")),
    )
    _log(monitor, ref)
    assert _rows(store)[0]["status"] == "active"
    assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=7))[1]


def test_incomplete_usage_cannot_settle_or_admit_again(domain):
    store, monitor = domain
    ref, _ = _reserve(store)
    _log(monitor, ref, complete=False)
    assert _rows(store)[0]["status"] == "active"
    with pytest.raises(ReservationUnavailable, match="incomplete"):
        _reserve(store)


def test_expiry_preserves_uncertainty_and_late_commit_resolves(domain):
    store, monitor = domain
    ref, _ = _reserve(store)
    expired = time.time() - 1
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE budget_reservations SET expires_at=?", (expired,))
    with pytest.raises(ReservationUnavailable, match="expired"):
        _reserve(store)
    _log(monitor, ref)
    (row,) = _rows(store)
    assert row["expires_at"] == expired and row["settled_at"] > expired
    assert row["status"] == "settled"
    assert _reserve(store)[0]


def test_force_records_hold_without_weakening_other_admissions(domain):
    store, _ = domain
    assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=1), force=True)[0]
    assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=7))[1]


def test_read_and_missing_settlement_never_provision(domain, tmp_path):
    store, _ = domain
    with pytest.raises(FileNotFoundError):
        store.read_active("agent-a")
    assert not store.path.exists()
    ref, _ = _reserve(store)
    original = store.path.read_bytes()
    assert store.read_active("agent-a")["fleet_cost_usd"] == 4
    assert store.path.read_bytes() == original
    store.path.rename(tmp_path / "saved.db")
    with pytest.raises(ReservationUnavailable):
        store.settle_after_commit(ref, Projection(4.0, 3, 2, 1))
    assert not store.path.exists()


def test_monitor_domains_are_separate(domain, tmp_path):
    store, _ = domain
    assert _reserve(store)[0]
    second_monitor = Monitor(tmp_path / "second-monitor.db")
    try:
        second = ReservationStore(store.path, second_monitor.db_path)
        assert _reserve(second, caps=_caps(per_fleet_max_cost_usd=4))[0]
        assert _reserve(store, caps=_caps(per_fleet_max_cost_usd=7))[1]
    finally:
        assert second_monitor.stop(timeout=3)


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "4", 10**400])
def test_invalid_projection_writes_nothing(domain, value):
    store, _ = domain
    with pytest.raises(ValueError):
        _reserve(store, Projection(value, 3, 2, 1))
    assert not store.path.exists()


def test_invalid_rows_cannot_cancel_each_other_in_sums(domain):
    store, _ = domain
    _reserve(store)
    _reserve(store)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE budget_reservations SET reserved_cost_usd=-1 "
            "WHERE rowid=(SELECT MIN(rowid) FROM budget_reservations)"
        )
    with pytest.raises(ValueError):
        _reserve(store)


def test_one_monitor_cannot_split_admissions_across_stores(domain, tmp_path):
    store, monitor = domain
    assert _reserve(store)[0]
    competing = ReservationStore(tmp_path / "other-guard.db", monitor.db_path)
    with pytest.raises(ReservationUnavailable, match="another reservation store"):
        _reserve(competing)
    assert not competing.path.exists()


def test_replaced_monitor_generation_refuses_old_store_object(domain):
    store, monitor = domain
    _reserve(store)
    with sqlite3.connect(monitor.db_path) as conn:
        conn.execute("UPDATE guard_accounting_domain SET ledger_id=?", ("f" * 32,))
    with pytest.raises(ReservationUnavailable, match="generation"):
        _reserve(store)


def test_fresh_recorded_baseline_is_read_after_serialization(domain):
    store, monitor = domain
    ref, _ = _reserve(store)
    store.release_unsent(ref)
    with sqlite3.connect(store.path) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        blocker.execute("BEGIN IMMEDIATE")
        pending = pool.submit(_reserve, store)
        # Record the competing spend while the caller waits for the guard
        # write lock. A baseline frozen before the lock would admit it.
        _log(monitor, None, cost=7)
        blocker.commit()
        new_ref, breach = pending.result(timeout=5)
    assert new_ref is None and breach.settled_used == 7


def test_legacy_reservations_are_preserved_and_cannot_be_assumed_scoped(domain):
    store, _ = domain
    with sqlite3.connect(store.path) as conn:
        conn.execute("""CREATE TABLE budget_reservations (
            reservation_id TEXT PRIMARY KEY, session_id TEXT, fleet_id TEXT, agent_id TEXT,
            created_at REAL, expires_at REAL, reserved_input_tokens INTEGER,
            reserved_output_tokens INTEGER, reserved_cost_usd REAL, status TEXT,
            actual_cost_usd REAL, actual_tokens INTEGER)""")
        conn.execute(
            "INSERT INTO budget_reservations VALUES "
            "('legacy', 'session-a', '', 'agent-a', ?, ?, 1, 2, 3, 'active', NULL, NULL)",
            (time.time(), time.time() + 600),
        )
    with pytest.raises(ReservationUnavailable, match="legacy"):
        _reserve(store)
    assert _rows(store)[0]["reservation_id"] == "legacy"


@pytest.mark.parametrize("include_workload", [False, True])
def test_snapshot_fence_detects_intervening_request(domain, monkeypatch, include_workload):
    store, monitor = domain
    coverage = store.begin_request("session-a", "instance-a")
    ref, _ = _reserve(store)
    store.attempted_send(coverage, ref)
    _log(monitor, ref)
    store.finish_request(coverage)
    assert store.snapshot("session-a", 3600)["guard_evidence_eligible"]
    calls = 0
    original = store._read_generation

    def race(conn):
        nonlocal calls
        calls += 1
        if calls == 2:
            store.begin_request("another-session", "instance-b")
        return original(conn)

    monkeypatch.setattr(store, "_read_generation", race)
    with pytest.raises(ReservationUnavailable, match="changed during observation"):
        store.snapshot("session-a", 3600, include_workload=include_workload)


@pytest.mark.parametrize("include_workload", [False, True])
def test_readonly_snapshot_does_not_prune_or_write(domain, monkeypatch, include_workload):
    store, monitor = domain
    coverage = store.begin_request("session-a", "instance-a")
    ref, _ = _reserve(store)
    store.attempted_send(coverage, ref)
    _log(monitor, ref)
    store.finish_request(coverage)
    original = sqlite3.connect
    connections = []

    def read_connection(path, *args, **kwargs):
        assert "mode=ro" in path
        conn = original(path, *args, **kwargs)
        connections.append(path)
        denied = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_ALTER_TABLE,
        }
        conn.set_authorizer(
            lambda action, *_: sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK
        )
        return conn

    monkeypatch.setattr(sqlite3, "connect", read_connection)
    assert store.snapshot("session-a", 3600, include_workload=include_workload)[
        "guard_evidence_eligible"
    ]
    assert len(connections) == 3


def test_history_prunes_only_old_completed_rows_during_writes(domain):
    store, _ = domain
    ref, _ = _reserve(store)
    store.release_unsent(ref)
    unresolved, _ = _reserve(store)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE budget_reservations SET created_at=0, settled_at=0")
    store.begin_request("session-a", "instance-a")
    assert [row["reservation_id"] for row in _rows(store)] == [unresolved.reservation_id]


def test_capacity_refuses_without_discarding_unresolved_history(domain):
    store, _ = domain
    store.max_records = 2
    _reserve(store)
    _reserve(store)
    with pytest.raises(ReservationUnavailable, match="capacity"):
        _reserve(store)
    assert len(_rows(store)) == 2


def test_monitor_claim_without_request_lifecycle_is_not_eligible(domain):
    store, monitor = domain
    ref, _ = _reserve(store)
    _log(monitor, ref)
    snapshot = store.snapshot("session-a", 3600)
    assert not snapshot["guard_evidence_eligible"]
    assert "ledger_coverage_incomplete" in snapshot["reason_codes"]


def test_changed_monitor_attribution_cannot_settle_or_lower_agent_spend(domain, monkeypatch):
    store, monitor = domain
    ref, _ = _reserve(store)
    monkeypatch.setattr(monitor_module, "_notify_durable_guard_commit", lambda *_: None)
    _log(monitor, ref)
    with sqlite3.connect(monitor.db_path) as ledger:
        ledger.execute("UPDATE requests SET agent_id='different-agent'")
    with pytest.raises(ReservationUnavailable, match="attribution"):
        store.settle_after_commit(ref, Projection(4.0, 3, 2, 1))
    with pytest.raises(ReservationUnavailable, match="attribution"):
        _reserve(store)


@pytest.mark.parametrize(
    "declared,context,expected",
    [(100, 1000, 100), (None, 1000, 900), (None, 100000, 32000), (None, None, 32000)],
)
def test_output_reservation(declared, context, expected):
    assert pessimistic_output_reservation(declared, context, 100) == expected


def _record_workload(domain):
    from tests.proxy.spend_guard.test_request_workload import response

    store, monitor = domain
    observed = response()
    coverage = store.begin_request("session-a", "instance-a")
    ref, _ = _reserve(store)
    store.attempted_send(coverage, ref, workload=observed)
    monitor.log(
        model=observed.model,
        input_tokens=33,
        output_tokens=7,
        cache_read_tokens=20,
        cache_creation_tokens=3,
        cost=4.0,
        latency_ms=1,
        status_code=200,
        endpoint="/v1/messages",
        session_id="session-a",
        agent_id="agent-a",
        reservation_ref=ref,
        guard_usage_complete=True,
    )
    assert monitor.flush(timeout=3)
    store.finish_request(coverage)
    return coverage, observed


def test_workload_snapshot_binds_only_requested_session_and_exact_committed_counts(domain):
    store, _ = domain
    _, observed = _record_workload(domain)
    ordinary = store.snapshot("session-a", 3600)
    assert "workload_observation" not in ordinary
    result = store.snapshot("session-a", 3600, include_workload=True)
    evidence = result.pop("workload_observation")
    assert result["accounting_generation"] == ordinary["accounting_generation"]
    assert evidence["available"] and evidence["reason_codes"] == []
    assert evidence["workload"] == observed.to_dict()
    import hashlib

    assert evidence["request_id_sha256"] == hashlib.sha256(b"request-a").hexdigest()
    other = store.snapshot("private-other-session", 3600, include_workload=True)
    assert other["workload_observation"]["workload"] is None
    assert "request-a" not in str(other)


def test_workload_snapshot_refuses_newer_unresolved_send_and_ledger_mismatch(domain):
    store, monitor = domain
    _record_workload(domain)
    with sqlite3.connect(monitor.db_path) as ledger:
        ledger.execute("UPDATE requests SET cache_creation_tokens=2")
    evidence = store.snapshot("session-a", 3600, include_workload=True)["workload_observation"]
    assert not evidence["available"] and "workload_ledger_mismatch" in evidence["reason_codes"]
    newest = store.begin_request("session-a", "instance-a")
    store.attempted_send(newest, None)
    evidence = store.snapshot("session-a", 3600, include_workload=True)["workload_observation"]
    assert not evidence["available"] and evidence["workload"] is None


def test_overlapping_session_requests_do_not_claim_a_single_current_workload(domain):
    store, _ = domain
    first, _ = _record_workload(domain)
    second, _ = _record_workload(domain)
    with sqlite3.connect(store.path) as conn:
        started, ended = conn.execute(
            "SELECT started_at, ended_at FROM budget_guard_coverage WHERE coverage_id=?", (second,)
        ).fetchone()
        # Synthetic completed requests with overlapping lifecycle intervals.
        conn.execute(
            "UPDATE budget_guard_coverage SET ended_at=? WHERE coverage_id=?",
            (started + (ended - started) / 2, first),
        )
    snapshot = store.snapshot("session-a", 3600, include_workload=True)
    assert snapshot["guard_evidence_eligible"]
    evidence = snapshot["workload_observation"]
    assert not evidence["available"] and evidence["workload"] is None
    assert evidence["reason_codes"] == ["session_request_order_ambiguous"]


def test_workload_read_preserves_legacy_schema_and_write_adds_column_without_loss(domain):
    store, _ = domain
    _record_workload(domain)
    with sqlite3.connect(store.path) as conn:
        conn.execute("ALTER TABLE budget_guard_coverage DROP COLUMN workload_json")
    evidence = store.snapshot("session-a", 3600, include_workload=True)["workload_observation"]
    assert not evidence["available"]
    with sqlite3.connect(store.path) as conn:
        assert "workload_json" not in {
            r[1] for r in conn.execute("PRAGMA table_info(budget_guard_coverage)")
        }
        assert conn.execute("SELECT count(*) FROM budget_guard_coverage").fetchone()[0] == 1
    store.begin_request("session-b", "instance-a")
    with sqlite3.connect(store.path) as conn:
        assert "workload_json" in {
            r[1] for r in conn.execute("PRAGMA table_info(budget_guard_coverage)")
        }
        assert conn.execute("SELECT count(*) FROM budget_guard_coverage").fetchone()[0] == 2


def test_stored_workload_corruption_is_not_returned_as_content(domain):
    store, _ = domain
    _record_workload(domain)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE budget_guard_coverage SET workload_json=?", ('{"prompt":"private"}',))
    with pytest.raises(ValueError, match="workload fields"):
        store.snapshot("session-a", 3600, include_workload=True)
