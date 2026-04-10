# SPDX-License-Identifier: Apache-2.0
"""tokenpak/pack.py — High-level ContextPack API with compile reports.

Provides the developer-facing compile() API with full transparency:

    from tokenpak.pack import ContextPack, PackBlock

    pack = ContextPack(budget=8000)
    pack.add(PackBlock(id="system_prompt", type="instructions", content="...", priority="critical"))
    pack.add(PackBlock(id="api_docs",      type="knowledge",     content="...", priority="high",   max_tokens=1000))
    pack.add(PackBlock(id="search_003",    type="evidence",      content="...", priority="medium", quality=0.3))
    pack.add(PackBlock(id="history",       type="conversation",  content="...", priority="low",    max_tokens=650))

    compiled = pack.compile()
    print(compiled.report)              # human-readable terminal report
    print(compiled.text)                # final wire-format text
    print(compiled.report.to_json())    # Langfuse-ready dict

Compile logic (in priority order):
  1. CRITICAL blocks: always KEPT (never removed/truncated unless block-level max_tokens set)
  2. HIGH blocks:     KEPT, truncated if block exceeds max_tokens
  3. MEDIUM blocks:   KEPT if budget allows; quality-filtered first (removed if quality < threshold)
  4. LOW blocks:      KEPT last, truncated to fill remaining budget; dropped if no room

Quality filtering (for MEDIUM/LOW): blocks with quality < quality_threshold are REMOVED.
Block-level max_tokens: blocks exceeding their own cap are COMPACTED (truncated within cap).
Budget overflow: lowest-priority blocks REMOVED until within budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .report import Action, CompileReport, Decision

# ── Token counting (reuse budgeter's approach) ────────────────────────────

try:
    import tiktoken

    _enc = tiktoken.encoding_for_model("gpt-4")

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))

    def _truncate_to_tokens(text: str, max_tokens: int) -> str:
        tokens = _enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return _enc.decode(tokens[:max_tokens]) + "..."

except ImportError:

    def _count_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def _truncate_to_tokens(text: str, max_tokens: int) -> str:
        approx = max_tokens * 4
        return text[:approx] + ("..." if len(text) > approx else "")


# ── Priority ordering ─────────────────────────────────────────────────────

_PRIORITY_RANK: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _priority_rank(p: str) -> int:
    return _PRIORITY_RANK.get(p.lower(), 99)


# ── PackBlock — input descriptor ─────────────────────────────────────────


@dataclass
class PackBlock:
    """A single content block to be evaluated during compile().

    Args:
        id:         Unique identifier (used in report + final_order).
        type:       Block type label (instructions, knowledge, evidence,
                    conversation, context, …).
        content:    Raw text content.
        priority:   'critical' | 'high' | 'medium' | 'low'
        quality:    0–1 float. Blocks below quality_threshold are REMOVED.
        max_tokens: Per-block token cap. If set and block exceeds it,
                    the block is COMPACTED to fit.
    """

    id: str
    type: str
    content: str
    priority: str = "medium"
    quality: Optional[float] = None
    max_tokens: Optional[int] = None


# ── CompiledResult — output of compile() ─────────────────────────────────


@dataclass
class CompiledResult:
    """Return value of ContextPack.compile().

    Stack-neutral output methods allow the compiled result to be used
    with any LLM provider without requiring the TokenPak gateway.
    """

    text: str
    report: CompileReport

    def __str__(self) -> str:  # pragma: no cover
        return self.text

    # ── Stack-neutral protocol outputs ────────────────────────────────

    def to_prompt(self) -> str:
        """Return compiled context as plain text.

        Works anywhere: OpenAI, Anthropic, LiteLLM, Ollama, or as a
        standalone string. Zero dependencies.

        Example::

            print(compiled.to_prompt())
        """
        return self.text

    def to_messages(self) -> List[Dict[str, Any]]:
        """Return compiled context as OpenAI-format messages list.

        Compatible with OpenAI, LiteLLM, Ollama, and any provider that
        accepts the ``messages`` parameter.

        Example::

            from openai import OpenAI
            client = OpenAI()
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=compiled.to_messages(),
            )
        """
        if not self.text:
            return []
        return [{"role": "user", "content": self.text}]

    def to_messages_with_system(
        self,
        system: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return compiled context with an optional separate system message.

        Useful when you want to split instructions from content.

        Args:
            system: Optional system prompt text. If None, returns a single
                    user message containing the full compiled context.

        Example::

            messages = compiled.to_messages_with_system("You are a helpful assistant.")
        """
        msgs: List[Dict[str, Any]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        if self.text:
            msgs.append({"role": "user", "content": self.text})
        return msgs

    def to_anthropic(self) -> Tuple[str, List[Dict[str, Any]]]:
        """Return ``(system_prompt, messages)`` in Anthropic SDK format.

        Example::

            from anthropic import Anthropic
            system, messages = compiled.to_anthropic()
            Anthropic().messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=1024,
                system=system,
                messages=messages,
            )
        """
        return (self.text, [])

    def to_json(self) -> Dict[str, Any]:
        """Return the full compiled result as a JSON-serializable dict.

        Includes the compiled text and the full compile report for
        storage, transfer, or observability pipelines.

        Example::

            import json
            payload = json.dumps(compiled.to_json())
        """
        return {
            "text": self.text,
            "report": self.report.to_json(),
        }


# ── Convenience helpers — incremental adoption ────────────────────────────


def pack_prompt(
    system: Optional[str] = None,
    docs: Optional[str] = None,
    history: Optional[str] = None,
    budget: int = 8000,
) -> str:
    """Level 2 convenience helper — pack a prompt in one call.

    No configuration required. Builds a ContextPack, adds your content as
    priority-ranked blocks, compiles, and returns the plain-text result.

    Args:
        system:  System/instruction text (critical priority — always kept).
        docs:    Knowledge/document text (high priority).
        history: Conversation history (low priority — first to be trimmed).
        budget:  Total token budget (default 8000).

    Returns:
        Compiled plain-text prompt ready for any LLM.

    Example::

        from tokenpak import pack_prompt
        prompt = pack_prompt(system="You are helpful.", docs=my_docs, budget=4096)
    """
    pack = ContextPack(budget=budget)
    if system:
        pack.add(PackBlock(id="system", type="instructions", content=system, priority="critical"))
    if docs:
        pack.add(PackBlock(id="docs", type="knowledge", content=docs, priority="high"))
    if history:
        pack.add(PackBlock(id="history", type="conversation", content=history, priority="low"))
    return pack.compile().to_prompt()


# ── ContextPack — main class ──────────────────────────────────────────────


class ContextPack:
    """Budget-aware context compiler with full transparency reports.

    Args:
        budget:            Total token budget for the compiled output.
        quality_threshold: Blocks with quality < this are REMOVED (default 0.5).
        separator:         String placed between blocks in text output.
    """

    def __init__(
        self,
        budget: int = 8000,
        quality_threshold: float = 0.5,
        separator: str = "\n\n---\n\n",
    ) -> None:
        self.budget = budget
        self.quality_threshold = quality_threshold
        self.separator = separator
        self._blocks: List[PackBlock] = []

    def add(self, block: PackBlock) -> "ContextPack":
        """Add a block. Returns self for chaining."""
        self._blocks.append(block)
        return self

    def clear(self) -> "ContextPack":
        """Remove all blocks."""
        self._blocks.clear()
        return self

    # ── Compile ───────────────────────────────────────────────────────────

    def compile(self) -> CompiledResult:
        """Compile all blocks into a budgeted output with a full report.

        Returns:
            CompiledResult with .text and .report attributes.
        """
        t_start = time.perf_counter()

        # Measure raw token counts
        block_tokens: dict[str, int] = {b.id: _count_tokens(b.content) for b in self._blocks}
        input_tokens_total = sum(block_tokens.values())

        decisions: list[Decision] = []
        kept_blocks: list[tuple[PackBlock, str, int]] = []  # (block, text, tokens)

        # Sort by priority (critical first) for deterministic ordering
        ordered = sorted(self._blocks, key=lambda b: _priority_rank(b.priority))

        for block in ordered:
            raw_tokens = block_tokens[block.id]

            # ── Quality filter (REMOVED) ──────────────────────────────
            if block.quality is not None and block.quality < self.quality_threshold:
                decisions.append(
                    Decision(
                        block_id=block.id,
                        block_type=block.type,
                        action=Action.REMOVED,
                        reason=(
                            f"below quality threshold "
                            f"({block.quality:.2f} < {self.quality_threshold:.2f})"
                        ),
                        priority=block.priority,
                        tokens_before=raw_tokens,
                        tokens_after=0,
                        quality=block.quality,
                    )
                )
                continue

            text = block.content

            # ── Block-level cap (COMPACTED) ───────────────────────────
            if block.max_tokens is not None and raw_tokens > block.max_tokens:
                text = _truncate_to_tokens(text, block.max_tokens)
                after_tokens = _count_tokens(text)
                decisions.append(
                    Decision(
                        block_id=block.id,
                        block_type=block.type,
                        action=Action.COMPACTED,
                        reason=f"exceeded block budget (max {block.max_tokens:,})",
                        method="extractive_truncation",
                        priority=block.priority,
                        tokens_before=raw_tokens,
                        tokens_after=after_tokens,
                    )
                )
                kept_blocks.append((block, text, after_tokens))
                continue

            # ── Tentative KEPT ────────────────────────────────────────
            kept_blocks.append((block, text, raw_tokens))

        # ── Budget enforcement pass ───────────────────────────────────
        # Remove lowest-priority blocks until we fit within budget.
        # For blocks already in decisions (REMOVED/COMPACTED), they're done.
        # For kept_blocks, we may need to drop the lowest-priority ones.
        sum(t for _, _, t in kept_blocks)

        # Sort kept by priority DESCENDING (lowest priority last → drop first)
        kept_by_prio = sorted(
            kept_blocks,
            key=lambda x: _priority_rank(x[0].priority),
            reverse=True,  # highest rank number = lowest priority = drop first
        )

        final_kept: list[tuple[PackBlock, str, int]] = []
        running_total = 0

        # We pass through from highest to lowest priority
        # Flip to process highest-priority first
        for block, text, tokens in reversed(kept_by_prio):
            if running_total + tokens <= self.budget:
                final_kept.append((block, text, tokens))
                running_total += tokens
            else:
                # Can we TRUNCATE to fit remaining budget?
                remaining = self.budget - running_total
                if remaining > 0 and block.priority in ("critical", "high"):
                    truncated = _truncate_to_tokens(text, remaining)
                    after_tokens = _count_tokens(truncated)
                    final_kept.append((block, truncated, after_tokens))
                    running_total += after_tokens
                    decisions.append(
                        Decision(
                            block_id=block.id,
                            block_type=block.type,
                            action=Action.TRUNCATED,
                            reason="conversation budget exceeded",
                            priority=block.priority,
                            tokens_before=tokens,
                            tokens_after=after_tokens,
                        )
                    )
                else:
                    # REMOVED due to budget
                    decisions.append(
                        Decision(
                            block_id=block.id,
                            block_type=block.type,
                            action=Action.REMOVED,
                            reason="over total budget — dropped lowest priority block",
                            priority=block.priority,
                            tokens_before=tokens,
                            tokens_after=0,
                            quality=block.quality,
                        )
                    )

        # Blocks that were quietly KEPT (no special action) need decisions too
        {b.id for b, _, _ in final_kept}
        decision_ids = {d.block_id for d in decisions}

        for block, text, tokens in final_kept:
            if block.id not in decision_ids:
                decisions.append(
                    Decision(
                        block_id=block.id,
                        block_type=block.type,
                        action=Action.KEPT,
                        reason=(
                            "critical priority"
                            if block.priority == "critical"
                            else f"{block.priority} priority — within budget"
                        ),
                        priority=block.priority,
                        tokens_before=block_tokens[block.id],
                        tokens_after=tokens,
                        quality=block.quality,
                    )
                )

        # Sort final_kept back into priority order for output
        priority_idx = {b.id: _priority_rank(b.priority) for b in self._blocks}
        final_sorted = sorted(final_kept, key=lambda x: priority_idx[x[0].id])

        # Sort decisions in evaluation order (priority, then original index)
        original_order = {b.id: i for i, b in enumerate(self._blocks)}
        decisions.sort(key=lambda d: (original_order.get(d.block_id, 999),))

        # ── Build output text ─────────────────────────────────────────
        parts = [text for _, text, _ in final_sorted]
        output_text = self.separator.join(parts)

        output_tokens = sum(t for _, _, t in final_sorted)
        final_order = [b.id for b, _, _ in final_sorted]

        t_end = time.perf_counter()
        compile_time_ms = (t_end - t_start) * 1000.0

        report = CompileReport(
            input_blocks=len(self._blocks),
            output_blocks=len(final_sorted),
            input_tokens=input_tokens_total,
            output_tokens=output_tokens,
            budget=self.budget,
            compile_time_ms=compile_time_ms,
            decisions=decisions,
            final_order=final_order,
        )

        return CompiledResult(text=output_text, report=report)
