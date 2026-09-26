"""Unit tests for the durable write-ahead execution-plan ledger.

Covers the atomicity/status-transition semantics in isolation (independent
of the chaos-level restart scenario in ``tests/chaos/test_proxy_restart_midstream.py``).
"""

from __future__ import annotations

import threading

import pytest

from tokenpak.proxy import execution_ledger as el


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    db_path = tmp_path / "execution_ledger.db"
    monkeypatch.setenv("TOKENPAK_EXECUTION_LEDGER_DB", str(db_path))
    monkeypatch.setenv("TOKENPAK_EXECUTION_LEDGER_ENABLED", "1")
    el._SCHEMA_READY.clear()
    yield db_path
    el._SCHEMA_READY.clear()


def test_begin_plan_creates_in_flight_row():
    el.begin_plan(
        "plan-1",
        request_id="req-1",
        request_hash="abc123",
        target_url="https://api.anthropic.com/v1/messages",
        stream_started=False,
    )
    row = el.lookup_plan("plan-1")
    assert row is not None
    assert row["status"] == "in_flight"
    assert row["request_id"] == "req-1"
    assert row["request_hash"] == "abc123"
    assert row["stream_started"] == 0


def test_complete_plan_transitions_from_in_flight():
    el.begin_plan("plan-2", request_id="req-2")
    el.complete_plan("plan-2")
    row = el.lookup_plan("plan-2")
    assert row["status"] == "completed"


def test_fail_plan_transitions_from_in_flight():
    el.begin_plan("plan-3", request_id="req-3")
    el.fail_plan("plan-3", reason="upstream_5xx_exhausted")
    row = el.lookup_plan("plan-3")
    assert row["status"] == "failed"
    assert row["failure_reason"] == "upstream_5xx_exhausted"


def test_fail_plan_is_a_noop_once_already_completed():
    """The WHERE status='in_flight' guard: fail_plan must not clobber a
    success recorded earlier by complete_plan — this is what makes the
    defensive catch-all fail_plan() call in server.py's finally-block safe.
    """
    el.begin_plan("plan-4", request_id="req-4")
    el.complete_plan("plan-4")
    el.fail_plan("plan-4", reason="handler_exit_unrecorded")
    row = el.lookup_plan("plan-4")
    assert row["status"] == "completed"
    assert row["failure_reason"] == ""


def test_complete_plan_is_a_noop_once_already_failed():
    el.begin_plan("plan-5", request_id="req-5")
    el.fail_plan("plan-5", reason="non_retryable_http_400")
    el.complete_plan("plan-5")
    row = el.lookup_plan("plan-5")
    assert row["status"] == "failed"


def test_begin_plan_on_unknown_plan_id_is_a_fresh_row_after_reuse():
    """Reusing a tip_plan_id (e.g. an actual client retry within the same
    process) resets status back to in_flight rather than erroring on the
    PRIMARY KEY collision.
    """
    el.begin_plan("plan-6", request_id="req-6a")
    el.complete_plan("plan-6")
    el.begin_plan("plan-6", request_id="req-6b")
    row = el.lookup_plan("plan-6")
    assert row["status"] == "in_flight"
    assert row["request_id"] == "req-6b"


def test_recover_orphaned_plans_only_touches_in_flight_rows():
    el.begin_plan("plan-orphan", request_id="req-o")
    el.begin_plan("plan-done", request_id="req-d")
    el.complete_plan("plan-done")

    recovered = el.recover_orphaned_plans()
    ids = {r["tip_plan_id"] for r in recovered}
    assert ids == {"plan-orphan"}

    assert el.lookup_plan("plan-orphan")["status"] == "failed"
    assert el.lookup_plan("plan-orphan")["failure_reason"] == el.RESTART_FAILURE_REASON
    assert el.lookup_plan("plan-done")["status"] == "completed"


def test_check_restart_recovered_failure_is_single_delivery():
    el.begin_plan("plan-signal", request_id="req-s")
    el.recover_orphaned_plans()

    first = el.check_restart_recovered_failure("plan-signal")
    assert first is not None
    assert first["tip_plan_id"] == "plan-signal"

    second = el.check_restart_recovered_failure("plan-signal")
    assert second is None


def test_check_restart_recovered_failure_ignores_ordinary_failures():
    """Only failures produced by the restart-recovery pass should trip the
    ingress short-circuit — an ordinary in-process upstream failure (e.g. a
    non-retryable 400) is already handled by upstream_retry.py's existing
    same-process path and must not be double-signaled here.
    """
    el.begin_plan("plan-ordinary-fail", request_id="req-of")
    el.fail_plan("plan-ordinary-fail", reason="non_retryable_http_400")
    assert el.check_restart_recovered_failure("plan-ordinary-fail") is None


def test_hash_request_is_stable_sha256():
    import hashlib

    body = b'{"model": "claude-sonnet-4-5"}'
    assert el.hash_request(body) == hashlib.sha256(body).hexdigest()
    assert el.hash_request(None) == hashlib.sha256(b"").hexdigest()


def test_concurrent_begin_complete_does_not_corrupt_status():
    """Threaded smoke test — many concurrent plans, each begun and completed
    on its own thread. Every row should land 'completed', none 'in_flight'.
    """

    def worker(i: int) -> None:
        plan_id = f"plan-concurrent-{i}"
        el.begin_plan(plan_id, request_id=f"req-{i}")
        el.complete_plan(plan_id)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    for i in range(16):
        row = el.lookup_plan(f"plan-concurrent-{i}")
        assert row is not None
        assert row["status"] == "completed"


def test_disabled_flag_short_circuits_all_writes(monkeypatch):
    monkeypatch.setenv("TOKENPAK_EXECUTION_LEDGER_ENABLED", "0")
    el.begin_plan("plan-disabled", request_id="req-disabled")
    assert el.lookup_plan("plan-disabled") is None
    assert el.recover_orphaned_plans() == []
