# SPDX-License-Identifier: Apache-2.0
"""tokenpak/report.py — Compile Report Schema.

Every compile() call produces a CompileReport showing exactly what
TokenPak did and why. No hidden logic.

Usage:
    compiled = pack.compile()
    print(compiled.report)                      # human-readable terminal
    print(compiled.report.to_json())            # machine-readable dict
    print(compiled.report.to_markdown())        # docs-ready markdown

    for decision in compiled.report.decisions:
        if decision.action == Action.REMOVED:
            print(f"Removed {decision.block_id}: {decision.reason}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Action Enum
# ---------------------------------------------------------------------------


class Action(Enum):
    KEPT = "kept"
    COMPACTED = "compacted"
    REMOVED = "removed"
    TRUNCATED = "truncated"

    @property
    def icon(self) -> str:
        return {
            Action.KEPT: "✅",
            Action.COMPACTED: "📦",
            Action.REMOVED: "❌",
            Action.TRUNCATED: "✂️",
        }[self]

    @property
    def label(self) -> str:
        return {
            Action.KEPT: "KEPT",
            Action.COMPACTED: "COMPACTED",
            Action.REMOVED: "REMOVED",
            Action.TRUNCATED: "TRUNCATED",
        }[self]


# ---------------------------------------------------------------------------
# Decision — per-block record
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """Record of what happened to a single block during compile."""

    block_id: str
    block_type: str
    action: Action
    reason: str
    priority: str = "medium"
    tokens_before: int = 0
    tokens_after: int = 0
    quality: Optional[float] = None  # for REMOVED (quality threshold check)
    method: Optional[str] = None  # for COMPACTED (e.g. "extractive_summarization")

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    def to_dict(self) -> Dict[str, Any]:
        d: dict = {
            "block_id": self.block_id,
            "block_type": self.block_type,
            "action": self.action.value,
            "reason": self.reason,
            "priority": self.priority,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
        }
        if self.quality is not None:
            d["quality"] = round(self.quality, 3)
        if self.method is not None:
            d["method"] = self.method
        return d


# ---------------------------------------------------------------------------
# CompileReport — summary + all decisions
# ---------------------------------------------------------------------------


@dataclass
class CompileReport:
    """Full report of a single compile() call."""

    # Summary
    input_blocks: int
    output_blocks: int
    input_tokens: int
    output_tokens: int
    budget: int
    compile_time_ms: float

    # Per-block decisions (in evaluation order)
    decisions: List[Decision] = field(default_factory=list)

    # Final block order (IDs as they appear in output)
    final_order: List[str] = field(default_factory=list)

    # ── Derived stats ─────────────────────────────────────────────────────

    @property
    def tokens_saved(self) -> int:
        return max(0, self.input_tokens - self.output_tokens)

    @property
    def savings_percent(self) -> float:
        if self.input_tokens == 0:
            return 0.0
        return round((self.tokens_saved / self.input_tokens) * 100, 1)

    @property
    def budget_used_percent(self) -> float:
        if self.budget == 0:
            return 0.0
        return round((self.output_tokens / self.budget) * 100, 1)

    # ── Format: text (terminal) ──────────────────────────────────────────

    def to_text(self) -> str:
        """Human-readable terminal report."""
        W = 55  # width of separator
        lines = [
            "TokenPak Compile Report",
            "═" * W,
            "",
            "Summary",
            "─" * W,
            f"Input:   {self.input_blocks} blocks, {self.input_tokens:,} tokens",
            f"Output:  {self.output_blocks} blocks, {self.output_tokens:,} tokens",
            f"Savings: {self.savings_percent}% ({self.tokens_saved:,} tokens saved)",
            f"Budget:  {self.budget:,} tokens ({self.budget_used_percent}% used)",
            f"Time:    {self.compile_time_ms:.1f}ms",
            "",
            "Block Decisions",
            "─" * W,
            "",
        ]

        for d in self.decisions:
            icon = d.action.icon
            label = d.action.label
            lines.append(f"{icon} {label}: [{d.block_type}] {d.block_id}")
            lines.append(f"   Priority: {d.priority}")
            lines.append(f"   Tokens: {d.tokens_before:,}")

            if d.action == Action.COMPACTED:
                lines.append(
                    f"   Before: {d.tokens_before:,} tokens → After: {d.tokens_after:,} tokens"
                )
                lines.append(f"   Reason: {d.reason}")
                if d.method:
                    lines.append(f"   Method: {d.method}")

            elif d.action == Action.REMOVED:
                lines.append(f"   Reason: {d.reason}")
                lines.append(f"   Tokens saved: {d.tokens_saved:,}")
                if d.quality is not None:
                    lines.append(f"   Quality: {d.quality:.2f}")

            elif d.action == Action.TRUNCATED:
                lines.append(
                    f"   Before: {d.tokens_before:,} tokens → After: {d.tokens_after:,} tokens"
                )
                lines.append(f"   Reason: {d.reason}")

            else:  # KEPT
                lines.append(f"   Reason: {d.reason}")

            lines.append("")

        if self.final_order:
            lines.append("Priority Order")
            lines.append("─" * W)
            for i, block_id in enumerate(self.final_order, 1):
                # Find matching decision
                dec = next((d for d in self.decisions if d.block_id == block_id), None)
                token_str = f" - {dec.tokens_after:,} tokens" if dec else ""
                priority_str = f" ({dec.priority})" if dec else ""
                lines.append(f"{i:2}. {block_id}{priority_str}{token_str}")

        lines.append("")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.to_text()

    def __repr__(self) -> str:
        return (
            f"<CompileReport blocks={self.output_blocks}/{self.input_blocks} "
            f"tokens={self.output_tokens}/{self.input_tokens} "
            f"savings={self.savings_percent}%>"
        )

    # ── Format: JSON (logging / Langfuse) ───────────────────────────────

    def to_json(self) -> Dict[str, Any]:
        """Machine-readable dict. Suitable for json.dumps(), Langfuse metadata, etc."""
        return {
            "summary": {
                "input_blocks": self.input_blocks,
                "output_blocks": self.output_blocks,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "tokens_saved": self.tokens_saved,
                "budget": self.budget,
                "budget_used_percent": self.budget_used_percent,
                "savings_percent": self.savings_percent,
                "compile_time_ms": round(self.compile_time_ms, 2),
            },
            "decisions": [d.to_dict() for d in self.decisions],
            "final_order": self.final_order,
        }

    # ── Format: Markdown (docs / README) ────────────────────────────────

    def to_markdown(self) -> str:
        """Markdown-formatted report for documentation or logging."""
        lines = [
            "## TokenPak Compile Report",
            "",
            "### Summary",
            "",
            "| Metric | Value |",
            "| ------ | ----- |",
            f"| Input  | {self.input_blocks} blocks, {self.input_tokens:,} tokens |",
            f"| Output | {self.output_blocks} blocks, {self.output_tokens:,} tokens |",
            f"| Savings | {self.savings_percent}% ({self.tokens_saved:,} tokens) |",
            f"| Budget | {self.budget:,} tokens ({self.budget_used_percent}% used) |",
            f"| Compile time | {self.compile_time_ms:.1f}ms |",
            "",
            "### Block Decisions",
            "",
        ]

        for d in self.decisions:
            icon = d.action.icon
            label = d.action.label
            lines.append(f"#### {icon} {label}: `{d.block_id}`")
            lines.append("")
            lines.append(f"- **Type:** `{d.block_type}`")
            lines.append(f"- **Priority:** `{d.priority}`")

            if d.action == Action.COMPACTED:
                lines.append(
                    f"- **Tokens:** {d.tokens_before:,} → {d.tokens_after:,} "
                    f"(saved {d.tokens_saved:,})"
                )
                lines.append(f"- **Reason:** {d.reason}")
                if d.method:
                    lines.append(f"- **Method:** `{d.method}`")

            elif d.action == Action.REMOVED:
                lines.append(f"- **Tokens:** {d.tokens_before:,} (removed)")
                lines.append(f"- **Reason:** {d.reason}")
                if d.quality is not None:
                    lines.append(f"- **Quality:** {d.quality:.2f}")

            elif d.action == Action.TRUNCATED:
                lines.append(
                    f"- **Tokens:** {d.tokens_before:,} → {d.tokens_after:,} "
                    f"(saved {d.tokens_saved:,})"
                )
                lines.append(f"- **Reason:** {d.reason}")

            else:  # KEPT
                lines.append(f"- **Tokens:** {d.tokens_after:,}")
                lines.append(f"- **Reason:** {d.reason}")

            lines.append("")

        if self.final_order:
            lines.append("### Priority Order")
            lines.append("")
            for i, block_id in enumerate(self.final_order, 1):
                dec = next((d for d in self.decisions if d.block_id == block_id), None)
                token_str = f" — {dec.tokens_after:,} tokens" if dec else ""
                priority_str = f" `{dec.priority}`" if dec else ""
                lines.append(f"{i}. `{block_id}`{priority_str}{token_str}")
            lines.append("")

        return "\n".join(lines)
