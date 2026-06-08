"""DispatchManifest record (Standards Delta v0 §4.2)."""

from __future__ import annotations

from pydantic import Field

from .common import (
    AcceptanceCriterion,
    Constraint,
    Deliverable,
    DispatchBaseModel,
    ManifestPermissions,
    PathPolicy,
    QualityRequirements,
)
from .enums import ManifestStatus


class DispatchManifest(DispatchBaseModel):
    """Scoped work contract derived from a DispatchJob (Standards Delta v0 §4.2).

    ``path_policy`` is consumed by the ``apply_patch`` tool (round-6 §4.2) and
    always carries the four mandatory denied globs (see :class:`PathPolicy`).
    """

    id: str = Field(description='"manifest_<ulid>"')
    job_id: str
    route_id: str = Field(description='e.g. "route.code_task.v1"')
    goal: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    deliverables: list[Deliverable] = Field(default_factory=list)

    permissions: ManifestPermissions
    path_policy: PathPolicy = Field(default_factory=PathPolicy)
    quality_requirements: QualityRequirements

    status: ManifestStatus


__all__ = ["DispatchManifest"]
