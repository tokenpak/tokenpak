"""StationRunner — bounded per-station loop execution (Standards Delta v0 §5.4–§5.8).

The :class:`StationRunner` executes a single route station. It wires together the
foundation pieces P-EXEC-01 orchestrates:

* the **worker** (+ optional prompt **overlay**) resolved from the registries by
  the FulfillmentLine runner and handed in already-bound;
* the **context cargo** assembled by a
  :class:`~tokenpak.orchestration.dispatch.context.provider.ContextProvider`
  (the §5.9 ``build_context`` call);
* the **tool registry** authorization layer
  (:func:`~tokenpak.orchestration.dispatch.tools.authorize_tool_call`), enforced
  at invocation time on every tool the worker requests;
* the **Run Ledger** (the per-station run record is committed *only after* its
  schema-valid output is written — acceptance criterion 4).

The worker "thinking" call goes through TIP via the injected :class:`WorkerLLM`
boundary (mirroring the FrontDock ``TipClient`` / Reviewer ``ReviewerLLM``
pattern): **no provider SDK is imported here**, and tests inject a deterministic
mock. The runner runs a **bounded loop** per a resolved
:class:`~tokenpak.orchestration.dispatch.models.common.StationLoopPolicy`
(precedence resolved by :mod:`.loop_policy`), stopping on the EXACT §5.4
``stop_when`` set.

Spend Guard inheritance (§8): the per-station token budget flows through TIP. The
runner models the live Spend-Guard cap as an injected callable
(:class:`SpendGuard`) so it is deterministic in tests; a hard-stop mid-station
surfaces as a ``station_failure`` with ``reason=spend_guard_exceeded`` (the
FulfillmentLine runner turns that into a :class:`DispatchDecision`, §8).

Cancellation (§5.6): a cancel token checked before each iteration propagates as
the ``cancel_requested`` stop condition; a TIP result that arrives *after* a
cancel is captured as a :class:`LateResult` with ``effects_applied=false`` and no
effects are applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, runtime_checkable
from uuid import uuid4

from .context.provider import ContextBundle, ContextProvider
from .ledger.db import RunLedger
from .loop_policy import (
    LoopOutcome,
    LoopState,
    evaluate_stop,
    resolve_loop_policy,
)
from .models.common import StationLoopPolicy
from .models.enums import (
    AutonomyMode,
    LoopStopCondition,
    StationRunStatus,
)
from .models.late_result import LateResult
from .models.manifest import DispatchManifest
from .models.route import RouteStation
from .models.station_run import DispatchStationRun
from .models.worker import DispatchWorker
from .registry.workers import PromptOverlay, compose_prompt
from .tools import (
    ApprovalRequiredError,
    ToolName,
    ToolPolicyViolation,
    authorize_tool_call,
)

# Result-schema version the runner stamps onto a completed station run. The
# worker output is a free-form mapping in v0.1-alpha (the concrete station_result
# schema is a later concern); the version string is what the Gatehouse
# ``station_output_schema`` check keys its validators off.
STATION_RESULT_SCHEMA_VERSION = "station_result.v1"

# Reason string for a Spend-Guard hard stop (Standards Delta v0 §8). The
# FulfillmentLine runner matches on this exact string to raise the §8 decision.
SPEND_GUARD_EXCEEDED_REASON = "spend_guard_exceeded"


# ---------------------------------------------------------------------------
# Injected boundaries (TIP worker call, Spend Guard, cancellation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerToolRequest:
    """A tool the worker asked the runner to invoke this iteration.

    ``tool`` is the registry tool name; ``args`` is an opaque argument mapping
    the runner passes to the tool callable. The runner authorizes the call
    against the autonomy × tool matrix (§5.3) *before* invoking it; an
    authorization failure ends the loop with ``tool_policy_violation``.
    """

    tool: ToolName | str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerTurn:
    """One TIP worker "thinking" turn (the injected :class:`WorkerLLM` output).

    A turn either:

    * requests one or more tool calls (``tool_requests`` non-empty) — the runner
      runs them and loops again; or
    * emits a schema-valid ``result_payload`` (``output_schema_valid=True``) —
      the success exit; or
    * signals a fatal error (``fatal_error=True``) — the loop stops with
      ``fatal_error``.

    ``tokens_used`` is the iteration's token spend, debited against the Spend
    Guard cap (§8). It defaults to 0 so a pure no-op turn is free.
    """

    tool_requests: tuple[WorkerToolRequest, ...] = ()
    result_payload: Optional[dict[str, Any]] = None
    output_schema_valid: bool = False
    fatal_error: bool = False
    tokens_used: int = 0
    wall_seconds: int = 0


@runtime_checkable
class WorkerLLM(Protocol):
    """Injected TIP worker boundary — routes through TIP at runtime (§5.1, §8).

    Mirrors the FrontDock ``TipClient`` / Reviewer ``ReviewerLLM`` contracts: in
    production this is the TIP worker invocation (Spend Guard enforced); in tests
    it is a deterministic mock. **No provider SDK is imported or called by this
    module.** :meth:`run_turn` is called once per loop iteration with the
    composed prompt, the assembled context bundle, and the tool outputs from the
    previous iteration; it returns a :class:`WorkerTurn`.
    """

    def run_turn(
        self,
        *,
        prompt: list[str],
        context: ContextBundle,
        prior_tool_outputs: list[Any],
        iteration: int,
    ) -> WorkerTurn:
        """Return the worker's turn for this loop iteration."""
        ...


# A Spend-Guard cap source: returns the remaining token budget for the station.
# A return value <= 0 means the cap is exhausted (hard stop, §8). Modeled as a
# callable so it is deterministic in tests and the live cap can flow from TIP.
SpendGuard = Callable[[], int]


def unlimited_spend_guard() -> int:
    """A non-binding Spend Guard cap (effectively unlimited).

    Default when the runner is constructed without a Spend Guard: the per-station
    token budget is not enforced locally (TIP enforces it at runtime). Tests
    inject a binding cap to exercise the §8 hard-stop path.
    """

    return 1 << 62


@runtime_checkable
class CancelToken(Protocol):
    """Cancellation signal checked before each loop iteration (§5.6)."""

    def is_cancelled(self) -> bool:
        """Return True once cancellation has been requested for this job."""
        ...


class FlagCancelToken:
    """A trivial in-memory cancel token (deterministic in tests).

    The FulfillmentLine runner / CLI set :attr:`cancelled` when
    ``tokenpak dispatch cancel`` runs; the station runner checks it before each
    iteration (§5.6).
    """

    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self.cancelled


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------


@dataclass
class StationRunOutcome:
    """The result of running one station (returned by :meth:`StationRunner.run`).

    Carries the committed :class:`DispatchStationRun`, the §5.4 stop condition
    that ended the loop, any :class:`LateResult` captured on a post-cancel TIP
    result, the ids of effects recorded during the run, and a failure reason
    (set only on a failed run — e.g. :data:`SPEND_GUARD_EXCEEDED_REASON`).
    """

    station_run: DispatchStationRun
    stop_condition: Optional[LoopStopCondition]
    late_result: Optional[LateResult] = None
    effect_ids: list[str] = field(default_factory=list)
    failure_reason: Optional[str] = None

    @property
    def completed(self) -> bool:
        return self.station_run.status is StationRunStatus.COMPLETED

    @property
    def cancelled(self) -> bool:
        return self.station_run.status is StationRunStatus.CANCELLED


# ---------------------------------------------------------------------------
# StationRunner
# ---------------------------------------------------------------------------


class StationRunner:
    """Runs one route station's bounded loop (Standards Delta v0 §5.4–§5.8).

    Construct with the injected :class:`WorkerLLM` (the TIP boundary), a
    :class:`ContextProvider`, a :class:`RunLedger`, and optional Spend Guard /
    cancel token. Call :meth:`run` with the run id, the manifest, the route
    station, the bound worker (+ overlay), and the autonomy mode.

    The station-run record is committed to the ledger **only after** its
    schema-valid output is written (acceptance criterion 4): a successful run
    writes the ``completed`` record exactly once, atomically, after the loop
    produced a valid payload; a failed / cancelled run writes its terminal record
    once the loop ends. Effects recorded mid-loop (via tool callables) are written
    through the ledger's §4.8 lifecycle as they happen, independent of the
    station-run commit.
    """

    def __init__(
        self,
        *,
        worker_llm: WorkerLLM,
        context_provider: ContextProvider,
        ledger: RunLedger,
        spend_guard: Optional[SpendGuard] = None,
        cancel_token: Optional[CancelToken] = None,
        tool_runner: Optional[Callable[[WorkerToolRequest], Any]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._worker_llm = worker_llm
        self._context = context_provider
        self._ledger = ledger
        self._spend_guard = spend_guard or unlimited_spend_guard
        self._cancel = cancel_token or FlagCancelToken(False)
        # ``tool_runner`` actually invokes a tool the worker requested (returning
        # an opaque output the next turn can read). Default is a no-op that does
        # nothing but record the call — concrete effect-bearing tools are wired by
        # the caller (the FulfillmentLine runner / CLI). It must NOT be called for
        # an unauthorized tool — the runner gates first.
        self._tool_runner = tool_runner or (lambda request: None)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        *,
        run_id: str,
        manifest: DispatchManifest,
        station: RouteStation,
        worker: DispatchWorker,
        autonomy_mode: AutonomyMode | str,
        overlay: Optional[PromptOverlay] = None,
        route_intent: Optional[str] = None,
        station_run_id: Optional[str] = None,
        attempt_number: int = 1,
        approval_granted: bool = False,
    ) -> StationRunOutcome:
        """Execute one station's bounded loop and commit its terminal record.

        Steps: resolve the effective loop policy (§5.4 precedence) → build the
        context bundle (§5.9) → compose the worker+overlay prompt (§16) → run the
        bounded loop (worker turn → tool authorization+invocation → stop-condition
        check) → write the terminal :class:`DispatchStationRun` (``completed``
        only after a schema-valid payload exists — criterion 4).
        """

        mode = autonomy_mode if isinstance(autonomy_mode, AutonomyMode) else AutonomyMode(autonomy_mode)
        sr_id = station_run_id or f"stationrun_{uuid4().hex}"

        policy = resolve_loop_policy(
            station_override=station.loop_policy,
            worker_default=worker.default_loop_policy,
            route_intent=route_intent,
        )

        prompt = compose_prompt(worker, overlay)

        # Cancellation can already be requested before the station even starts:
        # mark the station cancelled (a queued station that never ran), §5.6.
        if self._cancel.is_cancelled():
            return self._commit_terminal(
                sr_id=sr_id,
                run_id=run_id,
                station=station,
                worker=worker,
                overlay=overlay,
                context_bundle_id="(not-built: cancelled before start)",
                status=StationRunStatus.CANCELLED,
                stop_condition=LoopStopCondition.CANCEL_REQUESTED,
                iteration_count=0,
                tool_call_count=0,
                wall_seconds=0,
                result_payload=None,
                attempt_number=attempt_number,
                tip_request_ids=[],
            )

        context = self._context.build_context(manifest, station)

        return self._run_loop(
            sr_id=sr_id,
            run_id=run_id,
            manifest=manifest,
            station=station,
            worker=worker,
            overlay=overlay,
            prompt=prompt,
            context=context,
            policy=policy,
            mode=mode,
            attempt_number=attempt_number,
            approval_granted=approval_granted,
        )

    # -- the bounded loop ----------------------------------------------------

    def _run_loop(
        self,
        *,
        sr_id: str,
        run_id: str,
        manifest: DispatchManifest,
        station: RouteStation,
        worker: DispatchWorker,
        overlay: Optional[PromptOverlay],
        prompt: list[str],
        context: ContextBundle,
        policy: StationLoopPolicy,
        mode: AutonomyMode,
        attempt_number: int,
        approval_granted: bool,
    ) -> StationRunOutcome:
        iteration = 0
        tool_calls = 0
        wall_seconds = 0
        tip_request_ids: list[str] = []
        effect_ids: list[str] = []
        prior_tool_outputs: list[Any] = []
        result_payload: Optional[dict[str, Any]] = None
        late_result: Optional[LateResult] = None
        failure_reason: Optional[str] = None

        while True:
            # --- cancellation check, BEFORE the (paid) worker turn (§5.6) ----
            if self._cancel.is_cancelled():
                outcome = LoopOutcome(
                    stop_condition=LoopStopCondition.CANCEL_REQUESTED,
                    exhausted=False,
                    produced_valid_output=result_payload is not None,
                )
                status = StationRunStatus.CANCELLED
                break

            # --- Spend Guard check, BEFORE the worker turn (§8) --------------
            if self._spend_guard() <= 0:
                failure_reason = SPEND_GUARD_EXCEEDED_REASON
                outcome = LoopOutcome(
                    stop_condition=LoopStopCondition.FATAL_ERROR,
                    exhausted=False,
                    produced_valid_output=False,
                )
                status = StationRunStatus.FAILED
                break

            # --- one worker "thinking" turn through TIP ----------------------
            iteration += 1
            turn = self._worker_llm.run_turn(
                prompt=prompt,
                context=context,
                prior_tool_outputs=prior_tool_outputs,
                iteration=iteration,
            )
            tip_request_ids.append(f"tip_{run_id}_{sr_id}_{iteration}")
            wall_seconds += max(0, turn.wall_seconds)

            # Spend debit for this turn flows through the injected Spend Guard
            # (§8): the guard owns the running token total and reports the
            # remaining cap, which the NEXT iteration's pre-turn check reads. The
            # turn reports its spend (``turn.tokens_used``) for that bookkeeping;
            # the runner does not enforce the cap itself — TIP / the guard does.

            # --- if the turn arrived after a cancel, capture a LateResult ----
            # (defensive: covers an adapter that does not support hard-kill, so
            #  the turn completed even though cancellation was requested — §5.6.)
            if self._cancel.is_cancelled():
                late_result = self._capture_late_result(
                    job_id=manifest.job_id, station_run_id=sr_id, turn=turn
                )
                outcome = LoopOutcome(
                    stop_condition=LoopStopCondition.CANCEL_REQUESTED,
                    exhausted=False,
                    produced_valid_output=False,
                )
                status = StationRunStatus.CANCELLED
                break

            # --- fatal error from the worker ---------------------------------
            if turn.fatal_error:
                outcome = LoopOutcome(
                    stop_condition=LoopStopCondition.FATAL_ERROR,
                    exhausted=False,
                    produced_valid_output=False,
                )
                status = StationRunStatus.FAILED
                break

            # --- authorize + invoke any requested tool calls (§5.3) ----------
            tool_violation = False
            for request in turn.tool_requests:
                try:
                    authorize_tool_call(
                        request.tool, mode, approval_granted=approval_granted
                    )
                except (ToolPolicyViolation, ApprovalRequiredError):
                    tool_violation = True
                    break
                tool_calls += 1
                output = self._tool_runner(request)
                prior_tool_outputs.append(output)
                # A tool callable that returns a DispatchEffect (or an object with
                # an ``effect`` attribute) contributes an effect id to the run.
                effect_id = _effect_id_of(output)
                if effect_id is not None:
                    effect_ids.append(effect_id)

            if tool_violation:
                outcome = LoopOutcome(
                    stop_condition=LoopStopCondition.TOOL_POLICY_VIOLATION,
                    exhausted=False,
                    produced_valid_output=False,
                )
                status = StationRunStatus.FAILED
                break

            # --- record this turn's schema-valid output (if any) -------------
            if turn.output_schema_valid and turn.result_payload is not None:
                result_payload = dict(turn.result_payload)

            # --- evaluate the §5.4 stop conditions ---------------------------
            state = LoopState(
                iteration_count=iteration,
                tool_call_count=tool_calls,
                wall_seconds=wall_seconds,
                output_schema_valid=result_payload is not None,
                pending_tool_requests=False,  # tool requests were satisfied above
                cancel_requested=False,
                tool_policy_violation=False,
                fatal_error=False,
            )
            outcome = evaluate_stop(state, policy)
            if outcome.should_stop:
                if outcome.stop_condition is LoopStopCondition.OUTPUT_SCHEMA_VALID_AND_NO_PENDING_TOOL_REQUESTS:
                    status = StationRunStatus.COMPLETED
                elif outcome.exhausted:
                    # Budget exhausted: §5.4 on_exhausted → mark_failed (the
                    # create_reviewer_note / block_delivery actions are downstream
                    # Gatehouse concerns, surfaced by the failed status).
                    status = StationRunStatus.FAILED
                else:
                    status = StationRunStatus.FAILED
                break
            # else: keep looping.

        return self._commit_terminal(
            sr_id=sr_id,
            run_id=run_id,
            station=station,
            worker=worker,
            overlay=overlay,
            context_bundle_id=_context_bundle_id(context),
            status=status,
            stop_condition=outcome.stop_condition,
            iteration_count=iteration,
            tool_call_count=tool_calls,
            wall_seconds=wall_seconds,
            result_payload=result_payload if status is StationRunStatus.COMPLETED else None,
            attempt_number=attempt_number,
            tip_request_ids=tip_request_ids,
            late_result=late_result,
            effect_ids=effect_ids,
            failure_reason=failure_reason,
        )

    # NOTE: _commit_terminal threads ``failure_reason`` through explicitly (no
    # module-level mutable state) so the sequential runner stays re-entrant.

    # -- record commit (criterion 4: commit only after valid output) ---------

    def _commit_terminal(
        self,
        *,
        sr_id: str,
        run_id: str,
        station: RouteStation,
        worker: DispatchWorker,
        overlay: Optional[PromptOverlay],
        context_bundle_id: str,
        status: StationRunStatus,
        stop_condition: Optional[LoopStopCondition],
        iteration_count: int,
        tool_call_count: int,
        wall_seconds: int,
        result_payload: Optional[dict[str, Any]],
        attempt_number: int,
        tip_request_ids: list[str],
        late_result: Optional[LateResult] = None,
        effect_ids: Optional[list[str]] = None,
        failure_reason: Optional[str] = None,
    ) -> StationRunOutcome:
        """Build + atomically persist the terminal DispatchStationRun.

        For a ``completed`` status the record carries the schema-valid
        ``result_payload``; the commit happens here, AFTER the payload is known
        (acceptance criterion 4). For any non-completed terminal status the
        payload is ``None`` (the §4.5 schema permits a null payload on a failed /
        cancelled run).
        """

        station_run = DispatchStationRun(
            id=sr_id,
            run_id=run_id,
            station_id=station.id,
            worker_id=worker.id,
            prompt_overlay_id=overlay.id if overlay is not None else None,
            context_bundle_id=context_bundle_id,
            tip_request_ids=list(tip_request_ids),
            status=status,
            iteration_count=iteration_count,
            tool_call_count=tool_call_count,
            wall_seconds=wall_seconds,
            result_payload=result_payload,
            result_schema_version=STATION_RESULT_SCHEMA_VERSION,
            attempt_number=attempt_number,
        )
        # The record is schema-valid by construction (pydantic validated it on
        # build); write it atomically. The ledger's _insert is a single
        # transaction (acceptance criterion 4).
        self._ledger.write_station_run(station_run)
        if late_result is not None:
            self._ledger.write_late_result(late_result)

        return StationRunOutcome(
            station_run=station_run,
            stop_condition=stop_condition,
            late_result=late_result,
            effect_ids=list(effect_ids or []),
            failure_reason=failure_reason if status is StationRunStatus.FAILED else None,
        )

    # -- late result capture (§5.6) -----------------------------------------

    def _capture_late_result(
        self,
        *,
        job_id: str,
        station_run_id: str,
        turn: WorkerTurn,
    ) -> LateResult:
        """Capture a post-cancel TIP result as a LateResult (effects_applied=False).

        Per §5.6 step 5: a TIP output that arrives after cancellation is recorded
        as a :class:`LateResult` with ``effects_applied=false`` and NO effects are
        applied. ``recovery_allowed`` is False (v0.1-alpha is inspect-only).
        """

        import hashlib
        import json

        payload = turn.result_payload or {}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return LateResult(
            id=f"late_{uuid4().hex}",
            job_id=job_id,
            station_run_id=station_run_id,
            received_at=self._clock(),
            result_hash=f"sha256:{digest}",
            stored_artifact_id=None,
            effects_applied=False,
            recovery_allowed=False,
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _effect_id_of(output: Any) -> Optional[str]:
    """Extract a DispatchEffect id from a tool output, if it carries one."""

    if output is None:
        return None
    # An ApplyPatchResult / RunCommandResult carries ``.effect``; a bare effect
    # carries ``.id`` + ``.status``.
    effect = getattr(output, "effect", output)
    if effect is None:
        return None
    eid = getattr(effect, "id", None)
    # Only count it if it actually looks like an effect (has a status).
    if eid is not None and getattr(effect, "status", None) is not None:
        return str(eid)
    return None


def _context_bundle_id(context: ContextBundle) -> str:
    """Derive a stable id for a context bundle (manifest+station+content hash).

    The :class:`ContextBundle` has no ``id`` field; the runner derives a
    deterministic one from the manifest/station ids and the included file hashes
    so the station-run record can reference exactly the cargo it ran on.
    """

    import hashlib

    parts = [context.manifest_id, context.station_id]
    parts.extend(f"{f.path}:{f.sha256}" for f in context.files)
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"ctx_{context.manifest_id}_{context.station_id}_{digest}"


__all__ = [
    "STATION_RESULT_SCHEMA_VERSION",
    "SPEND_GUARD_EXCEEDED_REASON",
    "WorkerToolRequest",
    "WorkerTurn",
    "WorkerLLM",
    "SpendGuard",
    "unlimited_spend_guard",
    "CancelToken",
    "FlagCancelToken",
    "StationRunOutcome",
    "StationRunner",
]
