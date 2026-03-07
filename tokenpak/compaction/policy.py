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
