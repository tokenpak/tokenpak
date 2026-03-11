"""
CompressionPipeline — orchestrator for the TokenPak compression pipeline.

Chains segmentization → dedup → recipe assembly → directive application
in a single pass. Each stage is optional and toggled via constructor flags.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .dedup import dedup_messages
from .alias_compressor import AliasCompressor, AliasResult
from .directives import DirectiveApplier
from .instruction_table import InstructionTable
from .segmentizer import Segment, segmentize


@dataclass
class PipelineResult:
    """Output of a CompressionPipeline.run() call."""

    messages: List[Dict[str, Any]]
    segments: List[Segment]
    tokens_raw: int
    tokens_after: int
    duration_ms: float
    stages_run: List[str] = field(default_factory=list)
    instruction_replacements: Dict[str, int] = field(default_factory=dict)
    instruction_savings: Dict[str, int] = field(default_factory=dict)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_raw - self.tokens_after)

    @property
    def savings_pct(self) -> float:
        if self.tokens_raw == 0:
            return 0.0
        return round(self.tokens_saved / self.tokens_raw * 100, 2)


class CompressionPipeline:
    """
    Orchestrates the TokenPak compression pipeline.

    Stages (all optional, enabled by default):
      1. dedup    — remove duplicate / near-duplicate message turns
      2. segment  — classify messages into typed Segment objects
      3. directives — apply Pro-tier directives (stub; no-op in OSS)

    Custom compression hooks can be added via :meth:`add_hook`.

    Parameters
    ----------
    enable_dedup : bool
        Whether to run the dedup stage.
    enable_segmentation : bool
        Whether to run the segmentizer stage.
    enable_directives : bool
        Whether to run the directive-application stage.
    trace_id : str
        Optional trace ID forwarded to segmentize().
    """

    def __init__(
        self,
        enable_dedup: bool = True,
        enable_alias: bool = True,
        enable_segmentation: bool = True,
        enable_directives: bool = True,
        enable_instruction_table: bool = True,
        instruction_table_path: str | None = None,
        context_budget_tight: bool = True,
        trace_id: str = "",
        alias_min_occurrences: int = 3,
        alias_min_length: int = 20,
    ) -> None:
        self.enable_dedup = enable_dedup
        self.enable_alias = enable_alias
        self.enable_segmentation = enable_segmentation
        self._alias_compressor = AliasCompressor(
            min_occurrences=alias_min_occurrences,
            min_entity_length=alias_min_length,
        )
        self.enable_directives = enable_directives
        self.enable_instruction_table = enable_instruction_table
        self.context_budget_tight = context_budget_tight
        self.trace_id = trace_id
        self._hooks: List[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = []
        self._directive_applier = DirectiveApplier()
        self._instruction_table = InstructionTable(path=instruction_table_path)

    def add_hook(
        self,
        fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    ) -> None:
        """Register a custom compression hook (called after built-in stages)."""
        self._hooks.append(fn)

    def run(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        dry_run: bool = False,
    ) -> PipelineResult:
        """
        Run the full compression pipeline on *messages*.

        Parameters
        ----------
        messages:
            List of message dicts (must have "role" key).
        tools:
            Optional tool-definition list (forwarded to segmentizer).

        Returns
        -------
        PipelineResult
        """
        t0 = time.time()
        stages: List[str] = []

        tokens_raw = _estimate_tokens(messages)
        out = list(messages)  # work on a copy

        # Stage 1: dedup
        if self.enable_dedup:
            out = dedup_messages(out)
            stages.append("dedup")

        # Stage 1b: alias compression
        alias_result: "AliasResult | None" = None
        if self.enable_alias:
            alias_result = self._alias_compressor.compress(out)
            out = alias_result.messages
            stages.append("alias")

        instruction_replacements: Dict[str, int] = {}
        instruction_savings: Dict[str, int] = {}

        # Stage 2: instruction table lookup compression
        if self.enable_instruction_table:
            out, instruction_stats = self._instruction_table.compress_messages(
                out,
                context_budget_tight=self.context_budget_tight,
                persist=not dry_run,
            )
            instruction_replacements = instruction_stats.replacements_by_id
            instruction_savings = instruction_stats.tokens_saved_by_id
            stages.append("instruction_table")

        # Stage 3: custom hooks
        for hook in self._hooks:
            try:
                out = hook(out)
                stages.append(hook.__name__ if hasattr(hook, "__name__") else "hook")
            except Exception as exc:
                print(f"  ⚠ compression hook error: {exc}")

        # Stage 4: segmentize
        segments: List[Segment] = []
        if self.enable_segmentation:
            segments = segmentize(out, tools=tools, trace_id=self.trace_id)
            stages.append("segmentize")

        # Stage 5: directives
        if self.enable_directives:
            out = self._directive_applier.apply(out)
            stages.append("directives")

        tokens_after = _estimate_tokens(out)
        duration_ms = (time.time() - t0) * 1000

        return PipelineResult(
            messages=out,
            segments=segments,
            tokens_raw=tokens_raw,
            tokens_after=tokens_after,
            duration_ms=round(duration_ms, 2),
            stages_run=stages,
            instruction_replacements=instruction_replacements,
            instruction_savings=instruction_savings,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += len(part["text"]) // 4
    return total
