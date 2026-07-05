"""Dispatch idempotency + crash-recovery contract tests.

Covers the pre-wiring durability fixes for station execution:

  * a RUNNING station-run intent row is persisted BEFORE station work begins,
    so a crash mid-station is classifiable by resume reconciliation (instead of
    the reconciler seeing only the prior COMPLETED station and blindly
    re-running);
  * resume threads the reconciliation's rerun attempt number through, so a
    retried station runs as attempt N+1 rather than restarting at attempt 1;
  * the effect-record lifecycle (planned → applied/failed) is persisted through
    the Run Ledger by the effect-bearing tools when a ledger is supplied;
  * ``apply_patch`` writes files atomically — an injected crash mid-write
    leaves the target as its exact before-image or after-image, never a
    truncated partial file;
  * terminal-state enforcement — run()/resume() refuse a run already in a
    terminal status, and a finished run is never re-finalized;
  * the run-level execution lease — two concurrent resume() calls on the same
    run cannot interleave; exactly one executes and the other exits with a
    clear error;
  * receipts are 1:1 with runs — rebuilding a receipt upserts the same row
    instead of minting a new id and orphaning the old row;
  * ``dispatch run --dry-run`` is write-free (the on-disk ledger stays
    byte-identical; on a fresh home it is not even created).

All tests are deterministic and mocked (no real provider, no network).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
from datetime import datetime, timezone

import pytest

# Dispatch is pydantic-native; the dep ships via the opt-in `dispatch` extra.
pytest.importorskip("pydantic")

import importlib  # noqa: E402

# The tools package re-exports the apply_patch FUNCTION under the same name as
# its module, so fetch the module object explicitly (for monkeypatching its
# internals).
apply_patch_tool = importlib.import_module(
    "tokenpak.orchestration.dispatch.tools.apply_patch"
)

from tokenpak.orchestration.dispatch.context.provider import LocalContextProvider  # noqa: E402
from tokenpak.orchestration.dispatch.dispatch import DispatchRuntime  # noqa: E402
from tokenpak.orchestration.dispatch.frontdock import FrontDock  # noqa: E402
from tokenpak.orchestration.dispatch.ledger.db import RunLedger  # noqa: E402
from tokenpak.orchestration.dispatch.models.common import PathPolicy  # noqa: E402
from tokenpak.orchestration.dispatch.models.enums import (  # noqa: E402
    AutonomyMode,
    EffectStatus,
    EffectTargetType,
    StationRunStatus,
)
from tokenpak.orchestration.dispatch.models.run import DispatchRun  # noqa: E402
from tokenpak.orchestration.dispatch.models.station_run import DispatchStationRun  # noqa: E402
from tokenpak.orchestration.dispatch.receipt_builder import (  # noqa: E402
    build_and_write_receipt,
    receipt_id_for_run,
)
from tokenpak.orchestration.dispatch.registry.workers import default_worker_registry  # noqa: E402
from tokenpak.orchestration.dispatch.resume import (  # noqa: E402
    ResumeAction,
    hash_workspace_file,
    reconcile_run,
)
from tokenpak.orchestration.dispatch.runner import (  # noqa: E402
    TERMINAL_RUN_STATUSES,
    FulfillmentLine,
    LineStatus,
    RunAlreadyTerminalError,
    RunLeaseHeldError,
)
from tokenpak.orchestration.dispatch.station_runner import (  # noqa: E402
    WorkerToolRequest,
    WorkerTurn,
)
from tokenpak.orchestration.dispatch.tools.apply_patch import apply_patch  # noqa: E402
from tokenpak.orchestration.dispatch.tools.run_command import run_command  # noqa: E402

_NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Deterministic fakes
# ---------------------------------------------------------------------------


class FakeWorkerLLM:
    """Worker boundary mock: replays a scripted list of WorkerTurns by iteration."""

    def __init__(self, turns):
        self._turns = turns
        self.calls = 0

    def run_turn(self, *, prompt, context, prior_tool_outputs, iteration):
        self.calls += 1
        idx = min(iteration - 1, len(self._turns) - 1)
        turn = self._turns[idx]
        if isinstance(turn, Exception):
            raise turn  # simulated hard interruption mid-turn
        return turn


class FakeReviewerLLM:
    """Reviewer boundary mock: returns a canned 'pass' payload."""

    def __call__(self, prompt: str) -> str:
        return json.dumps(
            {
                "status": "pass",
                "criteria_results": [],
                "required_fixes": [],
                "risk_flags": [],
                "delivery_recommendation": {"status": "ready", "reason": "ok"},
            }
        )


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ledger(home):
    led = RunLedger()
    try:
        yield led
    finally:
        led.close()


def _code_task_intake():
    fd = FrontDock()
    return fd.intake(
        "implement a code fix in the parser module",
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        now=_NOW,
    )


def _select_code_task(intake):
    runtime = DispatchRuntime()
    outcome = runtime.select_route(intake, now=_NOW)
    assert outcome.route is not None
    return outcome.route


def _line(ledger, worker, **over):
    kwargs = dict(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=FakeReviewerLLM(),
        clock=lambda: _NOW,
    )
    kwargs.update(over)
    return FulfillmentLine(**kwargs)


# ---------------------------------------------------------------------------
# 1) RUNNING intent row — crash mid-station is durable + classifiable
# ---------------------------------------------------------------------------


def test_crash_mid_station_leaves_running_row(ledger):
    """A crash during the worker turn leaves a durable RUNNING station-run row."""

    intake = _code_task_intake()
    route = _select_code_task(intake)

    line = _line(ledger, FakeWorkerLLM([RuntimeError("simulated process death")]))
    with pytest.raises(RuntimeError, match="simulated process death"):
        line.run(
            route=route,
            manifest=intake.manifest,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            run_id="run_crash",
        )

    persisted = ledger.read_station_runs_for_run("run_crash")
    assert len(persisted) == 1
    interrupted = persisted[0]
    assert interrupted.status is StationRunStatus.RUNNING
    assert interrupted.attempt_number == 1

    # Reconciliation now CLASSIFIES the interruption (running + no effects →
    # rerun with attempt+1) instead of continuing past a phantom completion.
    outcome = reconcile_run(
        station_runs=persisted,
        effects_for_last_station=ledger.read_effects_for_station_run(interrupted.id),
        workspace_root=".",
        now=_NOW,
    )
    assert outcome.action is ResumeAction.RERUN_STATION
    assert outcome.rerun_attempt_number == 2


def test_completed_station_row_replaces_running_row(ledger):
    """A station that finishes normally ends with ONE terminal row (same id)."""

    intake = _code_task_intake()
    route = _select_code_task(intake)

    line = _line(
        ledger,
        FakeWorkerLLM([WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)]),
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )
    assert result.status is LineStatus.DELIVERED
    persisted = ledger.read_station_runs_for_run(result.run.id)
    build_rows = [sr for sr in persisted if sr.station_id == "build"]
    assert len(build_rows) == 1  # the intent row was rewritten, not duplicated
    assert build_rows[0].status is StationRunStatus.COMPLETED


# ---------------------------------------------------------------------------
# 2) Crash → resume reruns the station at attempt 2 (attempt threading)
# ---------------------------------------------------------------------------


def test_crash_then_resume_reruns_at_attempt_2(ledger, tmp_path):
    intake = _code_task_intake()
    route = _select_code_task(intake)

    crashing = _line(ledger, FakeWorkerLLM([RuntimeError("simulated process death")]))
    with pytest.raises(RuntimeError):
        crashing.run(
            route=route,
            manifest=intake.manifest,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            run_id="run_rerun",
        )

    healthy = _line(
        ledger,
        FakeWorkerLLM([WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)]),
    )
    result = healthy.resume(
        run_id="run_rerun",
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        workspace_root=str(tmp_path),
    )
    assert result.status is LineStatus.DELIVERED

    persisted = ledger.read_station_runs_for_run("run_rerun")
    by_status = {(sr.station_id, sr.status): sr for sr in persisted}
    # The interrupted attempt transitioned to failed_interrupted…
    interrupted = by_status[("build", StationRunStatus.FAILED_INTERRUPTED)]
    assert interrupted.attempt_number == 1
    # …and the rerun executed as attempt 2 (NOT restarting at attempt 1).
    rerun = by_status[("build", StationRunStatus.COMPLETED)]
    assert rerun.attempt_number == 2
    assert rerun.id != interrupted.id


# ---------------------------------------------------------------------------
# 3) Durable effects + crash → resume sees them and reconciles
# ---------------------------------------------------------------------------


def test_crash_after_applied_effect_resume_reconciles_and_continues(ledger, tmp_path):
    """An apply_patch effect persisted mid-station is visible to resume."""

    intake = _code_task_intake()
    route = _select_code_task(intake)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = PathPolicy(allowed_paths=["src/**"])

    def tool_runner(request: WorkerToolRequest):
        # The RUNNING intent row is durable BEFORE tool execution, so the tool
        # can attribute its effect to the live station run.
        row = ledger._conn.execute(
            "SELECT id, run_id FROM dispatch_station_runs WHERE status = 'running'"
        ).fetchone()
        assert row is not None, "RUNNING intent row must exist during tool execution"
        return apply_patch(
            relative_path="src/a.py",
            content="patched content",
            path_policy=policy,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            job_id=intake.manifest.job_id,
            station_run_id=row["id"],
            workspace_root=workspace,
            ledger=ledger,
        )

    # Turn 1 applies the patch; turn 2 simulates a process death.
    crashing = _line(
        ledger,
        FakeWorkerLLM(
            [
                WorkerTurn(tool_requests=(WorkerToolRequest(tool="apply_patch"),)),
                RuntimeError("simulated process death"),
            ]
        ),
        tool_runner=tool_runner,
    )
    with pytest.raises(RuntimeError):
        crashing.run(
            route=route,
            manifest=intake.manifest,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            run_id="run_effects",
        )

    persisted = ledger.read_station_runs_for_run("run_effects")
    assert persisted[-1].status is StationRunStatus.RUNNING
    effects = ledger.read_effects_for_station_run(persisted[-1].id)
    assert [e.status for e in effects] == [EffectStatus.APPLIED]
    assert effects[0].after_hash == hash_workspace_file(workspace, "src/a.py")

    # Resume: running + applied effect that matches after_hash → workspace is
    # consistent → continue to the next station (review) → delivered.
    healthy = _line(
        ledger,
        FakeWorkerLLM([WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)]),
    )
    result = healthy.resume(
        run_id="run_effects",
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        workspace_root=str(workspace),
    )
    assert result.status is LineStatus.DELIVERED
    reconciled = ledger.read_station_runs_for_run("run_effects")
    statuses = {sr.status for sr in reconciled if sr.station_id == "build"}
    assert StationRunStatus.NEEDS_RECOVERY in statuses


# ---------------------------------------------------------------------------
# 4) Terminal-state enforcement
# ---------------------------------------------------------------------------


def _delivered_run(ledger):
    intake = _code_task_intake()
    route = _select_code_task(intake)
    line = _line(
        ledger,
        FakeWorkerLLM([WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)]),
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )
    assert result.status is LineStatus.DELIVERED
    return intake, route, result


def test_resume_on_delivered_run_refuses(ledger, tmp_path):
    intake, route, result = _delivered_run(ledger)
    line = _line(
        ledger,
        FakeWorkerLLM([WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)]),
    )
    with pytest.raises(RunAlreadyTerminalError) as excinfo:
        line.resume(
            run_id=result.run.id,
            route=route,
            manifest=intake.manifest,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            workspace_root=str(tmp_path),
        )
    assert result.run.id in str(excinfo.value)
    assert "delivered" in str(excinfo.value)


def test_run_with_terminal_run_id_refuses(ledger):
    intake, route, result = _delivered_run(ledger)
    line = _line(
        ledger,
        FakeWorkerLLM([WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)]),
    )
    with pytest.raises(RunAlreadyTerminalError):
        line.run(
            route=route,
            manifest=intake.manifest,
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            run_id=result.run.id,
        )


def test_finalize_is_idempotent_on_terminal_run(ledger):
    """Re-finalizing a delivered run is a no-op (status + receipt link stable)."""

    intake, route, result = _delivered_run(ledger)
    line = _line(ledger, FakeWorkerLLM([WorkerTurn()]))

    before = ledger.read_run(result.run.id)
    assert before.status == "delivered"
    finalized = line._finalize_run(before, status="cancelled")
    assert finalized.status == "delivered"  # unchanged — no re-finalize
    after = ledger.read_run(result.run.id)
    assert after.status == "delivered"
    assert after.ended_at == before.ended_at
    assert after.receipt_id == before.receipt_id


def test_terminal_status_set_matches_job_state_machine():
    assert TERMINAL_RUN_STATUSES == {"delivered", "cancelled", "failed", "withdrawn"}


# ---------------------------------------------------------------------------
# 5) Run-level lease — concurrent resume: exactly one executes
# ---------------------------------------------------------------------------


def _seed_interrupted_run(ledger, intake, route):
    run = DispatchRun(
        id="run_lease",
        job_id=intake.manifest.job_id,
        manifest_id=intake.manifest.id,
        route_id=route.id,
        started_at=_NOW,
        status="running",
    )
    ledger.write_run(run)
    ledger.write_station_run(
        DispatchStationRun(
            id="stationrun_interrupted",
            run_id="run_lease",
            station_id="build",
            worker_id="worker.builder.default.v1",
            context_bundle_id="ctx",
            status=StationRunStatus.RUNNING,
            result_schema_version="station_result.v1",
        )
    )


def test_concurrent_resume_exactly_one_executes(home, tmp_path):
    """Two concurrent resume() calls: one walks the run, the other exits with a
    clear lease-held error. Deterministic: the winner blocks inside its first
    worker turn until the loser has been refused."""

    intake = _code_task_intake()
    route = _select_code_task(intake)
    setup = RunLedger()
    try:
        _seed_interrupted_run(setup, intake, route)
    finally:
        setup.close()

    entered = threading.Event()
    release = threading.Event()

    class BlockingWorker:
        def run_turn(self, *, prompt, context, prior_tool_outputs, iteration):
            entered.set()  # lease is held; let the second caller try now
            assert release.wait(timeout=10), "second caller never finished"
            return WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)

    results: dict[str, object] = {}

    def winner():
        led = RunLedger()  # own connection: SQLite handles are thread-bound
        try:
            results["winner"] = _line(led, BlockingWorker()).resume(
                run_id="run_lease",
                route=route,
                manifest=intake.manifest,
                autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
                workspace_root=str(tmp_path),
            )
        except Exception as exc:  # pragma: no cover - failure diagnostics
            results["winner"] = exc
        finally:
            led.close()

    def loser():
        assert entered.wait(timeout=10), "winner never claimed the lease"
        led = RunLedger()
        try:
            results["loser"] = _line(
                led,
                FakeWorkerLLM(
                    [WorkerTurn(result_payload={"ok": 1}, output_schema_valid=True)]
                ),
            ).resume(
                run_id="run_lease",
                route=route,
                manifest=intake.manifest,
                autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
                workspace_root=str(tmp_path),
            )
        except Exception as exc:
            results["loser"] = exc
        finally:
            release.set()  # let the winner finish
            led.close()

    t1 = threading.Thread(target=winner)
    t2 = threading.Thread(target=loser)
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert isinstance(results["loser"], RunLeaseHeldError)
    assert not isinstance(results["winner"], Exception), results["winner"]
    assert results["winner"].status is LineStatus.DELIVERED

    # The lease was released after the walk: a later resume is not lease-blocked
    # (it now refuses on TERMINAL status instead — the run was delivered).
    led = RunLedger()
    try:
        assert led.read_run_lease("run_lease") is None
    finally:
        led.close()


def test_lease_claim_is_exclusive_and_releasable(ledger):
    assert ledger.try_claim_run_lease("run_x", "owner_a") is True
    assert ledger.try_claim_run_lease("run_x", "owner_b") is False
    assert ledger.try_claim_run_lease("run_x", "owner_a") is True  # re-entrant
    assert ledger.release_run_lease("run_x", "owner_b") is False  # not the holder
    assert ledger.release_run_lease("run_x", "owner_a") is True
    assert ledger.try_claim_run_lease("run_x", "owner_b") is True


def test_stale_lease_is_reclaimable(ledger):
    """A lease left behind by a crashed holder does not brick resume forever."""

    stale_time = datetime(2026, 7, 2, 0, 0, 0, tzinfo=timezone.utc)
    assert ledger.try_claim_run_lease("run_y", "crashed_owner", now=stale_time)
    fresh_time = datetime(2026, 7, 2, 6, 0, 0, tzinfo=timezone.utc)  # 6h later
    assert ledger.try_claim_run_lease("run_y", "new_owner", now=fresh_time) is True
    assert ledger.read_run_lease("run_y")["owner"] == "new_owner"


# ---------------------------------------------------------------------------
# 6) Receipts are 1:1 with runs
# ---------------------------------------------------------------------------


def test_receipt_id_is_deterministic_from_run_id():
    assert receipt_id_for_run("run_abc123") == "receipt_abc123"
    assert receipt_id_for_run("weird-id") == "receipt_weird-id"


def test_double_receipt_build_upserts_single_row(ledger):
    _, _, result = _delivered_run(ledger)
    run = ledger.read_run(result.run.id)
    first_receipt_id = run.receipt_id
    assert first_receipt_id == receipt_id_for_run(run.id)

    # Rebuild the receipt (e.g. a retried finalization): same id, same row.
    rebuilt = build_and_write_receipt(
        run=run, ledger=ledger, final_status=run.status, clock=lambda: _NOW
    )
    assert rebuilt.id == first_receipt_id

    rows = ledger._conn.execute(
        "SELECT COUNT(*) AS n FROM dispatch_receipts WHERE run_id = ?",
        (run.id,),
    ).fetchone()
    assert rows["n"] == 1  # no orphaned second receipt
    assert ledger.read_run(run.id).receipt_id == first_receipt_id


# ---------------------------------------------------------------------------
# 7) apply_patch: durable effect lifecycle + atomic (crash-safe) writes
# ---------------------------------------------------------------------------


def _patch_kwargs(workspace, **over):
    kwargs = dict(
        relative_path="src/a.py",
        content="new content",
        path_policy=PathPolicy(allowed_paths=["src/**"]),
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="job_t",
        station_run_id="stationrun_t",
        workspace_root=workspace,
        effect_id="effect_t",
        now=_NOW,
    )
    kwargs.update(over)
    return kwargs


def test_apply_patch_persists_planned_before_write_then_applied(ledger, tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    seen_at_write_time: dict[str, EffectStatus] = {}
    real_write = apply_patch_tool._atomic_write_bytes

    def spying_write(target, data):
        effect = ledger.read_effect("effect_t")
        assert effect is not None, "planned effect must be durable BEFORE the write"
        seen_at_write_time["status"] = effect.status
        real_write(target, data)

    monkeypatch.setattr(apply_patch_tool, "_atomic_write_bytes", spying_write)

    result = apply_patch(**_patch_kwargs(workspace, ledger=ledger))

    assert seen_at_write_time["status"] is EffectStatus.PLANNED
    persisted = ledger.read_effect("effect_t")
    assert persisted.status is EffectStatus.APPLIED
    assert persisted.finalized_at is not None
    assert persisted.after_hash == result.effect.after_hash
    assert persisted.after_hash == hash_workspace_file(workspace, "src/a.py")
    assert persisted.rollback_available is True


@pytest.mark.parametrize("crash_point", ["fsync", "replace"])
def test_apply_patch_crash_leaves_before_image_never_partial(
    ledger, tmp_path, monkeypatch, crash_point
):
    """An injected crash mid-write leaves the target byte-identical to its
    before-image (never truncated / partial), and the durable effect record is
    finalized as failed."""

    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    original = b"original before-image content"
    (workspace / "src" / "a.py").write_bytes(original)
    before_hash = hash_workspace_file(workspace, "src/a.py")

    def boom(*args, **kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(apply_patch_tool.os, crash_point, boom)
    try:
        with pytest.raises(OSError, match="simulated crash"):
            apply_patch(**_patch_kwargs(workspace, ledger=ledger))
    finally:
        monkeypatch.undo()

    # The target is EXACTLY the before-image — matching before_hash, so resume
    # reconciliation classifies it as "not applied, safe to rerun".
    assert (workspace / "src" / "a.py").read_bytes() == original
    assert hash_workspace_file(workspace, "src/a.py") == before_hash
    # No temp-file litter left in the workspace.
    assert list((workspace / "src").glob("*.tmp.*")) == []
    # The durable effect record finalized as failed.
    persisted = ledger.read_effect("effect_t")
    assert persisted.status is EffectStatus.FAILED
    assert persisted.finalized_at is not None


def test_apply_patch_success_replaces_content_atomically(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "a.py").write_bytes(b"old")

    result = apply_patch(**_patch_kwargs(workspace, content="brand new"))
    assert (workspace / "src" / "a.py").read_text() == "brand new"
    assert result.effect.status is EffectStatus.APPLIED
    assert list((workspace / "src").glob("*.tmp.*")) == []


def test_run_command_persists_effect_lifecycle(ledger, tmp_path):
    result = run_command(
        command=[sys.executable, "-c", "print('ok')"],
        category="tests",  # mutating category → effect recorded
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        job_id="job_t",
        station_run_id="stationrun_t",
        cwd=tmp_path,
        timeout_seconds=20,
        effect_id="effect_cmd",
        ledger=ledger,
    )
    assert result.returncode == 0
    persisted = ledger.read_effect("effect_cmd")
    assert persisted is not None
    assert persisted.status is EffectStatus.APPLIED
    assert persisted.finalized_at is not None
    assert persisted.target_type is EffectTargetType.COMMAND_OUTPUT


def test_run_command_launch_failure_finalizes_effect_failed(ledger, tmp_path):
    with pytest.raises(OSError):
        run_command(
            command=["/nonexistent/binary/definitely-not-here"],
            category="tests",
            autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
            job_id="job_t",
            station_run_id="stationrun_t",
            cwd=tmp_path,
            effect_id="effect_cmd_fail",
            ledger=ledger,
        )
    persisted = ledger.read_effect("effect_cmd_fail")
    assert persisted.status is EffectStatus.FAILED
    assert persisted.finalized_at is not None


# ---------------------------------------------------------------------------
# 8) dispatch run --dry-run is write-free
# ---------------------------------------------------------------------------


def _run_args(request, **over):
    base = dict(
        request=request, route=None, autonomy=None, ci=False,
        dry_run=False, confirm=False, as_json=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _invoke_run(args):
    from tokenpak.cli.commands.dispatch_cmd import cmd_dispatch_run

    buf = io.StringIO()
    saved = sys.stdout
    sys.stdout = buf
    try:
        rc = cmd_dispatch_run(args)
    finally:
        sys.stdout = saved
    return int(rc or 0), json.loads(buf.getvalue())


def _ledger_dir_snapshot(home):
    root = home / "dispatch"
    if not root.exists():
        return None
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_dry_run_on_fresh_home_creates_no_ledger(home):
    rc, payload = _invoke_run(_run_args("write a python function", dry_run=True))
    assert rc == 0
    assert payload["dry_run"] is True
    assert payload["autonomy_mode"] == "draft"
    assert "note" in payload
    # Nothing on disk — not even the (empty, migrated) ledger database.
    assert not (home / "dispatch").exists()


def test_dry_run_leaves_existing_ledger_byte_identical(home):
    # Seed a real (persisted) run so the ledger exists with content.
    rc, seeded = _invoke_run(_run_args("write a python function"))
    assert rc == 0
    before = _ledger_dir_snapshot(home)
    assert before  # the seed created the ledger

    rc, payload = _invoke_run(_run_args("write another python function", dry_run=True))
    assert rc == 0
    assert payload["dry_run"] is True

    after = _ledger_dir_snapshot(home)
    assert after == before  # byte-identical: the dry run wrote nothing

    # And the dry run's job is genuinely absent from the ledger.
    led = RunLedger()
    try:
        assert led.read_job(payload["job_id"]) is None
        assert led.read_job(seeded["job_id"]) is not None
    finally:
        led.close()


def test_non_dry_run_still_persists(home):
    rc, payload = _invoke_run(_run_args("write a python function"))
    assert rc == 0
    assert payload["dry_run"] is False
    led = RunLedger()
    try:
        assert led.read_job(payload["job_id"]) is not None
    finally:
        led.close()
