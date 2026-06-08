"""DispatchPolicy record (Standards Delta v0 §2/§4 record list — SKETCH).

``DispatchPolicy`` appears in the §2 record vocabulary and §4 record list but
has no full field schema in the Standards Delta ("sketch needed"). This is a
faithful **sketch**: a named, reusable policy bundle composed of the same
permission / path / quality primitives the DispatchManifest already defines
(§4.2), so a manifest can reference a named policy instead of inlining one.
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
