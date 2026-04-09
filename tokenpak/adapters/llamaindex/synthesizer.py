"""
TokenPakSynthesizer — LlamaIndex synthesizer with budget-aware compression.

Compresses retrieved nodes to fit within a token budget before synthesis,
preserving the most relevant content and highest-scored evidence.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .converters import (
    LlamaBlock,
    llamaindex_nodes_to_blocks,
    blocks_to_llamaindex_nodes,
    _estimate_tokens,
)


class TokenPakSynthesizer:
    """
    LlamaIndex-compatible synthesizer with TokenPak compression.

    Automatically compresses retrieved nodes within a token budget
    before calling the underlying LLM for synthesis.

    Usage:
        synthesizer = TokenPakSynthesizer(budget=4000, llm=your_llm)

        # With LlamaIndex query engine:
        engine = index.as_query_engine(synthesizer=synthesizer)
        response = engine.query("What is context compression?")

        # Direct use:
        response = synthesizer.synthesize("question", nodes=nodes)

    Compression strategy:
        1. Convert nodes to LlamaBlocks (score-aware)
        2. Sort by quality (highest-scored first)
        3. Trim content proportionally to fit budget
        4. Preserve structure (headers, code blocks) when possible
    """

    def __init__(
        self,
        budget: int = 4000,
        llm: Optional[Any] = None,
        keep_headers: bool = True,
        keep_code: bool = True,
        system_prompt_reserve: int = 500,
    ):
        """
        Args:
            budget: Max tokens for compressed context passed to LLM.
            llm: Optional LLM instance (llama_index compatible). If None,
                 returns compressed context as text (dry mode).
            keep_headers: Preserve markdown headers during compression.
            keep_code: Preserve code blocks verbatim (no truncation inside).
            system_prompt_reserve: Token reserve for system/query overhead.
        """
        self.budget = budget
        self.llm = llm
        self.keep_headers = keep_headers
        self.keep_code = keep_code
        self.system_prompt_reserve = system_prompt_reserve
        self._effective_budget = max(100, budget - system_prompt_reserve)
        self._last_stats: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize(
        self,
        query: str,
        nodes: List[Any],
        additional_source_nodes: Optional[List[Any]] = None,
    ) -> Any:
        """
        Synthesize a response from compressed nodes.

        Args:
            query: Query string.
            nodes: List of LlamaIndex nodes (dict or TextNode/NodeWithScore).
            additional_source_nodes: Extra nodes to include in context.

        Returns:
            If llm is set: LLM response object.
            Otherwise: compressed context string (useful for testing).
        """
        all_nodes = list(nodes or [])
        if additional_source_nodes:
            all_nodes.extend(additional_source_nodes)

        blocks = llamaindex_nodes_to_blocks(all_nodes)
        compressed_blocks = self._compress_blocks(blocks)
        context = self._blocks_to_context(compressed_blocks, query)

        self._last_stats = {
            "input_nodes": len(all_nodes),
            "output_blocks": len(compressed_blocks),
            "input_tokens": sum(b._original_tokens for b in compressed_blocks),
            "output_tokens": sum(b.tokens for b in compressed_blocks),
            "compression_ratio": self._compression_ratio(compressed_blocks),
        }

        if self.llm is not None:
            return self._call_llm(query, context)

        # Dry mode — return structured result
        return {
            "response": context,
            "query": query,
            "source_nodes": blocks_to_llamaindex_nodes(compressed_blocks),
            "compression_stats": self._last_stats,
        }

    async def asynthesize(
        self,
        query: str,
        nodes: List[Any],
        additional_source_nodes: Optional[List[Any]] = None,
    ) -> Any:
        """Async version of synthesize."""
        if self.llm is not None and hasattr(self.llm, "acomplete"):
            all_nodes = list(nodes or [])
            if additional_source_nodes:
                all_nodes.extend(additional_source_nodes)
            blocks = llamaindex_nodes_to_blocks(all_nodes)
            compressed_blocks = self._compress_blocks(blocks)
            context = self._blocks_to_context(compressed_blocks, query)
            return await self._acall_llm(query, context)

        # Fallback to sync
        return self.synthesize(query, nodes, additional_source_nodes)

    @property
    def last_stats(self) -> Dict[str, Any]:
        """Compression stats from the last synthesize() call."""
        return self._last_stats

    # ------------------------------------------------------------------
    # Compression engine
    # ------------------------------------------------------------------

    def _compress_blocks(self, blocks: List[LlamaBlock]) -> List[LlamaBlock]:
        """
        Compress blocks to fit within budget.

        Strategy:
          1. Sort by quality DESC (best evidence first)
          2. If total tokens ≤ budget: return as-is
          3. Otherwise: trim proportionally, preserving high-quality blocks
        """
        if not blocks:
            return []

        # Sort best-quality first
        sorted_blocks = sorted(blocks, key=lambda b: b.quality, reverse=True)

        total_tokens = sum(b.tokens for b in sorted_blocks)
        if total_tokens <= self._effective_budget:
            return sorted_blocks

        # Proportional trim: higher-quality blocks get more budget
        compressed = []
        quality_sum = sum(b.quality for b in sorted_blocks) or len(sorted_blocks)

        for block in sorted_blocks:
            quality_share = block.quality / quality_sum
            block_budget = max(50, int(self._effective_budget * quality_share))
            block_budget = min(block_budget, block.tokens)

            if block.tokens > block_budget:
                new_content = self._trim_content(
                    block.content,
                    block_budget,
                    keep_headers=self.keep_headers,
                    keep_code=self.keep_code,
                )
                compressed.append(LlamaBlock(
                    id=block.id,
                    content=new_content,
                    block_type=block.block_type,
                    quality=block.quality,
                    metadata=block.metadata,
                    provenance=block.provenance,
                    compressed=True,
                    _original_tokens=block.tokens,
                ))
            else:
                compressed.append(block)

        return compressed

    @staticmethod
    def _trim_content(
        text: str,
        token_budget: int,
        keep_headers: bool = True,
        keep_code: bool = True,
    ) -> str:
        """
        Trim text to fit within token_budget.

        Preserves:
          - First paragraph (context anchor)
          - Markdown headers if keep_headers=True
          - Code blocks if keep_code=True
        """
        char_budget = token_budget * 4
        if len(text) <= char_budget:
            return text

        lines = text.splitlines(keepends=True)
        result_lines = []
        in_code_block = False
        char_count = 0

        for line in lines:
            # Track code fences
            if line.strip().startswith("```"):
                in_code_block = not in_code_block

            # Always keep headers
            is_header = keep_headers and line.startswith("#")
            # Keep lines inside code blocks
            in_code = keep_code and in_code_block

            if is_header or in_code:
                result_lines.append(line)
                char_count += len(line)
            elif char_count + len(line) <= char_budget:
                result_lines.append(line)
                char_count += len(line)
            else:
                remaining = char_budget - char_count
                if remaining > 20:
                    result_lines.append(line[:remaining] + "…")
                break

        trimmed = "".join(result_lines).rstrip()
        if len(trimmed) < len(text):
            trimmed += "\n[…compressed by TokenPak…]"

        return trimmed

    @staticmethod
    def _blocks_to_context(blocks: List[LlamaBlock], query: str) -> str:
        """Format compressed blocks into prompt context."""
        if not blocks:
            return ""

        parts = [f"## Retrieved Context for: {query}\n"]
        for i, block in enumerate(blocks, 1):
            source = (
                block.provenance.get("file_name")
                or block.provenance.get("url")
                or block.provenance.get("source")
                or f"source_{i}"
            )
            score_str = f"{block.quality:.2f}" if block.quality != 1.0 else ""
            header = f"### [{i}] {source}"
            if score_str:
                header += f" (score: {score_str})"
            if block.compressed:
                header += " [compressed]"
            parts.append(header)
            parts.append(block.content)
            parts.append("")

        return "\n".join(parts).strip()

    @staticmethod
    def _compression_ratio(blocks: List[LlamaBlock]) -> float:
        original = sum(b._original_tokens for b in blocks)
        compressed = sum(b.tokens for b in blocks)
        if original == 0:
            return 1.0
        return round(compressed / original, 3)

    def _call_llm(self, query: str, context: str) -> Any:
        """Call LLM with compressed context."""
        prompt = f"{context}\n\nQuestion: {query}\nAnswer:"
        if hasattr(self.llm, "complete"):
            return self.llm.complete(prompt)
        return self.llm(prompt)

    async def _acall_llm(self, query: str, context: str) -> Any:
        """Async LLM call."""
        prompt = f"{context}\n\nQuestion: {query}\nAnswer:"
        if hasattr(self.llm, "acomplete"):
            return await self.llm.acomplete(prompt)
        return self._call_llm(query, context)
