"""DispatchPolicy record (SKETCH).

``DispatchPolicy`` appears in the record vocabulary and record list but
has no full field schema yet ("sketch needed"). This is a
faithful **sketch**: a named, reusable policy bundle composed of the same
permission / path / quality primitives the DispatchManifest already defines,
so a manifest can reference a named policy instead of inlining one.
Expand via a later packet once the policy contract is specified.
"""

from __future__ import annotations

from pydantic import Field

from .common import (
    DispatchBaseModel,
    PathPolicy,
    QualityRequirements,
)
from .enums import AutonomyMode


class DispatchPolicy(DispatchBaseModel):
    """A named, reusable policy bundle (SKETCH — see module docstring)."""

    id: str = Field(description='"policy.<name>.v<n>" or "policy_<ulid>"')
    name: str
    description: str = ""

    autonomy_mode: AutonomyMode
    path_policy: PathPolicy = Field(default_factory=PathPolicy)
    quality_requirements: QualityRequirements | None = None

    allowed_actions: list[str] = Field(default_factory=list)
    requires_approval_for: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)


__all__ = ["DispatchPolicy"]
