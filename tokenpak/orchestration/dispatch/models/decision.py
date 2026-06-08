"""DispatchDecision record (Standards Delta v0 §4.6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .common import DispatchBaseModel
from .enums import AutoApplyAfter, DecisionScope, DecisionStatus, ResolvedBy, RiskLevel


class DecisionOption(DispatchBaseModel):
    """A single selectable option on a decision (Standards Delta v0 §4.6)."""

    id: str
    label: str
    description: str
    tradeoffs: list[str] = Field(default_factory=list)


class DecisionRecommendation(DispatchBaseModel):
    """System recommendation among the options (Standards Delta v0 §4.6)."""

    option_id: str
    rationale: str


class DecisionDefaultAction(DispatchBaseModel):
    """Default action if unresolved (Standards Delta v0 §4.6).

    v0.1-alpha always uses ``auto_apply_after = never``.
    """

    option_id: str
    auto_apply_after: AutoApplyAfter = AutoApplyAfter.NEVER


class DecisionResolution(DispatchBaseModel):
    """Resolution state of a decision (Standards Delta v0 §4.6)."""

    selected_option_id: str | None = None
    resolved_by: ResolvedBy | None = None
    resolved_at: datetime | None = None


class DispatchDecision(DispatchBaseModel):
    """A user/system decision surfaced by the Decision Inbox (§4.6)."""

    id: str = Field(description='"decision_<ulid>"')
    job_id: str
    created_at: datetime

    scope: DecisionScope
    title: str
    question: str
    reason: str
    risk_level: RiskLevel

    options: list[DecisionOption] = Field(default_factory=list)
    recommendation: DecisionRecommendation
    default_action: DecisionDefaultAction

    status: DecisionStatus
    resolution: DecisionResolution = Field(default_factory=DecisionResolution)


__all__ = [
    "DecisionOption",
    "DecisionRecommendation",
    "DecisionDefaultAction",
    "DecisionResolution",
    "DispatchDecision",
]
