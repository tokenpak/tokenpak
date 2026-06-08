"""Enumerations for TokenPak Dispatch record schemas.

Every enum here is transcribed verbatim from Standards Delta v0
(``01_PROJECTS/tokenpak/dispatch-2026-05-19/standards-delta-v0.md``) §4/§5/§6.
The enum *values* are the authoritative strings; member names are sanitized
upper-case forms. Do NOT add, drop, or rename members without a corresponding
Standards Delta amendment — these are contract enums, not implementation
conveniences (Std 41 §1.2 applies to task packets; Dispatch records carry
their own execution-tier state space per Standards Delta v0 §6).
"""

from __future__ import annotations

from enum import Enum


class AutonomyMode(str, Enum):
    """DispatchJob / DispatchManifest autonomy mode (Standards Delta v0 §4.1)."""

    ADVISORY = "advisory"
    DRAFT = "draft"
    DISPATCH_WITH_APPROVAL = "dispatch_with_approval"
    AUTO_DISPATCH_LIMITED = "auto_dispatch_limited"


class DispatchJobStatus(str, Enum):
    """DispatchJob execution-tier state machine (Standards Delta v0 §4.1 + §6).

    Terminal states per §6: ``delivered``, ``cancelled``, ``failed``,
    ``withdrawn``. These do NOT map onto the Std 41 task-packet status enum;
    the crosswalk in §6 applies only when ``source_task_packet_id`` is set.
    """

    DRAFT = "draft"
    MANIFEST_READY = "manifest_ready"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    GATE_REVIEW = "gate_review"
    BLOCKED = "blocked"
    REPAIRING = "repairing"
    DELIVERY_READY = "delivery_ready"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


class ManifestStatus(str, Enum):
    """DispatchManifest lifecycle status (Standards Delta v0 §4.2)."""

    DRAFT = "draft"
    NEEDS_DECISION = "needs_decision"
    APPROVED = "approved"
    ACTIVE = "active"


class RiskLevel(str, Enum):
    """Shared risk level (Standards Delta v0 §4.3 default_risk, §4.6 risk_level)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StationRunStatus(str, Enum):
    """DispatchStationRun status (Standards Delta v0 §4.5).

    Exact 9-member enum; required by P-SCHEMA-01 acceptance criteria to match
    the Standards Delta §4.5 list verbatim.
    """

    QUEUED = "queued"
    CONTEXT_READY = "context_ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILED_INTERRUPTED = "failed_interrupted"
    NEEDS_RECOVERY = "needs_recovery"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class DecisionScope(str, Enum):
    """DispatchDecision scope (Standards Delta v0 §4.6, v0.1-alpha)."""

    JOB = "job"
    STATION = "station"


class DecisionStatus(str, Enum):
    """DispatchDecision status (Standards Delta v0 §4.6)."""

    PENDING = "pending"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class AutoApplyAfter(str, Enum):
    """DispatchDecision default_action.auto_apply_after (Standards Delta v0 §4.6).

    v0.1-alpha only ever uses ``never``; ``timeout`` / ``user_preference`` are
    reserved for later versions.
    """

    NEVER = "never"
    TIMEOUT = "timeout"
    USER_PREFERENCE = "user_preference"


class ResolvedBy(str, Enum):
    """DispatchDecision resolution.resolved_by (Standards Delta v0 §4.6)."""

    USER = "user"
    SYSTEM = "system"


class EffectTargetType(str, Enum):
    """DispatchEffect target_type (Standards Delta v0 §4.8)."""

    FILE = "file"
    COMMAND_OUTPUT = "command_output"
    ARTIFACT = "artifact"


class RollbackBehavior(str, Enum):
    """DispatchEffect rollback_behavior (Standards Delta v0 §4.8)."""

    DELETE_FILE_IF_AFTER_HASH_MATCHES = "delete_file_if_after_hash_matches"
    RESTORE_BEFORE_CONTENT_IF_CURRENT_HASH_MATCHES_AFTER_HASH = (
        "restore_before_content_if_current_hash_matches_after_hash"
    )
    RESTORE_BEFORE_CONTENT = "restore_before_content"
    MANUAL_ONLY = "manual_only"


class EffectStatus(str, Enum):
    """DispatchEffect status (Standards Delta v0 §4.8)."""

    PLANNED = "planned"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    NEEDS_RECOVERY = "needs_recovery"
    NEEDS_MANUAL_RECOVERY = "needs_manual_recovery"


class ModifyFilesPolicy(str, Enum):
    """DispatchWorker permission_profile.modify_files (Standards Delta v0 §5.1)."""

    ALWAYS = "always"
    POLICY_CONTROLLED = "policy_controlled"
    NEVER = "never"


class RunCommandsPolicy(str, Enum):
    """DispatchWorker permission_profile.run_commands (Standards Delta v0 §5.1)."""

    ALWAYS = "always"
    POLICY_CONTROLLED = "policy_controlled"
    NEVER = "never"


class LoopStopCondition(str, Enum):
    """StationLoopPolicy.stop_when closed enum (Standards Delta v0 §5.4).

    ``station_goal_satisfied`` was removed per round-6 §4.5 and is deliberately
    absent.
    """

    OUTPUT_SCHEMA_VALID_AND_NO_PENDING_TOOL_REQUESTS = (
        "output_schema_valid AND no_pending_tool_requests"
    )
    LOOP_BUDGET_EXHAUSTED = "loop_budget_exhausted"
    CANCEL_REQUESTED = "cancel_requested"
    TOOL_POLICY_VIOLATION = "tool_policy_violation"
    FATAL_ERROR = "fatal_error"


class LoopOnExhausted(str, Enum):
    """StationLoopPolicy.on_exhausted actions (Standards Delta v0 §5.4)."""

    MARK_FAILED = "mark_failed"
    CREATE_REVIEWER_NOTE = "create_reviewer_note"
    BLOCK_DELIVERY = "block_delivery"


# ---------------------------------------------------------------------------
# Reviewer Station I/O enums (Standards Delta v0 §5.7)
# ---------------------------------------------------------------------------


class ReviewerStatus(str, Enum):
    """ReviewerStationResult.status (Standards Delta v0 §5.7).

    The top-level semantic verdict. The Reviewer→Gatehouse handoff table (§5.7)
    keys entirely off this value; ``delivery_recommendation.status`` is DERIVED
    from it.
    """

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class CriterionStatus(str, Enum):
    """ReviewerStationResult.criteria_results[].status (Standards Delta v0 §5.7)."""

    PASS = "pass"
    FAIL = "fail"
    UNCLEAR = "unclear"


class FixSeverity(str, Enum):
    """ReviewerStationResult.required_fixes[].severity (Standards Delta v0 §5.7)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SuggestedStation(str, Enum):
    """ReviewerStationResult.required_fixes[].suggested_station (§5.7).

    Which station should address the fix: a build re-run, a doc re-run, or a
    user decision.
    """

    BUILD = "build"
    DOC = "doc"
    USER_DECISION = "user_decision"


class DeliveryRecommendationStatus(str, Enum):
    """ReviewerStationResult.delivery_recommendation.status (§5.7).

    DERIVED from :class:`ReviewerStatus` (never authored independently):
    ``pass`` → ``ready``, ``warning`` → ``ready_with_warning``, ``fail`` →
    ``blocked``.
    """

    READY = "ready"
    BLOCKED = "blocked"
    READY_WITH_WARNING = "ready_with_warning"


__all__ = [
    "AutonomyMode",
    "DispatchJobStatus",
    "ManifestStatus",
    "RiskLevel",
    "StationRunStatus",
    "DecisionScope",
    "DecisionStatus",
    "AutoApplyAfter",
    "ResolvedBy",
    "EffectTargetType",
    "RollbackBehavior",
    "EffectStatus",
    "ModifyFilesPolicy",
    "RunCommandsPolicy",
    "LoopStopCondition",
    "LoopOnExhausted",
    "ReviewerStatus",
    "CriterionStatus",
    "FixSeverity",
    "SuggestedStation",
    "DeliveryRecommendationStatus",
]
