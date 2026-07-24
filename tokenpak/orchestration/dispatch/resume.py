"""Resume contract — effect reconciliation for an interrupted run.

When a run is resumed, the FulfillmentLine runner asks :func:`reconcile_run` what
to do about the *last* station of the run. The four cases are:

1. **last station completed** → continue with the next station.
2. **last station running, NO effects** → mark ``failed_interrupted``; rerun the
   station with ``attempt_number+1``.
3. **last station running, APPLIED effects exist** → mark ``needs_recovery``; run
   effect reconciliation. For each applied effect compare the current workspace
   hash to ``after_hash``. All match → consistent, allow continue. Any drift →
   create a :class:`DispatchDecision` (accept / rollback-if-single-clean /
   rerun-from-clean / cancel).
4. **last station running, PLANNED effects exist** (started, never finalized) →
   mark ``needs_recovery``; reconcile. Current state matches ``after_hash`` →
   effect WAS applied (promote to applied, reconcile). Matches ``before_hash`` →
   NOT applied (safe to rerun). Neither → user decision required.

**Multi-effect auto-rollback is DISABLED in v0.1-alpha**:
the reconciler NEVER auto-rolls-back more than one effect — a drift across >1
effect always surfaces a :class:`DispatchDecision`, never a silent rollback. The
single-clean-effect rollback is offered only as a decision *option*, not applied
automatically.

This module is pure policy + filesystem *reads* (hashing the current workspace);
it applies no rollback and writes no records itself. The runner persists the
station-status transition and any decision it returns.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from .models.decision import (
    DecisionDefaultAction,
    DecisionOption,
    DecisionRecommendation,
    DispatchDecision,
)
from .models.effect import DispatchEffect
from .models.enums import (
    AutoApplyAfter,
    DecisionScope,
    DecisionStatus,
    EffectStatus,
    EffectTargetType,
    RiskLevel,
    StationRunStatus,
)
from .models.station_run import DispatchStationRun


def hash_workspace_file(workspace_root: Path | str, target: str) -> Optional[str]:
    """Return the ``sha256:`` hash of ``<workspace_root>/<target>``, or ``None``.

    ``None`` means the file does not currently exist (consistent with an effect
    whose target was deleted, or a create-effect that was never applied). Matches
    the ``apply_patch`` hash format (``sha256:`` prefix) so the reconciler can
    compare directly against an effect's ``before_hash`` / ``after_hash``.
    """

    path = Path(workspace_root) / target
    if not path.exists() or not path.is_file():
        return None
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Reconciliation result
# ---------------------------------------------------------------------------


class ResumeAction(str, Enum):
    """What the runner should do for the resumed run (the resume case outcome)."""

    CONTINUE_NEXT_STATION = "continue_next_station"  # case 1, or case 3 all-match
    RERUN_STATION = "rerun_station"  # case 2, or case 4 matches-before
    PROMOTE_AND_CONTINUE = "promote_and_continue"  # case 4 matches-after
    DECISION_REQUIRED = "decision_required"  # case 3 drift, case 4 unknown


@dataclass
class EffectReconciliation:
    """Per-effect reconciliation finding (which reconciliation branch the effect matched)."""

    effect_id: str
    target: str
    finding: str  # "matches_after" | "matches_before" | "drift" | "unknown"


@dataclass
class ResumeOutcome:
    """The reconciliation verdict for a resumed run.

    ``action`` is the runner's directive; ``station_status_transition`` is the
    new status the runner must persist on the interrupted station (None when the
    last station was already terminal — case 1). ``decision`` is set only for the
    ``DECISION_REQUIRED`` action. ``rerun_attempt_number`` is set for
    ``RERUN_STATION``. ``promote_effect_ids`` lists the planned effects to promote
    to ``applied`` for ``PROMOTE_AND_CONTINUE`` (case 4 matches-after).
    """

    action: ResumeAction
    station_status_transition: Optional[StationRunStatus] = None
    decision: Optional[DispatchDecision] = None
    rerun_attempt_number: Optional[int] = None
    promote_effect_ids: list[str] = field(default_factory=list)
    reconciliations: list[EffectReconciliation] = field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


def reconcile_run(
    *,
    station_runs: list[DispatchStationRun],
    effects_for_last_station: list[DispatchEffect],
    workspace_root: Path | str,
    now: Optional[datetime] = None,
) -> ResumeOutcome:
    """Reconcile the last station of a resumed run.

    ``station_runs`` is the run's station runs in execution order (the ledger's
    :meth:`RunLedger.read_station_runs_for_run`); ``effects_for_last_station`` is
    every effect recorded for the *last* station run (the ledger's
    :meth:`RunLedger.read_effects_for_station_run`). ``workspace_root`` is the
    repo the effects targeted (so the reconciler can hash the current file
    states).

    Returns a :class:`ResumeOutcome` directing the runner. Empty
    ``station_runs`` → start fresh (CONTINUE_NEXT_STATION, no transition).
    """

    created_at = now or datetime.now(timezone.utc)

    if not station_runs:
        # Nothing ran yet → start from the first station.
        return ResumeOutcome(
            action=ResumeAction.CONTINUE_NEXT_STATION,
            reason="No station runs exist; start from the first station.",
        )

    last = station_runs[-1]

    # --- Case 1: last station completed → continue with next station. -------
    if last.status is StationRunStatus.COMPLETED:
        return ResumeOutcome(
            action=ResumeAction.CONTINUE_NEXT_STATION,
            reason="Last station completed; continue with the next station.",
        )

    # The cases below all key off a station left in ``running`` (interrupted
    # mid-execution). A station already in another terminal/needs-recovery state
    # is handled as that state's natural resume (rerun a failed/cancelled station,
    # continue past a skipped one). Only ``running`` triggers reconciliation.
    if last.status is not StationRunStatus.RUNNING:
        # A previously-reconciled (needs_recovery) station, or a failed/cancelled
        # one, is rerun from a clean attempt. This is the conservative default.
        return ResumeOutcome(
            action=ResumeAction.RERUN_STATION,
            station_status_transition=StationRunStatus.FAILED_INTERRUPTED,
            rerun_attempt_number=last.attempt_number + 1,
            reason=(
                f"Last station status {last.status.value!r} is not 'running'; "
                "rerun the station from a clean attempt."
            ),
        )

    applied = [e for e in effects_for_last_station if e.status is EffectStatus.APPLIED]
    planned = [
        e
        for e in effects_for_last_station
        if e.status is EffectStatus.PLANNED and e.finalized_at is None
    ]

    # --- Case 2: running + NO effects → failed_interrupted; rerun. ----------
    if not applied and not planned:
        return ResumeOutcome(
            action=ResumeAction.RERUN_STATION,
            station_status_transition=StationRunStatus.FAILED_INTERRUPTED,
            rerun_attempt_number=last.attempt_number + 1,
            reason="Last station was running with no effects; rerun with attempt+1.",
        )

    # --- Case 4 takes precedence when planned (un-finalized) effects exist --
    # ("effect started but never finalized"). A run can carry both,
    # but an un-finalized planned effect is the more urgent, less-certain state.
    if planned:
        return _reconcile_planned(
            last=last,
            planned=planned,
            workspace_root=workspace_root,
            created_at=created_at,
        )

    # --- Case 3: running + APPLIED effects → needs_recovery; reconcile. -----
    return _reconcile_applied(
        last=last,
        applied=applied,
        workspace_root=workspace_root,
        created_at=created_at,
    )


def _reconcile_applied(
    *,
    last: DispatchStationRun,
    applied: list[DispatchEffect],
    workspace_root: Path | str,
    created_at: datetime,
) -> ResumeOutcome:
    """Case 3: compare each applied effect's after_hash to current state."""

    reconciliations: list[EffectReconciliation] = []
    drifted: list[DispatchEffect] = []
    for effect in applied:
        current = _current_hash(effect, workspace_root)
        if current == effect.after_hash:
            reconciliations.append(EffectReconciliation(effect.id, effect.target, "matches_after"))
        else:
            reconciliations.append(EffectReconciliation(effect.id, effect.target, "drift"))
            drifted.append(effect)

    if not drifted:
        # All applied effects match their after_hash → workspace consistent.
        return ResumeOutcome(
            action=ResumeAction.CONTINUE_NEXT_STATION,
            station_status_transition=StationRunStatus.NEEDS_RECOVERY,
            reconciliations=reconciliations,
            reason=(
                "All applied effects match their after_hash; workspace is "
                "consistent. Allow continue."
            ),
        )

    # Drift detected → DispatchDecision (case 3 a/b/c/d). The rollback option
    # (b) is offered ONLY when exactly one effect drifted and it is cleanly
    # revertible; multi-effect auto-rollback is DISABLED so it
    # is never an *applied* action — only a decision option, and only for a single
    # clean effect.
    single_clean = len(drifted) == 1 and drifted[0].rollback_available
    decision = _build_drift_decision(
        last=last,
        drifted=drifted,
        single_clean_rollback=single_clean,
        created_at=created_at,
    )
    return ResumeOutcome(
        action=ResumeAction.DECISION_REQUIRED,
        station_status_transition=StationRunStatus.NEEDS_RECOVERY,
        decision=decision,
        reconciliations=reconciliations,
        reason=(
            f"{len(drifted)} applied effect(s) drifted from their after_hash; a "
            "decision is required (auto multi-effect rollback is disabled)."
        ),
    )


def _reconcile_planned(
    *,
    last: DispatchStationRun,
    planned: list[DispatchEffect],
    workspace_root: Path | str,
    created_at: datetime,
) -> ResumeOutcome:
    """Case 4: a planned effect that was never finalized.

    For each planned effect: current == after_hash → it WAS applied (promote);
    current == before_hash → NOT applied (safe to rerun); neither → unknown.
    When the planned effects are unanimous the runner gets a clean directive;
    any mixed / unknown state surfaces a decision.
    """

    reconciliations: list[EffectReconciliation] = []
    findings: set[str] = set()
    promote_ids: list[str] = []
    for effect in planned:
        current = _current_hash(effect, workspace_root)
        if effect.after_hash is not None and current == effect.after_hash:
            finding = "matches_after"
            promote_ids.append(effect.id)
        elif current == effect.before_hash:
            finding = "matches_before"
        else:
            finding = "unknown"
        findings.add(finding)
        reconciliations.append(EffectReconciliation(effect.id, effect.target, finding))

    # Unanimous "was applied" → promote all + continue.
    if findings == {"matches_after"}:
        return ResumeOutcome(
            action=ResumeAction.PROMOTE_AND_CONTINUE,
            station_status_transition=StationRunStatus.NEEDS_RECOVERY,
            promote_effect_ids=promote_ids,
            reconciliations=reconciliations,
            reason="Planned effect(s) match after_hash; promote to applied and reconcile.",
        )

    # Unanimous "was NOT applied" → safe to rerun.
    if findings == {"matches_before"}:
        return ResumeOutcome(
            action=ResumeAction.RERUN_STATION,
            station_status_transition=StationRunStatus.NEEDS_RECOVERY,
            rerun_attempt_number=last.attempt_number + 1,
            reconciliations=reconciliations,
            reason="Planned effect(s) match before_hash; effect was not applied — safe to rerun.",
        )

    # Mixed or unknown → user decision required (partial / unknown state).
    decision = _build_planned_unknown_decision(last=last, planned=planned, created_at=created_at)
    return ResumeOutcome(
        action=ResumeAction.DECISION_REQUIRED,
        station_status_transition=StationRunStatus.NEEDS_RECOVERY,
        decision=decision,
        reconciliations=reconciliations,
        reason=(
            "Planned effect(s) are in a partial/unknown state (mixed hash match); "
            "a user decision is required."
        ),
    )


def _current_hash(effect: DispatchEffect, workspace_root: Path | str) -> Optional[str]:
    """Current workspace hash for an effect's target (file effects only).

    Non-file effects (command output) cannot be hash-reconciled; they return
    ``None`` (treated as drift / unknown, surfacing a decision rather than a
    silent assumption).
    """

    if effect.target_type is not EffectTargetType.FILE:
        return None
    return hash_workspace_file(workspace_root, effect.target)


# ---------------------------------------------------------------------------
# Decision builders
# ---------------------------------------------------------------------------


def _build_drift_decision(
    *,
    last: DispatchStationRun,
    drifted: list[DispatchEffect],
    single_clean_rollback: bool,
    created_at: datetime,
) -> DispatchDecision:
    """Build the case-3 drift decision (accept / rollback? / rerun / cancel)."""

    options = [
        DecisionOption(
            id="accept_current_state",
            label="Accept current state and continue",
            description="Treat the drifted workspace as authoritative and continue.",
            tradeoffs=["The drift is accepted without reverting it."],
        ),
    ]
    if single_clean_rollback:
        # Option (b) is offered ONLY for a single clean effect. Even then
        # it is a user-selectable option, never auto-applied — multi-effect
        # auto-rollback is disabled.
        options.append(
            DecisionOption(
                id="rollback_single_clean_effect",
                label="Roll back the single drifted effect",
                description=(
                    "Revert the one cleanly-revertible effect to its before-state. "
                    "Offered only because exactly one effect drifted and it is "
                    "cleanly revertible."
                ),
                tradeoffs=["Discards the drifted change for that one file."],
            )
        )
    options.extend(
        [
            DecisionOption(
                id="rerun_from_clean_state",
                label="Rerun the station from a clean state",
                description="Discard the partial work and rerun the station.",
                tradeoffs=["Repeats the station's LLM cost."],
            ),
            DecisionOption(
                id="cancel_job",
                label="Cancel the job",
                description="Stop the job; perform no further work.",
                tradeoffs=["No further work is performed."],
            ),
        ]
    )
    rollback_note = (
        " A single-effect rollback option is offered."
        if single_clean_rollback
        else " Automatic multi-effect rollback is disabled, so no rollback option "
        "is offered for this multi-effect drift."
    )
    return DispatchDecision(
        id=f"decision_{last.id}_resume_drift",
        job_id=last.run_id,
        created_at=created_at,
        scope=DecisionScope.STATION,
        title="Workspace drift detected on resume",
        question=(
            "Applied effects no longer match the workspace they recorded. Choose "
            "how to reconcile." + rollback_note
        ),
        reason=(
            "Resume reconciliation found drift between "
            f"{len(drifted)} applied effect(s) and the current workspace. "
            "Automatic multi-effect rollback is disabled."
        ),
        risk_level=RiskLevel.HIGH,
        options=options,
        recommendation=DecisionRecommendation(
            option_id="rerun_from_clean_state",
            rationale="Rerunning from a clean state is the safest deterministic path.",
        ),
        default_action=DecisionDefaultAction(
            option_id="rerun_from_clean_state",
            auto_apply_after=AutoApplyAfter.NEVER,
        ),
        status=DecisionStatus.PENDING,
    )


def _build_planned_unknown_decision(
    *,
    last: DispatchStationRun,
    planned: list[DispatchEffect],
    created_at: datetime,
) -> DispatchDecision:
    """Build the case-4 partial/unknown-state decision."""

    return DispatchDecision(
        id=f"decision_{last.id}_resume_planned_unknown",
        job_id=last.run_id,
        created_at=created_at,
        scope=DecisionScope.STATION,
        title="Interrupted effect in an unknown state on resume",
        question=(
            "A planned effect was started but never finalized, and the workspace "
            "matches neither its before-state nor its after-state. Choose how to "
            "proceed."
        ),
        reason=(
            "Resume reconciliation found a "
            f"planned-but-unfinalized effect among {len(planned)} effect(s) in a "
            "partial/unknown state; a user decision is required."
        ),
        risk_level=RiskLevel.HIGH,
        options=[
            DecisionOption(
                id="rerun_from_clean_state",
                label="Rerun the station from a clean state",
                description="Discard the partial effect and rerun the station.",
                tradeoffs=["Repeats the station's LLM cost; assumes no good partial work."],
            ),
            DecisionOption(
                id="inspect_manually",
                label="Inspect the partial state manually",
                description="Pause for manual inspection before any further action.",
                tradeoffs=["Requires manual operator intervention."],
            ),
            DecisionOption(
                id="cancel_job",
                label="Cancel the job",
                description="Stop the job; perform no further work.",
                tradeoffs=["No further work is performed."],
            ),
        ],
        recommendation=DecisionRecommendation(
            option_id="inspect_manually",
            rationale="An unknown partial state warrants inspection before automated action.",
        ),
        default_action=DecisionDefaultAction(
            option_id="inspect_manually",
            auto_apply_after=AutoApplyAfter.NEVER,
        ),
        status=DecisionStatus.PENDING,
    )


__all__ = [
    "hash_workspace_file",
    "ResumeAction",
    "EffectReconciliation",
    "ResumeOutcome",
    "reconcile_run",
]
