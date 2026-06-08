"""Integration tests for the FulfillmentLine runner + StationRunner (P-EXEC-01).

Covers the §9 acceptance criteria with deterministic, injected TIP/worker mocks
(no real provider):

  * full ``code_task`` golden path (build station completes → reviewer pass →
    delivery ready; Run Ledger records every station run);
  * resume mid-station with applied effects (§5.5 case 3: all-match continue +
    drift surfaces a decision; multi-effect auto-rollback disabled);
  * cancellation mid-station with a late result (§5.6: queued station cancelled,
    LateResult captured with effects_applied=False);
  * Reviewer warning → decision flow (§5.7 handoff table);
  * StationLoopPolicy precedence + stop conditions (§5.4);
  * Spend Guard hard-stop → DispatchDecision (§8).

The Run Ledger is opened against ``tmp_path`` via ``TOKENPAK_HOME`` so the real
``~/.tpk/`` is never touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

# Dispatch is pydantic-native; the dep ships via the opt-in `dispatch` extra.
pytest.importorskip("pydantic")

from tokenpak.orchestration.dispatch.context.provider import LocalContextProvider  # noqa: E402
from tokenpak.orchestration.dispatch.dispatch import DispatchRuntime  # noqa: E402
from tokenpak.orchestration.dispatch.frontdock import FrontDock  # noqa: E402
from tokenpak.orchestration.dispatch.ledger.db import RunLedger  # noqa: E402
from tokenpak.orchestration.dispatch.loop_policy import (  # noqa: E402
    LoopState,
    evaluate_stop,
    resolve_loop_policy,
    system_default_loop_policy,
)
from tokenpak.orchestration.dispatch.models.common import (  # noqa: E402
    StationLoopPolicy,
    WorkerLoopDefault,
)
from tokenpak.orchestration.dispatch.models.effect import DispatchEffect  # noqa: E402
from tokenpak.orchestration.dispatch.models.enums import (  # noqa: E402
    AutonomyMode,
    EffectStatus,
    EffectTargetType,
    LoopStopCondition,
    RollbackBehavior,
    StationRunStatus,
)
from tokenpak.orchestration.dispatch.models.station_run import DispatchStationRun  # noqa: E402
from tokenpak.orchestration.dispatch.registry.workers import default_worker_registry  # noqa: E402
from tokenpak.orchestration.dispatch.resume import (  # noqa: E402
    ResumeAction,
    hash_workspace_file,
    reconcile_run,
)
from tokenpak.orchestration.dispatch.runner import FulfillmentLine, LineStatus  # noqa: E402
from tokenpak.orchestration.dispatch.station_runner import (  # noqa: E402
    SPEND_GUARD_EXCEEDED_REASON,
    FlagCancelToken,
    WorkerToolRequest,
    WorkerTurn,
)

_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Deterministic fakes (no real provider)
# ---------------------------------------------------------------------------


class FakeWorkerLLM:
    """Worker boundary mock: replays a scripted list of WorkerTurns by iteration."""

    def __init__(self, turns: list[WorkerTurn]) -> None:
        self._turns = turns
        self.calls = 0
        self.prompts: list[list[str]] = []

    def run_turn(self, *, prompt, context, prior_tool_outputs, iteration):
        self.prompts.append(prompt)
        self.calls += 1
        # Clamp to the last scripted turn so an over-budget loop keeps getting a
        # deterministic (non-terminal) turn rather than an IndexError.
        idx = min(iteration - 1, len(self._turns) - 1)
        return self._turns[idx]


class FakeReviewerLLM:
    """Reviewer boundary mock: returns a canned reviewer payload JSON string."""

    def __init__(self, status: str, *, reason: str = "ok") -> None:
        recommendation = {
            "pass": "ready",
            "warning": "ready_with_warning",
            "fail": "blocked",
        }[status]
        self._payload = {
            "status": status,
            "criteria_results": [],
            "required_fixes": (
                [{"severity": "medium", "description": "tighten", "suggested_station": "build"}]
                if status != "pass"
                else []
            ),
            "risk_flags": [],
            "delivery_recommendation": {"status": recommendation, "reason": reason},
        }
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        return json.dumps(self._payload)


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
    """A FrontDock intake that deterministically routes to code_task."""

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
    assert outcome.route.id == "route.code_task.v1"
    return outcome.route


# ---------------------------------------------------------------------------
# 1) Full code_task golden path
# ---------------------------------------------------------------------------


def test_code_task_golden_path(ledger):
    intake = _code_task_intake()
    route = _select_code_task(intake)

    worker = FakeWorkerLLM(
        [WorkerTurn(result_payload={"summary": "patch drafted"}, output_schema_valid=True, tokens_used=20)]
    )
    reviewer = FakeReviewerLLM("pass")

    line = FulfillmentLine(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=reviewer,
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )

    assert result.status is LineStatus.DELIVERED
    assert result.run.status == "delivered"
    # Build station completed, reviewer ran exactly once.
    assert [(sr.station_id, sr.status) for sr in result.station_runs] == [
        ("build", StationRunStatus.COMPLETED),
        ("review", StationRunStatus.COMPLETED),
    ]
    assert reviewer.calls == 1
    assert result.delivery_package is not None
    assert result.delivery_package.status.value == "delivery_ready"

    # Run Ledger persisted both station runs + the run (criterion 4: a completed
    # station run carries its schema-valid payload).
    persisted = ledger.read_station_runs_for_run(result.run.id)
    assert {sr.station_id for sr in persisted} == {"build", "review"}
    build = next(sr for sr in persisted if sr.station_id == "build")
    assert build.status is StationRunStatus.COMPLETED
    assert build.result_payload == {"summary": "patch drafted"}
    assert ledger.read_run(result.run.id).status == "delivered"


def test_completed_station_run_committed_only_with_valid_output(ledger):
    """Criterion 4: a station that never produces valid output is not 'completed'."""

    intake = _code_task_intake()
    route = _select_code_task(intake)

    # Worker that never emits a valid payload → loop exhausts → FAILED, payload None.
    worker = FakeWorkerLLM([WorkerTurn(output_schema_valid=False, tokens_used=1)])
    line = FulfillmentLine(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=FakeReviewerLLM("pass"),
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )
    assert result.status is LineStatus.FAILED
    build = result.station_runs[0]
    assert build.status is StationRunStatus.FAILED
    assert build.result_payload is None  # no valid output → no payload committed


# ---------------------------------------------------------------------------
# 2) Resume mid-station with applied effects (§5.5 case 3)
# ---------------------------------------------------------------------------


def _running_station_with_applied_effect(ledger, run_id, workspace, target, content):
    """Persist a RUNNING station with one applied file effect matching after_hash."""

    (workspace / target).parent.mkdir(parents=True, exist_ok=True)
    (workspace / target).write_text(content)
    after = hash_workspace_file(workspace, target)

    sr = DispatchStationRun(
        id="stationrun_build",
        run_id=run_id,
        station_id="build",
        worker_id="worker.builder.default.v1",
        context_bundle_id="ctx",
        status=StationRunStatus.RUNNING,
        result_schema_version="station_result.v1",
    )
    ledger.write_station_run(sr)
    effect = DispatchEffect(
        id="effect_build_1",
        job_id="job_resume",
        station_run_id="stationrun_build",
        tool_name="apply_patch",
        target_type=EffectTargetType.FILE,
        target=target,
        before_exists=False,
        before_hash=None,
        after_hash=after,
        rollback_behavior=RollbackBehavior.DELETE_FILE_IF_AFTER_HASH_MATCHES,
        status=EffectStatus.APPLIED,
        rollback_available=True,
        created_at=_NOW,
        finalized_at=_NOW,
    )
    ledger.write_effect(effect)
    return sr, effect


def test_resume_applied_effects_all_match_continues(ledger, tmp_path):
    workspace = tmp_path / "ws"
    sr, _ = _running_station_with_applied_effect(
        ledger, "run_resume", workspace, "src/a.py", "applied content"
    )
    outcome = reconcile_run(
        station_runs=[sr],
        effects_for_last_station=ledger.read_effects_for_station_run(sr.id),
        workspace_root=workspace,
        now=_NOW,
    )
    assert outcome.action is ResumeAction.CONTINUE_NEXT_STATION
    assert outcome.station_status_transition is StationRunStatus.NEEDS_RECOVERY
    assert outcome.decision is None


def test_resume_applied_effects_drift_creates_decision(ledger, tmp_path):
    workspace = tmp_path / "ws"
    sr, _ = _running_station_with_applied_effect(
        ledger, "run_resume", workspace, "src/a.py", "applied content"
    )
    # Drift the workspace after the effect was recorded.
    (workspace / "src" / "a.py").write_text("hand-edited drift")

    outcome = reconcile_run(
        station_runs=[sr],
        effects_for_last_station=ledger.read_effects_for_station_run(sr.id),
        workspace_root=workspace,
        now=_NOW,
    )
    assert outcome.action is ResumeAction.DECISION_REQUIRED
    assert outcome.decision is not None
    # Single clean effect → a rollback option is offered (but is a user choice,
    # never auto-applied).
    option_ids = [o.id for o in outcome.decision.options]
    assert "rollback_single_clean_effect" in option_ids
    assert "rerun_from_clean_state" in option_ids


def test_resume_multi_effect_drift_disables_rollback(ledger, tmp_path):
    """Multi-effect auto-rollback DISABLED (§4.8/§5.5 step 5): no rollback option."""

    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "a.py").write_text("a-applied")
    (workspace / "src" / "b.py").write_text("b-applied")
    ha = hash_workspace_file(workspace, "src/a.py")
    hb = hash_workspace_file(workspace, "src/b.py")

    sr = DispatchStationRun(
        id="stationrun_build",
        run_id="run_resume",
        station_id="build",
        worker_id="worker.builder.default.v1",
        context_bundle_id="ctx",
        status=StationRunStatus.RUNNING,
        result_schema_version="station_result.v1",
    )

    def effect(eid, target, after):
        return DispatchEffect(
            id=eid,
            job_id="job",
            station_run_id="stationrun_build",
            tool_name="apply_patch",
            target_type=EffectTargetType.FILE,
            target=target,
            before_exists=False,
            before_hash=None,
            after_hash=after,
            rollback_behavior=RollbackBehavior.DELETE_FILE_IF_AFTER_HASH_MATCHES,
            status=EffectStatus.APPLIED,
            rollback_available=True,
            created_at=_NOW,
            finalized_at=_NOW,
        )

    effects = [effect("effect_a", "src/a.py", ha), effect("effect_b", "src/b.py", hb)]
    # Drift BOTH files.
    (workspace / "src" / "a.py").write_text("a-drift")
    (workspace / "src" / "b.py").write_text("b-drift")

    outcome = reconcile_run(
        station_runs=[sr],
        effects_for_last_station=effects,
        workspace_root=workspace,
        now=_NOW,
    )
    assert outcome.action is ResumeAction.DECISION_REQUIRED
    option_ids = [o.id for o in outcome.decision.options]
    assert "rollback_single_clean_effect" not in option_ids  # auto multi-rollback disabled
    assert "rerun_from_clean_state" in option_ids


def test_resume_planned_effect_matches_before_reruns(ledger, tmp_path):
    """§5.5 case 4: a planned (never finalized) effect whose target matches before."""

    workspace = tmp_path / "ws"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "a.py").write_text("original")
    before = hash_workspace_file(workspace, "src/a.py")

    sr = DispatchStationRun(
        id="stationrun_build",
        run_id="run_resume",
        station_id="build",
        worker_id="worker.builder.default.v1",
        context_bundle_id="ctx",
        status=StationRunStatus.RUNNING,
        result_schema_version="station_result.v1",
    )
    planned = DispatchEffect(
        id="effect_planned",
        job_id="job",
        station_run_id="stationrun_build",
        tool_name="apply_patch",
        target_type=EffectTargetType.FILE,
        target="src/a.py",
        before_exists=True,
        before_hash=before,
        after_hash="sha256:would-have-been",
        rollback_behavior=RollbackBehavior.RESTORE_BEFORE_CONTENT_IF_CURRENT_HASH_MATCHES_AFTER_HASH,
        status=EffectStatus.PLANNED,
        rollback_available=False,
        created_at=_NOW,
        finalized_at=None,
    )
    outcome = reconcile_run(
        station_runs=[sr],
        effects_for_last_station=[planned],
        workspace_root=workspace,
        now=_NOW,
    )
    assert outcome.action is ResumeAction.RERUN_STATION
    assert outcome.rerun_attempt_number == 2


def test_resume_via_fulfillment_line_continues_to_review(ledger, tmp_path):
    """End-to-end resume: an interrupted build with consistent effects resumes review."""

    intake = _code_task_intake()
    route = _select_code_task(intake)
    workspace = tmp_path / "ws"

    # Seed a run + a RUNNING build station with a consistent applied effect.
    from tokenpak.orchestration.dispatch.models.run import DispatchRun

    run = DispatchRun(
        id="run_resume",
        job_id=intake.manifest.job_id,
        manifest_id=intake.manifest.id,
        route_id=route.id,
        started_at=_NOW,
        status="running",
    )
    ledger.write_run(run)
    _running_station_with_applied_effect(
        ledger, "run_resume", workspace, "src/a.py", "applied content"
    )

    line = FulfillmentLine(
        worker_llm=FakeWorkerLLM([WorkerTurn(result_payload={"x": 1}, output_schema_valid=True)]),
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=FakeReviewerLLM("pass"),
        clock=lambda: _NOW,
    )
    result = line.resume(
        run_id="run_resume",
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
        workspace_root=str(workspace),
    )
    # The build was consistent → reconciliation continues to the review station,
    # which passes → delivered.
    assert result.status is LineStatus.DELIVERED
    assert any(sr.station_id == "review" for sr in result.station_runs)


# ---------------------------------------------------------------------------
# 3) Cancellation mid-station with late result (§5.6)
# ---------------------------------------------------------------------------


class CancelDuringTurnWorker:
    """Flips the cancel token during the worker turn (simulates a late TIP result)."""

    def __init__(self, token: FlagCancelToken) -> None:
        self._token = token
        self.calls = 0

    def run_turn(self, *, prompt, context, prior_tool_outputs, iteration):
        self._token.cancelled = True  # cancellation arrives DURING the turn
        self.calls += 1
        return WorkerTurn(result_payload={"late": "output"}, output_schema_valid=True, tokens_used=5)


def test_cancellation_mid_station_captures_late_result(ledger):
    intake = _code_task_intake()
    route = _select_code_task(intake)

    token = FlagCancelToken(False)
    worker = CancelDuringTurnWorker(token)
    line = FulfillmentLine(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        cancel_token=token,
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )

    assert result.status is LineStatus.CANCELLED
    assert result.run.status == "cancelled"
    # The build station was cancelled; a LateResult was captured (effects_applied
    # is always False in v0.1-alpha — §5.6).
    build = result.station_runs[0]
    assert build.status is StationRunStatus.CANCELLED
    assert len(result.late_results) == 1
    late = result.late_results[0]
    assert late.effects_applied is False
    assert late.recovery_allowed is False
    assert late.station_run_id == build.id

    # The queued reviewer station is recorded as cancelled (§5.6 step 3).
    persisted = ledger.read_station_runs_for_run(result.run.id)
    statuses = {sr.station_id: sr.status for sr in persisted}
    assert statuses["build"] is StationRunStatus.CANCELLED
    assert statuses["review"] is StationRunStatus.CANCELLED
    # The LateResult was persisted to the ledger.
    assert ledger.read_late_result(late.id) is not None


def test_cancel_before_start_marks_station_cancelled(ledger):
    intake = _code_task_intake()
    route = _select_code_task(intake)

    token = FlagCancelToken(True)  # already cancelled before the line starts
    line = FulfillmentLine(
        worker_llm=FakeWorkerLLM([WorkerTurn(result_payload={"x": 1}, output_schema_valid=True)]),
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        cancel_token=token,
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )
    assert result.status is LineStatus.CANCELLED
    # No worker turn ever ran; every station is cancelled.
    persisted = ledger.read_station_runs_for_run(result.run.id)
    assert all(sr.status is StationRunStatus.CANCELLED for sr in persisted)


# ---------------------------------------------------------------------------
# 4) Reviewer warning → decision flow (§5.7)
# ---------------------------------------------------------------------------


def test_reviewer_warning_creates_decision(ledger):
    intake = _code_task_intake()
    route = _select_code_task(intake)

    worker = FakeWorkerLLM(
        [WorkerTurn(result_payload={"summary": "drafted"}, output_schema_valid=True)]
    )
    reviewer = FakeReviewerLLM("warning", reason="naming nit")
    line = FulfillmentLine(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=reviewer,
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )

    assert result.status is LineStatus.DECISION_REQUIRED
    assert result.decision is not None
    assert result.delivery_package.status.value == "decision_required"
    # The warning decision is the accept/reject one (§5.7).
    option_ids = {o.id for o in result.decision.options}
    assert option_ids == {"accept", "reject"}
    # Persisted to the ledger + linked on the run.
    assert ledger.read_decision(result.decision.id) is not None
    assert result.decision.id in ledger.read_run(result.run.id).decisions


def test_reviewer_fail_blocks_delivery(ledger):
    intake = _code_task_intake()
    route = _select_code_task(intake)

    line = FulfillmentLine(
        worker_llm=FakeWorkerLLM(
            [WorkerTurn(result_payload={"summary": "drafted"}, output_schema_valid=True)]
        ),
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=FakeReviewerLLM("fail", reason="criterion unmet"),
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )
    assert result.status is LineStatus.BLOCKED
    assert result.delivery_package.status.value == "blocked"
    # No automatic repair loop: required_fixes carried, nothing re-dispatched.
    assert result.delivery_package.required_fixes


# ---------------------------------------------------------------------------
# StationLoopPolicy precedence + stop conditions (§5.4)
# ---------------------------------------------------------------------------


def test_loop_policy_system_default():
    policy = system_default_loop_policy()
    assert (policy.max_iterations, policy.max_tool_calls, policy.max_wall_seconds) == (2, 6, 600)


def test_loop_policy_precedence_station_override_wins():
    override = StationLoopPolicy(max_iterations=9, max_tool_calls=9, max_wall_seconds=9)
    resolved = resolve_loop_policy(
        station_override=override,
        worker_default=WorkerLoopDefault(max_iterations=3, max_tool_calls=8, max_wall_seconds=900),
        route_intent="code_task",
    )
    assert resolved is override  # station override is fully authoritative


def test_loop_policy_route_default_overrides_wall_seconds_only():
    resolved = resolve_loop_policy(
        worker_default=WorkerLoopDefault(max_iterations=3, max_tool_calls=8, max_wall_seconds=900),
        route_intent="code_task",
    )
    # code_task route wall-second default (1800) overrides the worker's 900;
    # iteration / tool-call budgets fall through from the worker default.
    assert resolved.max_wall_seconds == 1800
    assert resolved.max_iterations == 3
    assert resolved.max_tool_calls == 8


def test_loop_policy_worker_default_when_no_route_match():
    resolved = resolve_loop_policy(
        worker_default=WorkerLoopDefault(max_iterations=3, max_tool_calls=8, max_wall_seconds=777),
        route_intent="unknown_intent",
    )
    assert resolved.max_wall_seconds == 777  # no route default → worker wall-seconds


def test_loop_stop_conditions_exact_set():
    """The §5.4 stop_when set is exact; station_goal_satisfied is excluded."""

    members = {c.value for c in LoopStopCondition}
    assert "station_goal_satisfied" not in members
    assert members == {
        "output_schema_valid AND no_pending_tool_requests",
        "loop_budget_exhausted",
        "cancel_requested",
        "tool_policy_violation",
        "fatal_error",
    }


def test_evaluate_stop_success_exit():
    policy = system_default_loop_policy()
    state = LoopState(
        iteration_count=1,
        tool_call_count=0,
        wall_seconds=1,
        output_schema_valid=True,
        pending_tool_requests=False,
    )
    outcome = evaluate_stop(state, policy)
    assert outcome.stop_condition is LoopStopCondition.OUTPUT_SCHEMA_VALID_AND_NO_PENDING_TOOL_REQUESTS


def test_evaluate_stop_budget_exhausted():
    policy = StationLoopPolicy(max_iterations=1, max_tool_calls=6, max_wall_seconds=600)
    state = LoopState(
        iteration_count=1,
        tool_call_count=0,
        wall_seconds=1,
        output_schema_valid=False,
        pending_tool_requests=False,
    )
    outcome = evaluate_stop(state, policy)
    assert outcome.stop_condition is LoopStopCondition.LOOP_BUDGET_EXHAUSTED
    assert outcome.exhausted is True


def test_evaluate_stop_cancel_wins():
    policy = system_default_loop_policy()
    state = LoopState(
        iteration_count=1,
        tool_call_count=0,
        wall_seconds=1,
        output_schema_valid=True,  # would otherwise be the success exit
        pending_tool_requests=False,
        cancel_requested=True,
    )
    outcome = evaluate_stop(state, policy)
    assert outcome.stop_condition is LoopStopCondition.CANCEL_REQUESTED


# ---------------------------------------------------------------------------
# Spend Guard inheritance (§8)
# ---------------------------------------------------------------------------


def test_spend_guard_hard_stop_creates_decision(ledger):
    intake = _code_task_intake()
    route = _select_code_task(intake)

    # A Spend Guard that reports an exhausted cap (<= 0) → hard stop before the
    # first worker turn (§8).
    line = FulfillmentLine(
        worker_llm=FakeWorkerLLM([WorkerTurn(result_payload={"x": 1}, output_schema_valid=True)]),
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=FakeReviewerLLM("pass"),
        spend_guard=lambda: 0,  # cap exhausted
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.AUTO_DISPATCH_LIMITED,
    )
    assert result.status is LineStatus.DECISION_REQUIRED
    assert result.decision is not None
    # The build station failed with the spend_guard reason.
    build = result.station_runs[0]
    assert build.status is StationRunStatus.FAILED
    # The §8 decision offers raise-budget / change-route / cancel.
    option_ids = {o.id for o in result.decision.options}
    assert option_ids == {"raise_budget", "change_route", "cancel_job"}
    assert ledger.read_decision(result.decision.id) is not None
    # The decision is linked onto the persisted run record.
    assert result.decision.id in ledger.read_run(result.run.id).decisions


def test_spend_guard_reason_constant_threaded():
    """The spend-guard reason the StationRunner emits matches the §8 contract string."""

    assert SPEND_GUARD_EXCEEDED_REASON == "spend_guard_exceeded"


# ---------------------------------------------------------------------------
# Tool authorization through the loop (§5.3)
# ---------------------------------------------------------------------------


def test_denied_tool_request_ends_loop_with_policy_violation(ledger):
    """A worker that requests apply_patch under ADVISORY (denied) → tool_policy_violation."""

    intake = _code_task_intake()
    route = _select_code_task(intake)

    # ADVISORY denies apply_patch; the worker requests it → loop ends FAILED with
    # a tool_policy_violation stop condition.
    worker = FakeWorkerLLM(
        [WorkerTurn(tool_requests=(WorkerToolRequest(tool="apply_patch"),))]
    )
    line = FulfillmentLine(
        worker_llm=worker,
        context_provider=LocalContextProvider(),
        ledger=ledger,
        worker_registry=default_worker_registry(),
        reviewer_llm=FakeReviewerLLM("pass"),
        clock=lambda: _NOW,
    )
    result = line.run(
        route=route,
        manifest=intake.manifest,
        autonomy_mode=AutonomyMode.ADVISORY,
    )
    assert result.status is LineStatus.FAILED
    build = result.station_runs[0]
    assert build.status is StationRunStatus.FAILED
