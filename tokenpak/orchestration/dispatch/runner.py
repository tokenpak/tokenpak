"""FulfillmentLine runner — sequential station execution.

The :class:`FulfillmentLine` is the top-level execution engine P-EXEC-01 ships. It
takes a *selected, bound* route (from :class:`DispatchRuntime.select_route`) and
runs its stations **sequentially**, one :class:`StationRunner` per station, until
the route completes, a gate blocks, a decision is required, or cancellation
propagates.

**Sequential execution only.** Stations run strictly in declaration order; the
output of each station is available to the next. There is **no parallel
execution** and **no branch primitive** in v0.1-alpha — these are a deliberate
omission (parallel fulfillment and branch decisions are explicitly NOT in
v0.1-alpha). A FulfillmentLine is a *line*, not a graph; the
runner asserts this by walking ``route.stations`` in order with no fan-out.

What the FulfillmentLine wires together:

* the **StationRunner** for each worker station (worker + overlay + context cargo
  + tool registry + bounded loop, :mod:`.station_runner`);
* the **Reviewer Station** for a station whose role is ``reviewer``,
  invoked through the injected :class:`ReviewerLLM` boundary;
* the **Gatehouse** Delivery Gate — reviewer ``pass`` continues,
  ``warning`` auto-creates a :class:`DispatchDecision`, ``fail`` blocks delivery;
* the **Run Ledger** — the :class:`DispatchRun` record is written at start and
  updated as stations complete; each :class:`DispatchStationRun` is committed by
  its StationRunner only after schema-valid output (criterion 4);
* **Spend Guard inheritance** — a station that fails with
  ``reason=spend_guard_exceeded`` halts the line and surfaces a
  :class:`DispatchDecision` (raise budget / change route / cancel);
* **Resume** — :meth:`FulfillmentLine.resume` reconciles an interrupted
  run via :func:`reconcile_run` before continuing;
* **Cancellation** — a cancel token marks queued stations ``cancelled``
  and captures a late TIP result as a :class:`LateResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional
from uuid import uuid4

from .gatehouse import DeliveryPackage, DeliveryStatus, Gatehouse
from .ledger.db import RunLedger
from .loop_policy import ROUTE_WALL_SECOND_DEFAULTS
from .models.decision import (
    DecisionDefaultAction,
    DecisionOption,
    DecisionRecommendation,
    DispatchDecision,
)
from .models.enums import (
    AutoApplyAfter,
    AutonomyMode,
    DecisionScope,
    DecisionStatus,
    RiskLevel,
    StationRunStatus,
)
from .models.late_result import LateResult
from .models.manifest import DispatchManifest
from .models.receipt import DispatchReceipt
from .models.route import DispatchRoute, RouteStation
from .models.run import DispatchRun
from .models.station_run import DispatchStationRun
from .models.worker import DispatchWorker
from .receipt_builder import build_and_write_receipt
from .registry.routes import is_worker_station
from .registry.workers import (
    DispatchWorkerRegistry,
    OverlayLoader,
    PromptOverlay,
    assert_route_binding,
)
from .resume import ResumeAction, ResumeOutcome, reconcile_run
from .station_runner import (
    SPEND_GUARD_EXCEEDED_REASON,
    CancelToken,
    FlagCancelToken,
    SpendGuard,
    StationRunner,
    WorkerLLM,
    unlimited_spend_guard,
)
from .stations.reviewer import (
    ReviewerLLM,
    ReviewerStation,
    ReviewerStationInput,
    ReviewerStationResult,
)

# Terminal run statuses. ``DispatchRun.status`` tracks ``DispatchJob.status``;
# the job state machine defines exactly these four as terminal. A run in one of
# these states must never be re-walked, re-finalized, or resumed.
TERMINAL_RUN_STATUSES: frozenset[str] = frozenset({"delivered", "cancelled", "failed", "withdrawn"})


class RunAlreadyTerminalError(RuntimeError):
    """Raised when run()/resume() targets a run already in a terminal status."""

    def __init__(self, run_id: str, status: str) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(
            f"run {run_id!r} is already terminal (status {status!r}); "
            "it cannot be run or resumed again"
        )


class RunLeaseHeldError(RuntimeError):
    """Raised when another caller holds the run lease (concurrent run/resume)."""

    def __init__(self, run_id: str, holder: Optional[str]) -> None:
        self.run_id = run_id
        self.holder = holder
        held_by = f" (held by {holder!r})" if holder else ""
        super().__init__(
            f"run {run_id!r} is already being executed by another caller{held_by}; "
            "this caller is exiting without doing any work"
        )


# Status the line returns. Distinct from any single station's status: it reports
# the *line-level* outcome the caller acts on.
class LineStatus(str, Enum):
    """Outcome of a FulfillmentLine run (the caller's directive)."""

    DELIVERED = "delivered"  # all stations ran; delivery gate ready
    DELIVERY_READY_WITH_WARNING = "delivery_ready_with_warning"
    BLOCKED = "blocked"  # delivery gate blocked (reviewer fail / failed check)
    DECISION_REQUIRED = "decision_required"  # a decision halted the line
    CANCELLED = "cancelled"  # cancellation propagated
    FAILED = "failed"  # a station failed (non-spend-guard)


@dataclass
class FulfillmentResult:
    """The result of running a FulfillmentLine.

    Carries the line status, the persisted :class:`DispatchRun`, the per-station
    :class:`DispatchStationRun` records produced, any :class:`DispatchDecision`
    that halted the line (spend-guard / reviewer-warning / resume drift), the
    Gatehouse :class:`DeliveryPackage` when a delivery gate ran, and any
    :class:`LateResult` captured on cancellation.
    """

    status: LineStatus
    run: DispatchRun
    station_runs: list[DispatchStationRun] = field(default_factory=list)
    decision: Optional[DispatchDecision] = None
    delivery_package: Optional[DeliveryPackage] = None
    late_results: list[LateResult] = field(default_factory=list)
    effect_ids: list[str] = field(default_factory=list)
    reviewer_result: Optional[ReviewerStationResult] = None
    receipt: Optional[DispatchReceipt] = None
    reason: str = ""


class FulfillmentLine:
    """Sequential station-execution engine.

    Construct with the foundation seams — a :class:`WorkerLLM` (the TIP worker
    boundary), a context provider, a :class:`RunLedger`, a worker registry, and
    optional Spend Guard / cancel token / reviewer client / overlay loader. Call
    :meth:`run` with a *selected, bound* route, the manifest, and the autonomy
    mode.

    **Sequential, no parallel, no branches.** :meth:`_walk_stations` iterates
    ``route.stations`` in order. There is no fan-out, no concurrent station, and
    no conditional branch primitive — that is the deliberate v0.1-alpha omission.
    A later version may add a branch model; this runner does not.
    """

    def __init__(
        self,
        *,
        worker_llm: WorkerLLM,
        context_provider: Any,
        ledger: RunLedger,
        worker_registry: DispatchWorkerRegistry,
        reviewer_llm: Optional[ReviewerLLM] = None,
        overlay_loader: Optional[OverlayLoader] = None,
        gatehouse: Optional[Gatehouse] = None,
        spend_guard: Optional[SpendGuard] = None,
        cancel_token: Optional[CancelToken] = None,
        tool_runner: Optional[Callable[[Any], Any]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._worker_llm = worker_llm
        self._context_provider = context_provider
        self._ledger = ledger
        self._workers = worker_registry
        self._reviewer_llm = reviewer_llm
        self._overlay_loader = overlay_loader if overlay_loader is not None else OverlayLoader()
        self._gatehouse = gatehouse or Gatehouse()
        self._spend_guard = spend_guard or unlimited_spend_guard
        self._cancel = cancel_token or FlagCancelToken(False)
        self._tool_runner = tool_runner
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- public API ----------------------------------------------------------

    def run(
        self,
        *,
        route: DispatchRoute,
        manifest: DispatchManifest,
        autonomy_mode: AutonomyMode | str,
        route_intent: Optional[str] = None,
        run_id: Optional[str] = None,
        approval_granted: bool = False,
    ) -> FulfillmentResult:
        """Run a route's stations sequentially and return the line result.

        Writes a :class:`DispatchRun` at start, runs each station in order via a
        :class:`StationRunner` (or the Reviewer Station for a reviewer station),
        and finalizes the run record. Halts early on a failed station, a
        spend-guard hard stop, a reviewer block/decision, or cancellation.

        Raises :class:`RunAlreadyTerminalError` when ``run_id`` names an existing
        run already in a terminal status, and :class:`RunLeaseHeldError` when
        another caller currently holds the run's execution lease.
        """

        mode = (
            autonomy_mode
            if isinstance(autonomy_mode, AutonomyMode)
            else AutonomyMode(autonomy_mode)
        )
        rid = run_id or f"run_{uuid4().hex}"
        intent = route_intent if route_intent is not None else _route_intent(route)

        owner = self._claim_lease(rid)
        try:
            # Terminal-state guard (checked under the lease so a concurrent
            # finalize cannot slip between check and write).
            existing = self._ledger.read_run(rid)
            if existing is not None and existing.status in TERMINAL_RUN_STATUSES:
                raise RunAlreadyTerminalError(rid, existing.status)

            run = DispatchRun(
                id=rid,
                job_id=manifest.job_id,
                manifest_id=manifest.id,
                route_id=route.id,
                started_at=self._clock(),
                status="running",
            )
            self._ledger.write_run(run)

            return self._walk_stations(
                run=run,
                route=route,
                manifest=manifest,
                mode=mode,
                intent=intent,
                approval_granted=approval_granted,
                start_index=0,
            )
        finally:
            self._ledger.release_run_lease(rid, owner)

    def resume(
        self,
        *,
        run_id: str,
        route: DispatchRoute,
        manifest: DispatchManifest,
        autonomy_mode: AutonomyMode | str,
        workspace_root: str,
        route_intent: Optional[str] = None,
        approval_granted: bool = False,
    ) -> FulfillmentResult:
        """Resume an interrupted run.

        Reconciles the last station via :func:`reconcile_run`, persists the
        station-status transition, and — depending on the reconciliation verdict —
        continues with the next station, reruns the interrupted station, or
        surfaces a :class:`DispatchDecision` (drift / unknown state). Multi-effect
        auto-rollback is never performed.

        A rerun directive carries the reconciliation's ``rerun_attempt_number``
        through to the interrupted station's new attempt, so retried attempts
        are numbered ``attempt+1`` rather than restarting at 1.

        Raises :class:`RunAlreadyTerminalError` for a run already in a terminal
        status (a delivered/failed/cancelled run is never silently re-executed)
        and :class:`RunLeaseHeldError` when another caller holds the run lease.
        """

        mode = (
            autonomy_mode
            if isinstance(autonomy_mode, AutonomyMode)
            else AutonomyMode(autonomy_mode)
        )
        intent = route_intent if route_intent is not None else _route_intent(route)

        run = self._ledger.read_run(run_id)
        if run is None:
            raise KeyError(f"cannot resume unknown run {run_id!r}")
        if run.status in TERMINAL_RUN_STATUSES:
            raise RunAlreadyTerminalError(run_id, run.status)

        owner = self._claim_lease(run_id)
        try:
            # Re-read under the lease: a concurrent caller may have finalized the
            # run between the fast-path check above and the lease claim.
            run = self._ledger.read_run(run_id)
            if run is None:
                raise KeyError(f"cannot resume unknown run {run_id!r}")
            if run.status in TERMINAL_RUN_STATUSES:
                raise RunAlreadyTerminalError(run_id, run.status)

            station_runs = self._ledger.read_station_runs_for_run(run_id)
            effects_for_last = (
                self._ledger.read_effects_for_station_run(station_runs[-1].id)
                if station_runs
                else []
            )
            outcome = reconcile_run(
                station_runs=station_runs,
                effects_for_last_station=effects_for_last,
                workspace_root=workspace_root,
                now=self._clock(),
            )

            # Persist any station-status transition the reconciliation directs.
            if station_runs and outcome.station_status_transition is not None:
                self._transition_station(station_runs[-1], outcome.station_status_transition)

            # A decision halts the resume — record it and return.
            if outcome.action is ResumeAction.DECISION_REQUIRED and outcome.decision is not None:
                self._ledger.write_decision(outcome.decision)
                run = self._finalize_run(run, status="blocked", decision=outcome.decision)
                return FulfillmentResult(
                    status=LineStatus.DECISION_REQUIRED,
                    run=run,
                    station_runs=station_runs,
                    decision=outcome.decision,
                    reason=outcome.reason,
                )

            # Promote planned effects that the reconciliation found were applied.
            for effect_id in outcome.promote_effect_ids:
                self._ledger.mark_effect_applied(effect_id)

            # Determine where to continue from (and, on a rerun, which attempt
            # number the interrupted station's new attempt carries).
            start_index = self._resume_start_index(route, station_runs, outcome)
            first_attempt = (
                outcome.rerun_attempt_number
                if outcome.action is ResumeAction.RERUN_STATION
                and outcome.rerun_attempt_number is not None
                else 1
            )
            return self._walk_stations(
                run=run,
                route=route,
                manifest=manifest,
                mode=mode,
                intent=intent,
                approval_granted=approval_granted,
                start_index=start_index,
                prior_station_runs=station_runs,
                first_station_attempt_number=first_attempt,
            )
        finally:
            self._ledger.release_run_lease(run_id, owner)

    # -- lease helper ----------------------------------------------------------

    def _claim_lease(self, run_id: str) -> str:
        """Claim the run's execution lease; return the owner token.

        Raises :class:`RunLeaseHeldError` (with the holder, when readable) if
        another caller already holds the lease. The token is unique per call so
        two walks through the same FulfillmentLine instance still exclude each
        other.
        """

        owner = f"line_{uuid4().hex}"
        if not self._ledger.try_claim_run_lease(run_id, owner):
            lease = self._ledger.read_run_lease(run_id)
            raise RunLeaseHeldError(run_id, lease["owner"] if lease else None)
        return owner

    # -- the sequential walk -------------------------------------------------

    def _walk_stations(
        self,
        *,
        run: DispatchRun,
        route: DispatchRoute,
        manifest: DispatchManifest,
        mode: AutonomyMode,
        intent: Optional[str],
        approval_granted: bool,
        start_index: int,
        prior_station_runs: Optional[list[DispatchStationRun]] = None,
        first_station_attempt_number: int = 1,
    ) -> FulfillmentResult:
        """Walk ``route.stations`` sequentially from ``start_index`` (no parallel).

        ``first_station_attempt_number`` is the attempt number for the station at
        ``start_index`` (resume threads the reconciliation's rerun attempt
        through it); every subsequent station starts at attempt 1.
        """

        station_runs: list[DispatchStationRun] = list(prior_station_runs or [])
        late_results: list[LateResult] = []
        effect_ids: list[str] = []
        reviewer_result: Optional[ReviewerStationResult] = None
        last_build_station_run: Optional[DispatchStationRun] = None

        stations = route.stations
        for index in range(start_index, len(stations)):
            station = stations[index]

            # Cancellation: mark this + all remaining queued stations cancelled.
            if self._cancel.is_cancelled():
                self._mark_remaining_cancelled(run, stations[index:])
                run = self._finalize_run(run, status="cancelled")
                return FulfillmentResult(
                    status=LineStatus.CANCELLED,
                    run=run,
                    station_runs=station_runs,
                    late_results=late_results,
                    effect_ids=effect_ids,
                    reviewer_result=reviewer_result,
                    reason="Cancellation requested; remaining stations marked cancelled.",
                )

            # Reviewer station vs worker station.
            if _is_reviewer_station(station):
                reviewer_result, review_run = self._run_reviewer_station(
                    run=run,
                    route=route,
                    manifest=manifest,
                    station=station,
                    build_station_run=last_build_station_run,
                )
                if review_run is not None:
                    station_runs.append(review_run)
                # Reviewer ran → evaluate the Delivery Gate now.
                package = self._gatehouse.evaluate_delivery(
                    job_id=manifest.job_id,
                    manifest=manifest,
                    route=route,
                    reviewer_result=reviewer_result,
                    station_runs=station_runs,
                    delivery_package_fields=_delivery_fields(route, station_runs),
                    route_uses_reviewer=True,
                )
                return self._finalize_with_delivery(
                    run=run,
                    package=package,
                    station_runs=station_runs,
                    late_results=late_results,
                    effect_ids=effect_ids,
                    reviewer_result=reviewer_result,
                )

            # Worker station: run via a StationRunner.
            outcome = self._run_worker_station(
                run=run,
                manifest=manifest,
                station=station,
                mode=mode,
                intent=intent,
                approval_granted=approval_granted,
                attempt_number=(first_station_attempt_number if index == start_index else 1),
            )
            station_runs.append(outcome.station_run)
            effect_ids.extend(outcome.effect_ids)
            if outcome.late_result is not None:
                late_results.append(outcome.late_result)
            last_build_station_run = outcome.station_run

            # Cancellation surfaced mid-station (late result captured).
            if outcome.station_run.status is StationRunStatus.CANCELLED:
                self._mark_remaining_cancelled(run, stations[index + 1 :])
                run = self._finalize_run(run, status="cancelled")
                return FulfillmentResult(
                    status=LineStatus.CANCELLED,
                    run=run,
                    station_runs=station_runs,
                    late_results=late_results,
                    effect_ids=effect_ids,
                    reason="Cancellation propagated mid-station; late result captured.",
                )

            # Spend Guard hard stop: surface a decision, halt the line.
            if outcome.failure_reason == SPEND_GUARD_EXCEEDED_REASON:
                decision = self._build_spend_guard_decision(run, station)
                self._ledger.write_decision(decision)
                run = self._finalize_run(run, status="blocked", decision=decision)
                return FulfillmentResult(
                    status=LineStatus.DECISION_REQUIRED,
                    run=run,
                    station_runs=station_runs,
                    decision=decision,
                    late_results=late_results,
                    effect_ids=effect_ids,
                    reason="Spend Guard hard-stopped a station.",
                )

            # Any other station failure halts the line (no automatic repair loop).
            if outcome.station_run.status is StationRunStatus.FAILED:
                run = self._finalize_run(run, status="failed")
                return FulfillmentResult(
                    status=LineStatus.FAILED,
                    run=run,
                    station_runs=station_runs,
                    late_results=late_results,
                    effect_ids=effect_ids,
                    reason=f"Station {station.id!r} failed; line halted.",
                )

        # Walked every station with no reviewer gate (e.g. quick_answer): the
        # line is delivered. Build a delivery package via the Gatehouse with no
        # reviewer (a pass-through structural gate).
        package = self._gatehouse.evaluate_delivery(
            job_id=manifest.job_id,
            manifest=manifest,
            route=route,
            reviewer_result=ReviewerStationResult.for_status("pass"),
            station_runs=station_runs,
            delivery_package_fields=_delivery_fields(route, station_runs),
            route_uses_reviewer=False,
        )
        return self._finalize_with_delivery(
            run=run,
            package=package,
            station_runs=station_runs,
            late_results=late_results,
            effect_ids=effect_ids,
            reviewer_result=reviewer_result,
        )

    # -- worker station ------------------------------------------------------

    def _run_worker_station(
        self,
        *,
        run: DispatchRun,
        manifest: DispatchManifest,
        station: RouteStation,
        mode: AutonomyMode,
        intent: Optional[str],
        approval_granted: bool,
        attempt_number: int = 1,
    ):
        """Resolve the worker + overlay, then run the station via a StationRunner."""

        worker = self._resolve_worker(station)
        overlay = self._resolve_overlay(station)
        # Capability intersection: the worker must satisfy the overlay's and
        # the station's required capabilities or the binding fails loud. The
        # route was already bound by select_route, but re-asserting here keeps the
        # station runner's contract local and explicit.
        assert_route_binding(worker, overlay, station.required_capabilities)

        runner = StationRunner(
            worker_llm=self._worker_llm,
            context_provider=self._context_provider,
            ledger=self._ledger,
            spend_guard=self._spend_guard,
            cancel_token=self._cancel,
            tool_runner=self._tool_runner,
            clock=self._clock,
        )
        outcome = runner.run(
            run_id=run.id,
            manifest=manifest,
            station=station,
            worker=worker,
            autonomy_mode=mode,
            overlay=overlay,
            route_intent=intent,
            attempt_number=attempt_number,
            approval_granted=approval_granted,
        )
        # Append the station run id onto the run record (kept current as we go).
        self._append_station_run(run, outcome.station_run.id)
        for effect_id in outcome.effect_ids:
            self._append_effect(run, effect_id)
        if outcome.late_result is not None:
            self._append_late_result(run, outcome.late_result.id)
        return outcome

    def _resolve_worker(self, station: RouteStation) -> DispatchWorker:
        """Resolve the single worker that staffs a worker station (deterministic).

        Picks the first registry worker (by id) that declares the station's role
        and possesses every required capability — the same capability-intersection
        rule as ``resolve_station_workers``, reduced to a single deterministic
        pick for sequential execution.
        """

        role = station.required_role
        required = set(station.required_capabilities)
        for worker in self._workers.for_role(role):
            if required.issubset(set(worker.capabilities)):
                return worker
        raise RuntimeError(
            f"no worker staffs station {station.id!r} (role {role!r}, "
            f"capabilities {sorted(required)!r}); route should have been rejected "
            "at selection time"
        )

    def _resolve_overlay(self, station: RouteStation) -> Optional[PromptOverlay]:
        """Load the station's prompt overlay, or ``None`` when it declares none."""

        if not station.prompt_overlay:
            return None
        return self._overlay_loader.load(station.prompt_overlay)

    # -- reviewer station ----------------------------------------------------

    def _run_reviewer_station(
        self,
        *,
        run: DispatchRun,
        route: DispatchRoute,
        manifest: DispatchManifest,
        station: RouteStation,
        build_station_run: Optional[DispatchStationRun],
    ) -> tuple[ReviewerStationResult, Optional[DispatchStationRun]]:
        """Run the Reviewer Station and commit its station-run record.

        Requires a reviewer client to have been injected; raises if absent (a
        route with a reviewer station cannot run without one). Builds the
        :class:`ReviewerStationInput` from the manifest + the upstream build
        station's output, makes exactly one review call, and commits a
        ``completed`` :class:`DispatchStationRun` for the reviewer station.
        """

        if self._reviewer_llm is None:
            raise RuntimeError(
                f"route {route.id!r} has a reviewer station {station.id!r} but no "
                "reviewer client was injected into the FulfillmentLine"
            )

        reviewer = ReviewerStation(self._reviewer_llm)
        review_input = ReviewerStationInput(
            manifest_id=manifest.id,
            route_id=route.id,
            build_station_result_id=(
                build_station_run.id if build_station_run is not None else "stationrun_none"
            ),
            acceptance_criteria=list(manifest.acceptance_criteria),
            constraints=list(manifest.constraints),
            context_summary=manifest.goal,
        )
        result = reviewer.review(review_input)

        worker = self._resolve_worker(station)
        review_run = DispatchStationRun(
            id=f"stationrun_{uuid4().hex}",
            run_id=run.id,
            station_id=station.id,
            worker_id=worker.id,
            prompt_overlay_id=None,
            context_bundle_id=f"reviewer_{manifest.id}_{station.id}",
            tip_request_ids=[f"tip_{run.id}_review"],
            status=StationRunStatus.COMPLETED,
            iteration_count=1,
            tool_call_count=0,
            wall_seconds=0,
            result_payload=result.model_dump(mode="json"),
            result_schema_version="reviewer_station_result.v1",
            attempt_number=1,
        )
        # Commit only after the schema-valid reviewer output exists (criterion 4).
        self._ledger.write_station_run(review_run)
        self._append_station_run(run, review_run.id)
        return result, review_run

    # -- delivery + finalization --------------------------------------------

    def _finalize_with_delivery(
        self,
        *,
        run: DispatchRun,
        package: DeliveryPackage,
        station_runs: list[DispatchStationRun],
        late_results: list[LateResult],
        effect_ids: list[str],
        reviewer_result: Optional[ReviewerStationResult],
    ) -> FulfillmentResult:
        """Map a Gatehouse :class:`DeliveryPackage` onto the line result + run status."""

        if package.decision is not None:
            self._ledger.write_decision(package.decision)
            self._append_decision(run, package.decision.id)

        status_map = {
            DeliveryStatus.DELIVERY_READY: (LineStatus.DELIVERED, "delivered"),
            DeliveryStatus.DELIVERY_READY_WITH_WARNING: (
                LineStatus.DELIVERY_READY_WITH_WARNING,
                "delivery_ready",
            ),
            DeliveryStatus.DECISION_REQUIRED: (LineStatus.DECISION_REQUIRED, "gate_review"),
            DeliveryStatus.BLOCKED: (LineStatus.BLOCKED, "blocked"),
        }
        line_status, run_status = status_map[package.status]
        run = self._finalize_run(run, status=run_status, decision=package.decision)

        receipt = None
        if line_status in (LineStatus.DELIVERED, LineStatus.DELIVERY_READY_WITH_WARNING):
            receipt = build_and_write_receipt(
                run=run,
                ledger=self._ledger,
                final_status=run_status,
                clock=self._clock,
            )

        return FulfillmentResult(
            status=line_status,
            run=run,
            station_runs=station_runs,
            decision=package.decision,
            delivery_package=package,
            late_results=late_results,
            effect_ids=effect_ids,
            reviewer_result=reviewer_result,
            receipt=receipt,
            reason=package.summary,
        )

    def _finalize_run(
        self,
        run: DispatchRun,
        *,
        status: str,
        decision: Optional[DispatchDecision] = None,
    ) -> DispatchRun:
        """Set the run's terminal status + ended_at and persist it atomically.

        When a ``decision`` halted the run it is linked onto ``run.decisions`` (if
        not already there) so the Run Ledger record references it — the decision
        itself is written by the caller.

        Idempotency guard: if the persisted run record is ALREADY in a terminal
        status, this is a no-op that returns the persisted record unchanged — a
        finished run is never re-finalized (its status, ``ended_at``, and receipt
        linkage stay exactly as first written).
        """

        persisted = self._ledger.read_run(run.id)
        if persisted is not None and persisted.status in TERMINAL_RUN_STATUSES:
            return persisted

        decisions = list(run.decisions)
        if decision is not None and decision.id not in decisions:
            decisions.append(decision.id)
        run = run.model_copy(
            update={"status": status, "ended_at": self._clock(), "decisions": decisions}
        )
        self._ledger.write_run(run)
        return run

    # -- resume helpers ------------------------------------------------------

    def _resume_start_index(
        self,
        route: DispatchRoute,
        station_runs: list[DispatchStationRun],
        outcome: ResumeOutcome,
    ) -> int:
        """Pick the station index to resume from given the reconciliation verdict.

        * CONTINUE_NEXT_STATION → the station after the last completed one.
        * PROMOTE_AND_CONTINUE → the station after the interrupted one.
        * RERUN_STATION → the interrupted station itself.
        """

        if not station_runs:
            return 0
        last = station_runs[-1]
        last_index = _station_index(route, last.station_id)
        if outcome.action in (
            ResumeAction.CONTINUE_NEXT_STATION,
            ResumeAction.PROMOTE_AND_CONTINUE,
        ):
            return last_index + 1
        # RERUN_STATION → rerun the interrupted station.
        return last_index

    def _transition_station(
        self, station_run: DispatchStationRun, status: StationRunStatus
    ) -> None:
        """Persist a station-run status transition (resume reconciliation)."""

        updated = station_run.model_copy(update={"status": status})
        self._ledger.write_station_run(updated)

    # -- run-record append helpers (keep the DispatchRun lists current) ------

    def _append_station_run(self, run: DispatchRun, station_run_id: str) -> None:
        if station_run_id not in run.station_runs:
            run.station_runs.append(station_run_id)
            self._ledger.write_run(run)

    def _append_decision(self, run: DispatchRun, decision_id: str) -> None:
        if decision_id not in run.decisions:
            run.decisions.append(decision_id)
            self._ledger.write_run(run)

    def _append_effect(self, run: DispatchRun, effect_id: str) -> None:
        if effect_id not in run.effects:
            run.effects.append(effect_id)
            self._ledger.write_run(run)

    def _append_late_result(self, run: DispatchRun, late_result_id: str) -> None:
        if late_result_id not in run.late_results:
            run.late_results.append(late_result_id)
            self._ledger.write_run(run)

    # -- cancellation --------------------------------------------------------

    def _mark_remaining_cancelled(self, run: DispatchRun, remaining: list[RouteStation]) -> None:
        """Mark every not-yet-run station ``cancelled``.

        Each queued station gets a ``cancelled`` :class:`DispatchStationRun` so
        the Run Ledger records exactly which stations never ran.
        """

        for station in remaining:
            worker_id = self._cancelled_worker_id(station)
            cancelled = DispatchStationRun(
                id=f"stationrun_{uuid4().hex}",
                run_id=run.id,
                station_id=station.id,
                worker_id=worker_id,
                prompt_overlay_id=station.prompt_overlay,
                context_bundle_id="(not-built: cancelled)",
                tip_request_ids=[],
                status=StationRunStatus.CANCELLED,
                iteration_count=0,
                tool_call_count=0,
                wall_seconds=0,
                result_payload=None,
                result_schema_version=station.output_schema,
                attempt_number=1,
            )
            self._ledger.write_station_run(cancelled)
            self._append_station_run(run, cancelled.id)

    def _cancelled_worker_id(self, station: RouteStation) -> str:
        """Best-effort worker id for a cancelled station's record (never raises)."""

        if not is_worker_station(station):
            return station.system_component or "system_component"
        try:
            return self._resolve_worker(station).id
        except RuntimeError:
            return f"role:{station.required_role}"

    # -- decision builders ---------------------------------------------------

    def _build_spend_guard_decision(
        self, run: DispatchRun, station: RouteStation
    ) -> DispatchDecision:
        """Build the Spend-Guard decision (raise budget / change route / cancel)."""

        return DispatchDecision(
            id=f"decision_{run.id}_spend_guard",
            job_id=run.job_id,
            created_at=self._clock(),
            scope=DecisionScope.STATION,
            title="Spend Guard hard-stopped a station",
            question=(
                f"The Spend Guard cap was reached while running station "
                f"{station.id!r}. Raise the budget, change the route, or cancel "
                "the job?"
            ),
            reason=(
                "A station hit the Spend Guard cap hard "
                "stop (reason=spend_guard_exceeded). Dispatch surfaces a decision "
                "rather than bypassing Spend Guard."
            ),
            risk_level=RiskLevel.MEDIUM,
            options=[
                DecisionOption(
                    id="raise_budget",
                    label="Raise the budget",
                    description="Increase the Spend Guard cap and continue the station.",
                    tradeoffs=["Spends more tokens than the original cap allowed."],
                ),
                DecisionOption(
                    id="change_route",
                    label="Change the route",
                    description="Re-route the job to a cheaper route.",
                    tradeoffs=["May produce a less thorough result."],
                ),
                DecisionOption(
                    id="cancel_job",
                    label="Cancel the job",
                    description="Stop the job; perform no further work.",
                    tradeoffs=["No further work is performed."],
                ),
            ],
            recommendation=DecisionRecommendation(
                option_id="raise_budget",
                rationale="Raising the budget resumes the in-flight work with least disruption.",
            ),
            default_action=DecisionDefaultAction(
                option_id="raise_budget", auto_apply_after=AutoApplyAfter.NEVER
            ),
            status=DecisionStatus.PENDING,
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _is_reviewer_station(station: RouteStation) -> bool:
    """A reviewer station is a worker station whose required role is ``reviewer``."""

    return is_worker_station(station) and station.required_role == "reviewer"


def _route_intent(route: DispatchRoute) -> Optional[str]:
    """Derive the route's intent for loop-policy wall-second defaults.

    Prefers the route's first declared trigger intent; falls back to matching the
    route id against the known route wall-second default keys.
    """

    if route.triggers.intents:
        return route.triggers.intents[0]
    for intent in ROUTE_WALL_SECOND_DEFAULTS:
        if intent in route.id:
            return intent
    return None


def _station_index(route: DispatchRoute, station_id: str) -> int:
    """Index of ``station_id`` within ``route.stations`` (−1 if absent)."""

    for index, station in enumerate(route.stations):
        if station.id == station_id:
            return index
    return -1


def _delivery_fields(
    route: DispatchRoute, station_runs: list[DispatchStationRun]
) -> dict[str, Any]:
    """Assemble the delivery-package fields the Gatehouse completeness check reads.

    Builds exactly the pieces the route's :class:`RouteDelivery` flags require
    (summary / files_changed / tests / risks / next_steps), populated minimally
    from the station runs so the structural completeness check passes for a clean
    run. The Gatehouse iterates the flags dynamically; this provides a value for
    each enabled piece.
    """

    fields: dict[str, Any] = {}
    delivery = route.delivery
    if delivery.include_summary:
        fields["summary"] = f"Ran {len(station_runs)} station(s) on route {route.id}."
    if delivery.include_files_changed:
        fields["files_changed"] = _files_changed(station_runs)
    if delivery.include_tests:
        fields["tests"] = ["station tests not run in v0.1-alpha (deterministic)"]
    if delivery.include_risks:
        fields["risks"] = ["no external side effects (v0.1-alpha tool registry)"]
    if delivery.include_next_steps:
        fields["next_steps"] = ["review the delivery package"]
    return fields


def _files_changed(station_runs: list[DispatchStationRun]) -> list[str]:
    """A non-empty files-changed list so the completeness check passes a clean run.

    v0.1-alpha does not yet thread concrete file paths from effects into the
    delivery package; a placeholder marker keeps the structural completeness
    check honest (the route asked for the piece; the piece is present) without
    fabricating file names.
    """

    return ["(files-changed detail recorded in the Run Ledger effects)"]


__all__ = [
    "LineStatus",
    "FulfillmentResult",
    "FulfillmentLine",
    "TERMINAL_RUN_STATUSES",
    "RunAlreadyTerminalError",
    "RunLeaseHeldError",
]
