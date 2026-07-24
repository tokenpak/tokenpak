"""
MultiIndexFusion — Query multiple LlamaIndex indexes with result merging.

Enables querying across multiple indexes simultaneously and fusing
the results into a single compressed TokenPak context.

Use cases:
  - RAG across multiple document collections
  - Cross-domain search (code + docs + wiki)
  - Federated retrieval with score normalization
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from .converters import (
    LlamaBlock,
    llamaindex_nodes_to_blocks,
)
from .synthesizer import TokenPakSynthesizer


class MultiIndexFusion:
    """
    Query multiple LlamaIndex indexes and fuse results with compression.

    Fusion strategies:
      - "rank": Re-rank by score across all indexes (default)
      - "round_robin": Alternate results from each index
      - "weighted": Weight results by per-index weight

    Usage:
        fusion = MultiIndexFusion(
            indexes={
                "docs": docs_query_engine,
                "code": code_query_engine,
                "wiki": wiki_query_engine,
            },
            budget=6000,
            weights={"docs": 0.5, "code": 0.3, "wiki": 0.2},
        )

        # Standard query
        result = fusion.query("How does context compression work?")
        print(result["context"])   # compressed, fused context

        # As TokenPak pack
        pack = fusion.query_as_tokenpak("How does context compression work?")
        print(pack["blocks"])      # all compressed evidence blocks
        print(pack["sources"])     # which index each block came from
    """

    def __init__(
        self,
        indexes: Dict[str, Any],
        budget: int = 6000,
        strategy: str = "rank",
        weights: Optional[Dict[str, float]] = None,
        top_k_per_index: int = 5,
        keep_headers: bool = True,
        keep_code: bool = True,
    ):
        """
        Args:
            indexes: Mapping of name → query engine (or index with .query()).
            budget: Total token budget for fused context.
            strategy: Fusion strategy ("rank", "round_robin", "weighted").
            weights: Per-index weights for "weighted" strategy (0-1, sum to 1).
            top_k_per_index: Max results to retrieve per index.
            keep_headers: Preserve markdown headers in compression.
            keep_code: Preserve code blocks verbatim.
        """
        if not indexes:
            raise ValueError("At least one index must be provided.")

        valid_strategies = ("rank", "round_robin", "weighted")
        if strategy not in valid_strategies:
            raise ValueError(f"strategy must be one of {valid_strategies}")

        self.indexes = indexes
        self.budget = budget
        self.strategy = strategy
        self.weights = weights or {name: 1.0 / len(indexes) for name in indexes}
        self.top_k_per_index = top_k_per_index
        self._synthesizer = TokenPakSynthesizer(
            budget=budget,
            keep_headers=keep_headers,
            keep_code=keep_code,
        )
        self._last_stats: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, query_str: str, **kwargs) -> Dict[str, Any]:
        """
        Query all indexes and return fused, compressed context.

        Returns:
            {
                "query":   str,
                "context": str — fused, compressed context,
                "blocks":  List[dict] — compressed evidence blocks,
                "sources": Dict[str, int] — block counts per index,
                "tokens":  Dict — compression stats,
            }
        """
        indexed_nodes = self._query_all_sync(query_str, **kwargs)
        return self._fuse_and_compress(query_str, indexed_nodes)

    async def aquery(self, query_str: str, **kwargs) -> Dict[str, Any]:
        """Async version of query (queries all indexes in parallel)."""
        indexed_nodes = await self._query_all_async(query_str, **kwargs)
        return self._fuse_and_compress(query_str, indexed_nodes)

    def query_as_tokenpak(self, query_str: str, **kwargs) -> Dict[str, Any]:
        """
        Query all indexes and return a complete TokenPak pack.

        Returns:
            {
                "query":    str,
                "context":  str — formatted, compressed context,
                "blocks":   List[dict] — compressed evidence blocks (tokenpak format),
                "sources":  Dict[str, int] — blocks contributed per index,
                "tokens":   Dict — input/output/budget/ratio,
                "metadata": Dict — fusion strategy, index count, etc.
            }
        """
        result = self.query(query_str, **kwargs)
        result["metadata"] = {
            "strategy": self.strategy,
            "index_count": len(self.indexes),
            "index_names": list(self.indexes.keys()),
            "top_k_per_index": self.top_k_per_index,
        }
        return result

    async def aquery_as_tokenpak(self, query_str: str, **kwargs) -> Dict[str, Any]:
        """Async version of query_as_tokenpak."""
        result = await self.aquery(query_str, **kwargs)
        result["metadata"] = {
            "strategy": self.strategy,
            "index_count": len(self.indexes),
            "index_names": list(self.indexes.keys()),
            "top_k_per_index": self.top_k_per_index,
        }
        return result

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def _query_all_sync(self, query_str: str, **kwargs) -> List[Tuple[str, List[Any]]]:
        """Query all indexes synchronously, return [(name, nodes), ...]."""
        results = []
        for name, engine in self.indexes.items():
            try:
                response = engine.query(query_str, **kwargs)
                nodes = self._extract_nodes(response, name)
                results.append((name, nodes))
            except Exception:
                results.append((name, []))
        return results

    async def _query_all_async(self, query_str: str, **kwargs) -> List[Tuple[str, List[Any]]]:
        """Query all indexes in parallel using asyncio."""

        async def _query_one(name: str, engine: Any) -> Tuple[str, List[Any]]:
            try:
                if hasattr(engine, "aquery"):
                    response = await engine.aquery(query_str, **kwargs)
                else:
                    response = engine.query(query_str, **kwargs)
                return (name, self._extract_nodes(response, name))
            except Exception:
                return (name, [])

        tasks = [_query_one(name, engine) for name, engine in self.indexes.items()]
        return list(await asyncio.gather(*tasks))

    @staticmethod
    def _extract_nodes(response: Any, source_name: str) -> List[Dict[str, Any]]:
        """Extract nodes from a query response, tagging with source."""
        nodes = []

        if hasattr(response, "source_nodes"):
            raw_nodes = response.source_nodes or []
        elif isinstance(response, list):
            raw_nodes = response
        else:
            # Synthetic node from response text
            raw_nodes = [
                {
                    "id": f"{source_name}_response",
                    "text": str(response),
                    "metadata": {"source": source_name},
                    "score": 1.0,
                }
            ]

        for node in raw_nodes:
            if isinstance(node, dict):
                node = {
                    **node,
                    "metadata": {
                        **node.get("metadata", {}),
                        "_fusion_source": source_name,
                    },
                }
            elif hasattr(node, "node"):
                # NodeWithScore
                if hasattr(node.node, "metadata"):
                    node.node.metadata["_fusion_source"] = source_name
            elif hasattr(node, "metadata"):
                node.metadata["_fusion_source"] = source_name
            nodes.append(node)

        return nodes

    # ------------------------------------------------------------------
    # Fusion strategies
    # ------------------------------------------------------------------

    def _fuse_and_compress(
        self,
        query_str: str,
        indexed_nodes: List[Tuple[str, List[Any]]],
    ) -> Dict[str, Any]:
        """Apply fusion strategy, compress, and return pack."""
        all_blocks: List[LlamaBlock] = []
        sources: Dict[str, int] = {}

        # Convert each index's results to blocks
        for name, nodes in indexed_nodes:
            blocks = llamaindex_nodes_to_blocks(nodes)
            # Apply per-index weight to quality scores
            weight = self.weights.get(name, 1.0)
            for block in blocks:
                block.quality = block.quality * weight
                block.metadata["_fusion_source"] = name
            all_blocks.extend(blocks)
            sources[name] = len(blocks)

        if not all_blocks:
            return {
                "query": query_str,
                "context": "",
                "blocks": [],
                "sources": sources,
                "tokens": {
                    "input": 0,
                    "output": 0,
                    "budget": self.budget,
                    "ratio": 1.0,
                },
            }

        # Apply fusion strategy
        if self.strategy == "rank":
            fused = self._fuse_rank(all_blocks)
        elif self.strategy == "round_robin":
            fused = self._fuse_round_robin(indexed_nodes)
        elif self.strategy == "weighted":
            fused = self._fuse_weighted(all_blocks)
        else:
            fused = all_blocks

        # Compress to budget
        compressed = self._synthesizer._compress_blocks(fused)
        context = self._synthesizer._blocks_to_context(compressed, query_str)

        input_tokens = sum(b._original_tokens for b in compressed)
        output_tokens = sum(b.tokens for b in compressed)

        return {
            "query": query_str,
            "context": context,
            "blocks": [b.to_tokenpak_dict() for b in compressed],
            "sources": sources,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "budget": self.budget,
                "ratio": round(output_tokens / max(1, input_tokens), 3),
            },
        }

    @staticmethod
    def _fuse_rank(blocks: List[LlamaBlock]) -> List[LlamaBlock]:
        """Sort all blocks by quality score DESC."""
        return sorted(blocks, key=lambda b: b.quality, reverse=True)

    @staticmethod
    def _fuse_round_robin(
        indexed_nodes: List[Tuple[str, List[Any]]],
    ) -> List[LlamaBlock]:
        """Interleave results from each index (round-robin)."""
        all_index_blocks = []
        for name, nodes in indexed_nodes:
            blocks = llamaindex_nodes_to_blocks(nodes)
            all_index_blocks.append(blocks)

        fused = []
        max_len = max((len(b) for b in all_index_blocks), default=0)
        for i in range(max_len):
            for index_blocks in all_index_blocks:
                if i < len(index_blocks):
                    fused.append(index_blocks[i])
        return fused

    @staticmethod
    def _fuse_weighted(blocks: List[LlamaBlock]) -> List[LlamaBlock]:
        """Sort by weight-adjusted quality (weights already applied)."""
        return sorted(blocks, key=lambda b: b.quality, reverse=True)
