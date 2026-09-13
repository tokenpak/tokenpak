"""Real commits establish token coverage independently of priced guard evidence."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from tokenpak.proxy import monitor as monitor_module
from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.spend_guard.policy import load_config
from tokenpak.proxy.spend_guard.request_tokens import observe_request, observe_response
from tokenpak.proxy.spend_guard.reservation import (
    Projection,
    ReservationStore,
    ReservationUnavailable,
)
from tokenpak.proxy.spend_guard.rolling_caps import RollingCapsConfig


@pytest.fixture
def domain(tmp_path):
    assert monitor_module._stop_db_write_queue(timeout=3)
    monitor = Monitor(tmp_path / "monitor.db")
    store = ReservationStore(
        tmp_path / "guard.db", monitor.db_path, accounting_basis="provider_tokens"
    )
    yield store, monitor
    assert monitor.stop(timeout=3)
    with monitor_module._DB_LOCK:
        if monitor_module._DB_CONNECTION is not None:
            monitor_module._DB_CONNECTION.close()
        monitor_module._DB_CONNECTION = None
        monitor_module._DB_CONNECTION_PATH = None


def caps(**updates):
    values = dict(
        per_agent_max_cost_usd=0,
        per_fleet_max_cost_usd=0,
        per_agent_max_tokens_total=100,
        per_fleet_max_tokens_total=100,
        per_agent_max_cache_read_tokens=0,
        per_fleet_max_cache_read_tokens=0,
    )
    values.update(updates)
    return RollingCapsConfig(**values)


def begin(store, *, request_id="request-a", policy=None):
    coverage = store.begin_request("session-a", "owner-a")
    ref, breach = store.reserve(
        session_id="session-a",
        agent_id="agent-a",
        owner_instance_id="owner-a",
        request_id=request_id,
        projection=Projection(1, 33, 7, 33),
        caps=policy or caps(),
    )
    return coverage, ref, breach


def observed(store, coverage, ref):
    req = observe_request(
        b'{"model":"claude-sonnet-4-6","max_tokens":64,"messages":[{"role":"user","content":"x"}]}',
        "https://api.anthropic.com/v1/messages",
        {"Authorization": "Bearer private"},
    )
    store.attempted_send(coverage, ref)
    store.record_tokens(coverage, req)
    raw = json.dumps(
        {
            "id": "msg_synthetic",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 7,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 3,
            },
        }
    ).encode()
    result = observe_response(req, raw, streaming=False, complete=True, status=200)
    store.record_tokens(coverage, result)
    return result


def log(monitor, ref, **updates):
    values = dict(
        model="claude-sonnet-4-6",
        input_tokens=33,
        output_tokens=7,
        cache_read_tokens=20,
        cache_creation_tokens=3,
        cost=0.001,
        latency_ms=1,
        status_code=200,
        endpoint="/v1/messages",
        session_id="session-a",
        agent_id="agent-a",
        provider_input_tokens=10,
        provider_output_tokens=7,
        provider_cache_read_tokens=20,
        provider_cache_creation_tokens=3,
        provider_usage_source="provider_usage_object",
        provider_usage_provider="anthropic",
        reservation_ref=ref,
        guard_token_usage_complete=True,
    )
    values.update(updates)
    monitor.log(**values)
    assert monitor.flush(timeout=3)


def snapshot(store):
    return store.token_snapshot("session-a", 3600, owner_instance_id="owner-a")


def test_real_token_commit_settles_without_creating_monetary_evidence(domain):
    store, monitor = domain
    coverage, ref, _ = begin(store)
    observed(store, coverage, ref)
    log(monitor, ref)
    store.finish_request(coverage)
    result = snapshot(store)
    assert result["token_coverage_complete"]
    assert result["token_observation"]["available"]
    assert result["recorded_token_usage"]["fleet_tokens_total"] == 40
    assert result["pending_projected_token_usage"]["fleet_tokens_total"] == 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT status, actual_cost_usd, actual_tokens FROM budget_reservations"
        ).fetchone() == ("settled", None, 40)
    with sqlite3.connect(monitor.db_path) as conn:
        assert conn.execute(
            "SELECT guard_usage_complete, guard_token_usage_complete, guard_price_json FROM requests"
        ).fetchone() == (0, 1, None)
    for forbidden in ("private", "cost_usd", "agent-a", str(monitor.db_path)):
        if forbidden == "cost_usd":
            continue  # the observation explicitly carries actual_cost_usd=null
        assert forbidden not in json.dumps(result)
    assert result["token_observation"]["observation"]["actual_cost_usd"] is None
    with pytest.raises(ReservationUnavailable):
        ReservationStore(store.path, monitor.db_path).snapshot("session-a", 3600)


def test_commit_before_notification_removes_overlap_and_settles_late(domain, monkeypatch):
    store, monitor = domain
    coverage, ref, _ = begin(store)
    observed(store, coverage, ref)
    monkeypatch.setattr(monitor_module, "_notify_durable_guard_commit", lambda *a: None)
    log(monitor, ref)
    store.finish_request(coverage)
    result = snapshot(store)
    assert result["token_coverage_complete"]
    assert result["pending_projected_token_usage"]["fleet_tokens_total"] == 0
    assert store.settle_token_after_commit(ref)
    assert not store.settle_token_after_commit(ref)


def test_failed_or_mismatched_usage_retains_unresolved_hold(domain):
    store, monitor = domain
    coverage, ref, _ = begin(store)
    observed(store, coverage, ref)
    with pytest.raises(ReservationUnavailable, match="matching committed"):
        store.settle_token_after_commit(ref)
    log(monitor, ref, provider_input_tokens=11)
    store.finish_request(coverage)
    with pytest.raises(ReservationUnavailable, match="counts differ"):
        snapshot(store)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT status, actual_cost_usd FROM budget_reservations"
        ).fetchone() == ("active", None)


def test_missing_completion_flag_never_turns_into_legacy_price_coverage(domain):
    store, monitor = domain
    coverage, ref, _ = begin(store)
    observed(store, coverage, ref)
    log(monitor, ref, guard_token_usage_complete=False)
    store.finish_request(coverage)
    with pytest.raises(ReservationUnavailable, match="coverage is incomplete"):
        snapshot(store)
    with pytest.raises(ValueError, match="priced usage"):
        log(monitor, ref, guard_usage_complete=True)


def test_basis_is_permanently_bound_without_reclassifying_existing_rows(domain):
    store, monitor = domain
    store.begin_request("session-a", "owner-a")
    with pytest.raises(ReservationUnavailable, match="basis differs"):
        ReservationStore(store.path, monitor.db_path).begin_request("session-b", "owner-b")


def test_token_mode_refuses_force_and_monetary_caps_before_reserving(domain):
    store, _ = domain
    for updates in (
        {"per_fleet_max_cost_usd": 1},
        {"per_agent_max_cost_usd": 1},
        {"per_fleet_max_tokens_total": 0},
    ):
        with pytest.raises(ReservationUnavailable):
            begin(store, policy=caps(**updates))
    with pytest.raises(ReservationUnavailable):
        store.reserve(
            session_id="s",
            agent_id="",
            owner_instance_id="o",
            request_id="r",
            projection=Projection(1, 1, 1, 1),
            caps=caps(),
            force=True,
        )
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 0


def test_joint_token_admission_is_serialized(domain):
    store, _ = domain
    seed = store.begin_request("seed", "owner-a")
    store.finish_request(seed)

    # Isolate cap serialization from additional coverage-creation writes.
    # The HTTP fixture separately exercises coverage plus admission together.
    def admit(index):
        return store.reserve(
            session_id="session-a",
            agent_id="agent-a",
            owner_instance_id="owner-a",
            request_id=f"r-{index}",
            projection=Projection(1, 33, 7, 33),
            caps=caps(),
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(admit, range(4)))
    assert sum(ref is not None for ref, _ in results) == 2
    assert sum(breach is not None for _, breach in results) == 2
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM budget_reservations").fetchone()[0] == 2


def test_concurrent_first_coverage_and_admission_never_undercounts(domain):
    store, _ = domain

    def attempt(index):
        try:
            _, ref, breach = begin(store, request_id=f"first-{index}")
            assert (ref is None) != (breach is None)
            return ref
        except sqlite3.OperationalError as exc:
            # First-use migration and coverage can exhaust the bounded writer
            # wait. The request entrypoint must convert this into a refusal.
            assert "locked" in str(exc) or "busy" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(attempt, range(4)))
    admitted = [ref for ref in refs if ref is not None]
    assert len(admitted) <= 2
    with sqlite3.connect(store.path) as conn:
        rows = conn.execute(
            "SELECT reservation_id, reserved_input_tokens + reserved_output_tokens "
            "FROM budget_reservations"
        ).fetchall()
    assert {row[0] for row in rows} == {ref.reservation_id for ref in admitted}
    assert sum(row[1] for row in rows) <= 100


def test_snapshot_read_does_not_migrate_old_or_missing_store(domain):
    store, monitor = domain
    assert not store.path.exists()
    with pytest.raises(FileNotFoundError):
        snapshot(store)
    assert not store.path.exists()
    with sqlite3.connect(monitor.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0


def token_config(**updates):
    values = dict(
        enabled=True,
        reservations_enabled=True,
        accounting_basis="provider_tokens",
        rolling_caps_per_agent_max_cost_usd=0,
        rolling_caps_per_fleet_max_cost_usd=0,
    )
    values.update(updates)
    return load_config(raw_config={"tip_spend_guard": values})


def test_config_default_and_explicit_token_mode_are_separate(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("TOKENPAK_SPEND_GUARD_"):
            monkeypatch.delenv(name)
    assert load_config(raw_config={}).accounting_basis == "priced_usage"
    assert load_config(raw_config={}).reservations_enabled is False
    assert token_config().accounting_basis == "provider_tokens"
    for overrides in (
        {"rolling_caps_per_fleet_max_cost_usd": 1},
        {"rolling_caps_enabled": False},
        {"reservations_enabled": False},
        {"rolling_caps_per_fleet_max_tokens_total": 0},
        {"accounting_basis": "unknown"},
    ):
        with pytest.raises(ValueError):
            token_config(**overrides)


def test_sent_token_hold_cannot_be_released_as_unsent(domain):
    store, _ = domain
    coverage, ref, _ = begin(store)
    observed(store, coverage, ref)
    with pytest.raises(ReservationUnavailable, match="cannot be released"):
        store.release_unsent(ref)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT status, actual_cost_usd FROM budget_reservations"
        ).fetchone() == ("active", None)
