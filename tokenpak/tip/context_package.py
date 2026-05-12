# SPDX-License-Identifier: Apache-2.0
"""TIP Context Package contract — packaged context for the current AI session.

A ``ContextPackage`` is the bundle MultiPak hands to the current AI tool: it
references one or more :class:`tokenpak.tip.pak.Pak` instances and declares
the *delivery level* (how much of each Pak's content is included), the
*coverage state* (whether the package fully satisfies the user's intent),
and the policy decisions that gated the package.

This module defines the OSS-side schema for the Context Package and the
Handoff Pak (which is a Context Package targeted at a specific external
platform). The build engine that produces these packages lives in the
``tokenpak-paid`` daemon (closed source). The schema must land in OSS
first — an inviolable architectural rule preserving the open-tier vs.
paid-tier boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum, IntEnum
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Delivery levels and coverage states
# ---------------------------------------------------------------------------


class ContextLevel(IntEnum):
    """How much of each referenced Pak is materialized in the package.

    Levels are integer-ordered; higher = more content.

    - 0 ``no_memory``: empty package. Returned for general/unrelated requests.
    - 1 ``pointer_only``: candidate Pak IDs only, no content.
    - 2 ``recall_summary``: Pak summaries only.
    - 3 ``handoff_pak``: execution-ready package with summaries + decisions.
    - 4 ``hydrated_handoff_pak``: includes exact anchor snippets.
    - 5 ``full_restore``: original source chunks. Manual / debug-only.
    """

    NO_MEMORY = 0
    POINTER_ONLY = 1
    RECALL_SUMMARY = 2
    HANDOFF_PAK = 3
    HYDRATED_HANDOFF_PAK = 4
    FULL_RESTORE = 5


_CONTEXT_LEVEL_LABELS: Mapping[ContextLevel, str] = {
    ContextLevel.NO_MEMORY: "no_memory",
    ContextLevel.POINTER_ONLY: "pointer_only",
    ContextLevel.RECALL_SUMMARY: "recall_summary",
    ContextLevel.HANDOFF_PAK: "handoff_pak",
    ContextLevel.HYDRATED_HANDOFF_PAK: "hydrated_handoff_pak",
    ContextLevel.FULL_RESTORE: "full_restore",
}


def context_level_label(level: ContextLevel) -> str:
    """Wire-form (``snake_case`` string) for a ``ContextLevel``.

    Consumers SHOULD use this rather than constructing the string ad-hoc —
    when a future TIP-1.x minor revision adds a new level, only this table
    needs updating (per ``feedback_always_dynamic.md``).
    """
    return _CONTEXT_LEVEL_LABELS.get(level, "unknown")


def parse_context_level(value: int | str) -> ContextLevel:
    """Parse a level from either the integer or string wire form."""
    if isinstance(value, int):
        return ContextLevel(value)
    inverse = {label: level for level, label in _CONTEXT_LEVEL_LABELS.items()}
    if value not in inverse:
        raise ValueError(f"unknown context level label: {value!r}")
    return inverse[value]


class CoverageState(str, Enum):
    """Reported coverage of a Context Package vs. the requested intent.

    Every package emits a coverage state; consumers
    use it to decide whether to warn the user, request clarification, or
    block downstream tool calls.

    - ``complete``: required Paks all included, hydration adequate.
    - ``partial``: some required Paks missing or summary-only when more
      precision was needed.
    - ``low_confidence``: Paks included but ranking confidence below threshold.
    - ``missing_required_context``: at least one explicitly-required Pak
      could not be located.
    - ``blocked_by_policy``: at least one candidate Pak was excluded by the
      privacy/scope/sensitive policy gate.
    - ``not_found``: query yielded no candidate Paks at all (legitimate
      result for unrelated queries — pair with ``ContextLevel.NO_MEMORY``).
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    LOW_CONFIDENCE = "low_confidence"
    MISSING_REQUIRED_CONTEXT = "missing_required_context"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    NOT_FOUND = "not_found"


class CoverageConfidence(str, Enum):
    """Confidence tier on the coverage assessment."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Sub-records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """Per-package coverage scoring.

    ``required_paks`` is the count the resolver determined were required to
    satisfy intent; ``included_paks`` is how many made it into the package.
    They diverge on partial/blocked paths.

    ``missing_context`` is a list of human-readable descriptions of what
    couldn't be located (used for the user-facing warning surface).
    """

    state: CoverageState
    required_paks: int = 0
    included_paks: int = 0
    hydrated_anchors: int = 0
    missing_context: tuple[str, ...] = ()
    confidence: CoverageConfidence = CoverageConfidence.MEDIUM


@dataclass(frozen=True)
class ContextScope:
    """Scoping fields on a package — mirrors :class:`PakScope` but for the
    package as a whole. ``user_scope`` is always ``local_user`` in v1
    (no cross-tenant sharing in this release)."""

    user_scope: str = "local_user"
    project_scope: Optional[str] = None
    target_platform: Optional[str] = None
    target_task: Optional[str] = None


class AnchorBlockPosition(str, Enum):
    """Where hydrated anchor snippets sit relative to the rest of the package.

    Read by the Pro Phase 3 Context Package builder; OSS persistence +
    inspection treat the value transparently (OSS/Pro split: OSS = data
    surface, Pro = enforcement).

    - ``end``: anchor snippets appended after the main Pak content.
    - ``inline``: anchor snippets interleaved with their referencing block.
    - ``omit``: drop anchors; useful for budget-constrained packages.
    """

    END = "end"
    INLINE = "inline"
    OMIT = "omit"


@dataclass(frozen=True)
class OrderingHints:
    """Optional cache-aware ordering preferences for a Context Package.

    Additive within TIP-1.x — receivers that don't recognise the field
    ignore it and produce a valid (just unoptimised) package.
    The Pro Phase 3 Context Package builder is the authoritative consumer;
    OSS persists / inspects / exports / validates the field transparently
    (OSS = data plane, Pro = enforcement).

    Attributes:
        stable_first: Place stable/reusable Pak content before volatile
            task delta. Cache-aware.
        task_delta_after_stable_context: Mandatory and primary Paks appear
            after stable/reusable Paks.
        output_requirements_near_end: Output-shape instructions immediately
            precede the cursor.
        cache_sensitive_blocks: Block IDs whose ordering must not change
            once assembled (cache stability).
        anchor_block_position: Where hydrated anchor snippets sit (see
            :class:`AnchorBlockPosition`). Default ``END``.

    The supported hint fields are catalogued in the public registry.
    """

    stable_first: bool = True
    task_delta_after_stable_context: bool = True
    output_requirements_near_end: bool = True
    cache_sensitive_blocks: tuple[str, ...] = ()
    anchor_block_position: AnchorBlockPosition = AnchorBlockPosition.END

    def to_wire(self) -> dict[str, Any]:
        """Wire-form mapping for embedding in a Context Package payload."""
        return {
            "stable_first": self.stable_first,
            "task_delta_after_stable_context": self.task_delta_after_stable_context,
            "output_requirements_near_end": self.output_requirements_near_end,
            "cache_sensitive_blocks": list(self.cache_sensitive_blocks),
            "anchor_block_position": self.anchor_block_position.value,
        }

    @classmethod
    def from_wire(cls, data: Mapping[str, Any]) -> "OrderingHints":
        """Parse from a wire-form mapping.

        Unknown ``anchor_block_position`` values raise ``ValueError`` —
        the registry is the source of truth and unknown values indicate a
        receiver-side bug, not a benign extension (the field set itself is
        additive, but the enum is closed within a given TIP minor)."""
        raw_pos = data.get("anchor_block_position", AnchorBlockPosition.END.value)
        try:
            pos = AnchorBlockPosition(raw_pos)
        except ValueError as exc:
            raise ValueError(
                f"unknown anchor_block_position: {raw_pos!r}; expected one of "
                f"{[v.value for v in AnchorBlockPosition]!r}"
            ) from exc
        return cls(
            stable_first=bool(data.get("stable_first", True)),
            task_delta_after_stable_context=bool(
                data.get("task_delta_after_stable_context", True)
            ),
            output_requirements_near_end=bool(
                data.get("output_requirements_near_end", True)
            ),
            cache_sensitive_blocks=tuple(data.get("cache_sensitive_blocks", ())),
            anchor_block_position=pos,
        )


@dataclass(frozen=True)
class PolicyDecision:
    """Records what the policy gate did (uses the
    ``tip.context.policy`` capability).

    ``blocked_pak_ids`` and ``blocked_anchor_ids`` are non-empty whenever
    coverage state is ``blocked_by_policy``.
    """

    cross_project_blocked: bool = False
    sensitive_blocked: bool = False
    hydration_budget_exceeded: bool = False
    blocked_pak_ids: tuple[str, ...] = ()
    blocked_anchor_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# ContextPackage (top-level)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPackage:
    """A bundle of context delivered to the current AI session.

    Frozen — packages are immutable artifacts; resuming or re-targeting
    builds a new package referencing the same Paks.

    ``recall_query`` records the natural-language intent the package was
    built for (used for audit + telemetry; never sent on the license-
    validation egress path).

    ``memory_horizon`` is the time-scope the recall searched
    (``recent`` / ``historical`` / ``project_lifetime``); informational only.
    """

    package_id: str
    scope: ContextScope
    recall_query: str
    context_level: ContextLevel
    included_pak_ids: tuple[str, ...]
    hydrated_anchor_ids: tuple[str, ...]
    coverage: CoverageReport
    policy: PolicyDecision
    generated_at: str  # ISO-8601 timestamp.
    memory_horizon: str = "recent"
    privacy_class: str = "local_only"
    ordering_hints: Optional[OrderingHints] = None
    """Optional cache-aware ordering preferences.

    Additive within TIP-1.x — receivers that don't recognise this field
    ignore it and produce a valid (just unoptimised) package.
    """

    # ---- Round-trip ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Render to JSON-serializable form matching the wire schema.

        ``ordering_hints`` is omitted entirely when ``None`` so older
        receivers see the byte-stable pre-addendum shape; consumers that
        recognise the field read it from the emitted mapping otherwise.
        """
        d = asdict(self)
        d["context_level"] = context_level_label(self.context_level)
        d["coverage"]["state"] = self.coverage.state.value
        d["coverage"]["confidence"] = self.coverage.confidence.value
        if self.ordering_hints is None:
            d.pop("ordering_hints", None)
        else:
            d["ordering_hints"] = self.ordering_hints.to_wire()
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContextPackage":
        """Parse a ContextPackage from its wire form."""
        scope_d = data.get("scope") or {}
        coverage_d = data["coverage"]
        policy_d = data.get("policy") or {}

        return cls(
            package_id=data["package_id"],
            scope=ContextScope(
                user_scope=scope_d.get("user_scope", "local_user"),
                project_scope=scope_d.get("project_scope"),
                target_platform=scope_d.get("target_platform"),
                target_task=scope_d.get("target_task"),
            ),
            recall_query=data["recall_query"],
            context_level=parse_context_level(data["context_level"]),
            included_pak_ids=tuple(data.get("included_pak_ids", ())),
            hydrated_anchor_ids=tuple(data.get("hydrated_anchor_ids", ())),
            coverage=CoverageReport(
                state=CoverageState(coverage_d["state"]),
                required_paks=coverage_d.get("required_paks", 0),
                included_paks=coverage_d.get("included_paks", 0),
                hydrated_anchors=coverage_d.get("hydrated_anchors", 0),
                missing_context=tuple(coverage_d.get("missing_context", ())),
                confidence=CoverageConfidence(
                    coverage_d.get("confidence", "medium")
                ),
            ),
            policy=PolicyDecision(
                cross_project_blocked=policy_d.get("cross_project_blocked", False),
                sensitive_blocked=policy_d.get("sensitive_blocked", False),
                hydration_budget_exceeded=policy_d.get("hydration_budget_exceeded", False),
                blocked_pak_ids=tuple(policy_d.get("blocked_pak_ids", ())),
                blocked_anchor_ids=tuple(policy_d.get("blocked_anchor_ids", ())),
                reasons=tuple(policy_d.get("reasons", ())),
            ),
            generated_at=data["generated_at"],
            memory_horizon=data.get("memory_horizon", "recent"),
            privacy_class=data.get("privacy_class", "local_only"),
            ordering_hints=(
                OrderingHints.from_wire(data["ordering_hints"])
                if data.get("ordering_hints") is not None
                else None
            ),
        )

    # ---- Convenience -----------------------------------------------------

    def is_empty(self) -> bool:
        """True for level-0 / no_memory packages."""
        return self.context_level == ContextLevel.NO_MEMORY

    def has_complete_coverage(self) -> bool:
        return self.coverage.state == CoverageState.COMPLETE


__all__ = [
    "AnchorBlockPosition",
    "ContextLevel",
    "ContextPackage",
    "ContextScope",
    "CoverageConfidence",
    "CoverageReport",
    "CoverageState",
    "OrderingHints",
    "PolicyDecision",
    "context_level_label",
    "parse_context_level",
]
