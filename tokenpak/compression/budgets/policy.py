"""
TokenPak Compaction Policy System.

Provides a structured, serialisable policy configuration for compaction,
supporting global defaults and per-block-type overrides.

Example JSON configuration::

    {
        "compaction": {
            "mode": "balanced",
            "max_tokens": 8000,
            "priority_order": ["instructions", "code", "knowledge"],
            "per_block_limits": {
                "instructions": { "mode": "lossless" },
                "code": { "mode": "balanced", "max_tokens": 2000 }
            }
        }
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .modes import CompactionMode, compact

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BlockPolicy:
    """Per-block-type compaction policy."""

    mode: CompactionMode = CompactionMode.BALANCED
    max_tokens: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BlockPolicy":
        return cls(
            mode=CompactionMode(data.get("mode", CompactionMode.BALANCED)),
            max_tokens=data.get("max_tokens"),
        )

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"mode": self.mode.value}
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        return d


@dataclass
class CompactionPolicy:
    """
    Top-level compaction policy.

    Attributes:
        mode:            Default compaction mode for all blocks.
        max_tokens:      Global token budget ceiling (across all blocks).
        priority_order:  Block types ordered by priority when trimming.
        per_block_limits: Per-block-type overrides (keyed by block type).
    """

    mode: CompactionMode = CompactionMode.BALANCED
    max_tokens: Optional[int] = None
    priority_order: List[str] = field(default_factory=lambda: ["instructions", "code", "knowledge"])
    per_block_limits: Dict[str, BlockPolicy] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompactionPolicy":
        """Build policy from a plain dictionary (e.g. parsed JSON)."""
        inner = data.get("compaction", data)  # accept both wrapped and flat
        per_block: Dict[str, BlockPolicy] = {}
        for bt, cfg in inner.get("per_block_limits", {}).items():
            per_block[bt] = BlockPolicy.from_dict(cfg)
        return cls(
            mode=CompactionMode(inner.get("mode", CompactionMode.BALANCED)),
            max_tokens=inner.get("max_tokens"),
            priority_order=inner.get("priority_order", ["instructions", "code", "knowledge"]),
            per_block_limits=per_block,
        )

    @classmethod
    def default(cls) -> "CompactionPolicy":
        """Return the default balanced policy."""
        return cls()

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON round-trip."""
        d: Dict[str, Any] = {"mode": self.mode.value}
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        if self.priority_order:
            d["priority_order"] = self.priority_order
        if self.per_block_limits:
            d["per_block_limits"] = {bt: bp.to_dict() for bt, bp in self.per_block_limits.items()}
        return {"compaction": d}

    # ------------------------------------------------------------------ #
    # Compaction entry point
    # ------------------------------------------------------------------ #

    def compact_block(
        self,
        text: str,
        block_type: Optional[str] = None,
    ) -> str:
        """
        Compact *text* according to this policy.

        If *block_type* is provided and a per-block override exists, the
        override's mode and max_tokens take precedence over the global
        defaults.

        Args:
            text:       Input text to compact.
            block_type: Logical block type (e.g. ``"code"``, ``"instructions"``).

        Returns:
            Compacted text.
        """
        bp = self.per_block_limits.get(block_type or "")
        mode = bp.mode if bp else self.mode
        max_tokens = bp.max_tokens if (bp and bp.max_tokens is not None) else self.max_tokens
        return compact(text, mode=mode, target_tokens=max_tokens)

    def resolve_mode(self, block_type: Optional[str] = None) -> CompactionMode:
        """Return the effective CompactionMode for *block_type*."""
        bp = self.per_block_limits.get(block_type or "")
        return bp.mode if bp else self.mode


# ---------------------------------------------------------------------------
# Topic-Aware Compaction Policy
# ---------------------------------------------------------------------------


@dataclass
class TopicAwarePolicy(CompactionPolicy):
    """
    Topic-aware compaction policy with differential compression.

    Extends CompactionPolicy to support topic-aware segmentation and
    compression. Active topics receive richer context (less compression),
    while inactive topics are summarized aggressively.

    Attributes:
        active_mode:       Compaction mode for active topics (default: balanced).
        inactive_mode:     Compaction mode for inactive topics (default: aggressive).
        activity_threshold: Score threshold for topic activity classification (0.0-1.0).
        per_topic_limits:  Optional per-topic max token budgets.
    """

    active_mode: CompactionMode = CompactionMode.BALANCED
    inactive_mode: CompactionMode = CompactionMode.AGGRESSIVE
    activity_threshold: float = 0.5
    per_topic_limits: Dict[str, Optional[int]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TopicAwarePolicy":
        """Build policy from dictionary with topic-aware extensions."""
        inner = data.get("compaction", data)

        per_block: Dict[str, BlockPolicy] = {}
        for bt, cfg in inner.get("per_block_limits", {}).items():
            per_block[bt] = BlockPolicy.from_dict(cfg)

        per_topic: Dict[str, Optional[int]] = {}
        for tid, limit in inner.get("per_topic_limits", {}).items():
            per_topic[tid] = limit

        return cls(
            mode=CompactionMode(inner.get("mode", CompactionMode.BALANCED)),
            max_tokens=inner.get("max_tokens"),
            priority_order=inner.get("priority_order", ["instructions", "code", "knowledge"]),
            per_block_limits=per_block,
            active_mode=CompactionMode(inner.get("active_mode", CompactionMode.BALANCED)),
            inactive_mode=CompactionMode(inner.get("inactive_mode", CompactionMode.AGGRESSIVE)),
            activity_threshold=inner.get("activity_threshold", 0.5),
            per_topic_limits=per_topic,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary with topic-aware fields."""
        base = super().to_dict()
        inner = base.get("compaction", {})

        if self.active_mode != CompactionMode.BALANCED:
            inner["active_mode"] = self.active_mode.value
        if self.inactive_mode != CompactionMode.AGGRESSIVE:
            inner["inactive_mode"] = self.inactive_mode.value
        if self.activity_threshold != 0.5:
            inner["activity_threshold"] = self.activity_threshold
        if self.per_topic_limits:
            inner["per_topic_limits"] = self.per_topic_limits

        base["compaction"] = inner
        return base

    def compact_with_topics(self, text: str) -> str:
        """
        Compact text using topic-aware segmentation.

        Segments text into topics, applies differential compression:
        - Active topics: uses active_mode with appropriate token budget
        - Inactive topics: uses inactive_mode with tighter token budget

        Args:
            text: Input text to compact.

        Returns:
            Topic-aware compacted text.
        """
        from .topic_aware import TopicBoundaryDetector, place_topic_aware_breakpoints

        # Segment text into topics
        detector = TopicBoundaryDetector()
        segments = detector.segment(text)

        if not segments or len(segments) == 1:
            # Fallback to standard compaction for single-segment text
            return self.compact_block(text)

        # Place breakpoints with topic-aware budgets
        breakpoints = place_topic_aware_breakpoints(segments, self.max_tokens or 8000)

        # Compact each segment according to its activity
        result_parts = []
        for segment in segments:
            budget = breakpoints.get(segment.topic_id, self.max_tokens)

            if segment.activity_score >= self.activity_threshold:
                # Active topic: use active_mode
                mode = self.active_mode
            else:
                # Inactive topic: use inactive_mode
                mode = self.inactive_mode

            # Override with per-topic limit if configured
            if segment.topic_id in self.per_topic_limits:
                budget = self.per_topic_limits[segment.topic_id]

            compacted = compact(segment.content, mode=mode, target_tokens=budget)
            result_parts.append(compacted)

        return "".join(result_parts)

    def compact_block_with_topics(
        self,
        text: str,
        block_type: Optional[str] = None,
    ) -> str:
        """
        Compact block with optional topic awareness.

        If text contains clear topic boundaries, uses topic-aware segmentation.
        Otherwise, falls back to standard block compaction.

        Args:
            text:       Input text to compact.
            block_type: Logical block type (e.g. ``"code"``, ``"instructions"``).

        Returns:
            Compacted text.
        """
        # For small texts or code blocks, use standard compaction
        if block_type in ("code", "instructions") or len(text) < 500:
            return self.compact_block(text, block_type=block_type)

        # For large narrative blocks, use topic-aware compaction
        return self.compact_with_topics(text)
