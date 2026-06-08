"""Shared / nested models for Dispatch records.

These are the supporting structures referenced by the twelve top-level
Dispatch records (Standards Delta v0 §4–§5). Where the Standards Delta names a
type but does not fully specify its fields (``AcceptanceCriterion``,
``Constraint``, ``Deliverable``), a minimal faithful shape is provided and
marked as a supporting sketch — these are NOT among the twelve canonical
records and may be expanded by a later packet without breaking the records that
embed them.

All Dispatch models forbid unknown fields (``extra="forbid"``) so the schemas
act as strict contracts: an unexpected key is a fail-loud validation error.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tokenpak.orchestration.dispatch.registry.capabilities import validate_capabilities

from .enums import (
    AutonomyMode,
    LoopOnExhausted,
    LoopStopCondition,
)

# Standards Delta v0 §4.2: denied_paths ALWAYS includes these four globs.
MANDATORY_DENIED_PATHS: tuple[str, ...] = (
    ".env",
    ".git/**",
    "secrets/**",
    "license/**",
)


class DispatchBaseModel(BaseModel):
    """Base for every Dispatch record/model: strict, unknown keys rejected."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Manifest sub-structures (Standards Delta v0 §4.2)
# ---------------------------------------------------------------------------


class AcceptanceCriterion(DispatchBaseModel):
    """Supporting sketch — referenced by DispatchManifest / Reviewer I/O.

    The Standards Delta references ``AcceptanceCriterion`` as a type but does
    not specify its fields; this minimal shape is the supporting sketch.
    """

    id: str
    description: str


class Constraint(DispatchBaseModel):
    """Supporting sketch — referenced by DispatchManifest / Reviewer I/O."""

    id: str
    description: str


class Deliverable(DispatchBaseModel):
    """Supporting sketch — referenced by DispatchManifest deliverables list."""

    id: str
    description: str


class PathPolicy(DispatchBaseModel):
    """DispatchManifest.path_policy — consumed by the apply_patch tool (§4.2).

    ``denied_paths`` is guaranteed to always contain the four mandatory globs
    (``.env``, ``.git/**``, ``secrets/**``, ``license/**``); any missing
    mandatory entry is injected at validation time so the safety invariant
    holds regardless of caller input.
    """

    allowed_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(
        default_factory=lambda: list(MANDATORY_DENIED_PATHS)
    )
    allow_new_files: bool = True
    allow_delete_files: bool = False

    @field_validator("denied_paths")
    @classmethod
    def _ensure_mandatory_denied(cls, value: list[str]) -> list[str]:
        merged = list(value)
        for mandatory in MANDATORY_DENIED_PATHS:
            if mandatory not in merged:
                merged.append(mandatory)
        return merged


class ManifestPermissions(DispatchBaseModel):
    """DispatchManifest.permissions block (Standards Delta v0 §4.2)."""

    autonomy_mode: AutonomyMode
    allowed_actions: list[str] = Field(default_factory=list)
    requires_approval_for: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)


class QualityRequirements(DispatchBaseModel):
    """DispatchManifest.quality_requirements block (Standards Delta v0 §4.2)."""

    test_required: bool
    review_required: bool
    docs_required: bool
    evidence_required: bool


# ---------------------------------------------------------------------------
# Station loop policy (Standards Delta v0 §5.4)
# ---------------------------------------------------------------------------


class StationLoopPolicy(DispatchBaseModel):
    """Loop budget + stop conditions for a station (Standards Delta v0 §5.4).

    Precedence (resolved by the runner, not this schema):
    ``station_override > route_default > worker_default > system_default``.
    System default per §5.4 is ``max_iterations: 2, max_tool_calls: 6,
    max_wall_seconds: 600`` — used as the field defaults here.
    """

    max_iterations: int = 2
    max_tool_calls: int = 6
    max_wall_seconds: int = 600
    stop_when: list[LoopStopCondition] = Field(
        default_factory=lambda: list(LoopStopCondition)
    )
    on_exhausted: list[LoopOnExhausted] = Field(
        default_factory=lambda: list(LoopOnExhausted)
    )


# ---------------------------------------------------------------------------
# Worker loop default + permission profile (Standards Delta v0 §5.1)
# ---------------------------------------------------------------------------


class WorkerLoopDefault(DispatchBaseModel):
    """DispatchWorker.default_loop_policy — the §5.1 three-field budget.

    Distinct from :class:`StationLoopPolicy`: §5.1 specifies only the three
    integer budget fields for a worker default; stop conditions live on the
    station-level policy.
    """

    max_iterations: int
    max_tool_calls: int
    max_wall_seconds: int


def _validate_capability_list(value: list[str]) -> list[str]:
    """Shared registry-bound capability validator (Standards Delta v0 §5.2)."""

    return validate_capabilities(value)


__all__ = [
    "MANDATORY_DENIED_PATHS",
    "DispatchBaseModel",
    "AcceptanceCriterion",
    "Constraint",
    "Deliverable",
    "PathPolicy",
    "ManifestPermissions",
    "QualityRequirements",
    "StationLoopPolicy",
    "WorkerLoopDefault",
]
