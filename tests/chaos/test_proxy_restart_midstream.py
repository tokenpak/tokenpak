"""Chaos test: proxy restart mid-stream leaves a durable, signal-bearing trail.

Scenario under test: the TokenPak proxy process dies while an upstream call
is in flight (OOM-kill, redeploy, ``systemctl restart`` — anything that ends
the process without giving the in-flight request handler a chance to run its
normal completion/failure path). Before this fix, the durable ledger simply
did not exist: the only record of the in-flight call
(``tokenpak.proxy.inflight_registry``) is an in-memory dict that dies with
the process, so a fresh process has no way to distinguish "a request was
interrupted by my predecessor's death" from "nothing was happening" — the
client just sees a bare connection reset.

This test simulates that sequence at the ``execution_ledger`` module level
(the durability boundary the fix actually adds), without spawning a real
proxy subprocess:

  1. "Process A" begins a plan (write-ahead — this is the call the real
     request handler makes *before* dispatching the upstream call) and then
     is simulated to die: no matching ``complete_plan``/``fail_plan`` ever
     runs, and the schema-ready cache is cleared to emulate a fresh
     interpreter with no in-memory state.
  2. "Process B" starts against the same on-disk DB file and runs the
     startup recovery pass, exactly as ``ProxyServer.start()`` does before
     its listener accepts a connection.
  3. Assertions: the orphaned plan is detected and reported by the recovery
     pass within the ≤5s wall-clock bound; the row is durably marked
     ``failed`` / ``proxy_restart_detected``; and the client-facing ingress
     check (``check_restart_recovered_failure``) returns a record shaped so
     the handler can build an explicit ``TIPError``/``recovery_status``
     signal — never a bare reset.

This is intentionally a fail-with-signal test, not a replay test: full
transparent re-execution of a provider call interrupted mid-stream is out of
scope for this fix (see ``execution_ledger.py`` module docstring), so no
assertion here claims the original request was ever replayed.
"""

from __future__ import annotations

import time

import pytest

from tokenpak.proxy import execution_ledger as el
from tokenpak.proxy.upstream_retry import build_terminal_recovery_payload

RECOVERY_BOUND_SECONDS = 5.0


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """Point the ledger at a scratch DB and forget any cached schema state.

    Mirrors process isolation: each test gets a DB path no other test has
    touched, and the module-level "schema already ensured for this path"
    cache is cleared so this looks like a cold process to the ledger code.
    """
    db_path = tmp_path / "execution_ledger.db"
    monkeypatch.setenv("TOKENPAK_EXECUTION_LEDGER_DB", str(db_path))
    monkeypatch.setenv("TOKENPAK_EXECUTION_LEDGER_ENABLED", "1")
    el._SCHEMA_READY.clear()
    yield db_path
    el._SCHEMA_READY.clear()


def _simulate_process_death() -> None:
    """Emulate a fresh interpreter: drop any in-memory (non-durable) state.

    The only in-memory state execution_ledger keeps is the schema-ready
    cache (a pure optimization — dropping it just means the next call
    re-runs an idempotent CREATE TABLE IF NOT EXISTS). Everything that
    matters for recovery already lives on disk.
    """
    el._SCHEMA_READY.clear()


@pytest.mark.chaos
class TestProxyRestartMidstream:
    """Standard 28 Scenario 1: proxy restart mid-stream."""

    def test_orphaned_plan_detected_and_marked_failed_on_restart(self):
        tip_plan_id = "tip-plan-chaos-restart-1"

        # ---- Process A: begin a plan, then die before it ever resolves ----
        el.begin_plan(
            tip_plan_id,
            request_id="req-chaos-1",
            request_hash=el.hash_request(b'{"model":"claude-sonnet-4-5"}'),
            target_url="https://api.anthropic.com/v1/messages",
            stream_started=True,
        )

        mid_death = el.lookup_plan(tip_plan_id)
        assert mid_death is not None
        assert mid_death["status"] == "in_flight"

        _simulate_process_death()

        # ---- Process B: fresh startup, same DB file ------------------------
        t0 = time.monotonic()
        recovered = el.recover_orphaned_plans()
        elapsed = time.monotonic() - t0

        assert elapsed <= RECOVERY_BOUND_SECONDS, (
            f"recovery pass took {elapsed:.3f}s, exceeding the "
            f"{RECOVERY_BOUND_SECONDS}s decision bound"
        )
        assert len(recovered) == 1
        assert recovered[0]["tip_plan_id"] == tip_plan_id
        assert recovered[0]["stream_started"] is True

        after_recovery = el.lookup_plan(tip_plan_id)
        assert after_recovery is not None
        assert after_recovery["status"] == "failed"
        assert after_recovery["failure_reason"] == el.RESTART_FAILURE_REASON

    def test_client_retry_gets_explicit_signal_not_bare_reset(self):
        """The retry-with-same-plan-id path produces a TIPError-shaped payload.

        This is the fail-with-signal assertion, deliberately not a replay
        assertion: the original upstream call is never re-issued.
        """
        tip_plan_id = "tip-plan-chaos-restart-2"
        el.begin_plan(
            tip_plan_id,
            request_id="req-chaos-2",
            request_hash=el.hash_request(b"{}"),
            target_url="https://api.anthropic.com/v1/messages",
            stream_started=False,
        )
        _simulate_process_death()
        recovered = el.recover_orphaned_plans()
        assert len(recovered) == 1

        # Client retries with the same tip_plan_id — this is the exact check
        # tokenpak/proxy/server.py performs at request ingress.
        record = el.check_restart_recovered_failure(tip_plan_id)
        assert record is not None
        assert record["tip_plan_id"] == tip_plan_id

        payload = build_terminal_recovery_payload(
            request_id="req-chaos-2-retry",
            tip_plan_id=tip_plan_id,
            error_type="proxy_restart_detected",
            message="TokenPak proxy restarted while this request was in flight.",
            stream_started=bool(record["stream_started"]),
        )
        error = payload["error"]
        assert error["recovery_status"] == "terminally_failed"
        assert error["retryable"] is False
        assert error["tip_plan_id"] == tip_plan_id

        # The signal is delivered exactly once per orphaned plan: a second
        # retry with the same plan id must not keep re-triggering it forever
        # (the row is marked acknowledged on first delivery).
        assert el.check_restart_recovered_failure(tip_plan_id) is None

    def test_recovery_pass_does_not_disturb_genuinely_completed_plans(self):
        """A plan that completed normally before the restart is left alone.

        Guards against a recovery pass that is too eager: only rows still
        `in_flight` at startup are restart casualties.
        """
        completed_id = "tip-plan-chaos-completed"
        el.begin_plan(completed_id, request_id="req-chaos-3")
        el.complete_plan(completed_id)

        orphan_id = "tip-plan-chaos-orphan"
        el.begin_plan(orphan_id, request_id="req-chaos-4")

        _simulate_process_death()
        recovered = el.recover_orphaned_plans()

        recovered_ids = {r["tip_plan_id"] for r in recovered}
        assert orphan_id in recovered_ids
        assert completed_id not in recovered_ids

        still_completed = el.lookup_plan(completed_id)
        assert still_completed["status"] == "completed"

    def test_recovery_pass_is_a_safe_noop_with_nothing_in_flight(self):
        """An ordinary restart with no interrupted requests recovers nothing."""
        recovered = el.recover_orphaned_plans()
        assert recovered == []

    def test_ledger_failure_never_raises_fail_open(self, tmp_path, monkeypatch):
        """A ledger DB error must never surface as an exception to the caller.

        Simulates a broken DB path — a path component that is a plain file,
        not a directory, so ``Path.mkdir(parents=True)`` reliably raises
        ``NotADirectoryError`` regardless of the test runner's privileges
        (unlike a permission-denied path, which root would sail through).
        begin/complete/fail/recover/check must all fail open (swallow and
        return a harmless default), matching the codebase-wide "DB errors
        never break the proxy" convention (see spend_guard.pending,
        monitor.py).
        """
        blocker = tmp_path / "not_a_directory"
        blocker.write_text("this is a file, not a directory")
        monkeypatch.setenv(
            "TOKENPAK_EXECUTION_LEDGER_DB",
            str(blocker / "sub" / "execution_ledger.db"),
        )
        el._SCHEMA_READY.clear()

        # None of these should raise.
        el.begin_plan("tip-plan-broken", request_id="req-broken")
        el.complete_plan("tip-plan-broken")
        el.fail_plan("tip-plan-broken", reason="whatever")
        assert el.recover_orphaned_plans() == []
        assert el.check_restart_recovered_failure("tip-plan-broken") is None
        assert el.lookup_plan("tip-plan-broken") is None
