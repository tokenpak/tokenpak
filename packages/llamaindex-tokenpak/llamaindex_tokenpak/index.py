"""
TokenPakIndex — Index wrapper with automatic node compression.

Wraps a LlamaIndex index and adds TokenPak compression to retrieval.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .converters import llamaindex_nodes_to_blocks, blocks_to_llamaindex_nodes
from .synthesizer import TokenPakSynthesizer
from .query_engine import TokenPakQueryEngine


class TokenPakIndex:
    """
    LlamaIndex Index wrapper with TokenPak compression.

    Compresses retrieved nodes during query execution to reduce
    token costs while preserving answer quality.

    Usage:
        # Wrap an existing index
        tp_index = TokenPakIndex(existing_index, budget=4000)
        engine = tp_index.as_query_engine()
        response = engine.query("What is TokenPak?")

        # Or build from documents (requires llama-index-core)
        tp_index = TokenPakIndex.from_documents(docs, budget=2000)
    """

    def __init__(
        self,
        index: Any,
        budget: int = 2000,
        keep_headers: bool = True,
        keep_code: bool = True,
    ):
        """
        Args:
            index: LlamaIndex index instance.
            budget: Max tokens for compressed query context.
            keep_headers: Preserve markdown headers.
            keep_code: Preserve code blocks verbatim.
        """
        self.index = index
        self.budget = budget
        self.keep_headers = keep_headers
        self.keep_code = keep_code

    @classmethod
    def from_documents(
        cls,
        documents: List[Any],
        budget: int = 2000,
        index_class: Optional[Any] = None,
        **index_kwargs,
    ) -> "TokenPakIndex":
        """
        Create a TokenPakIndex from documents.

        Args:
            documents: List of LlamaIndex Document objects or dicts.
            budget: Max tokens for compressed query context.
            index_class: LlamaIndex index class (e.g. VectorStoreIndex).
                         Auto-detected if llama-index-core is installed.
            **index_kwargs: Additional kwargs passed to the index constructor.

        Returns:
            TokenPakIndex wrapping the built index.
        """
        if index_class is None:
            try:
                from llama_index.core import VectorStoreIndex
                index_class = VectorStoreIndex
            except ImportError:
                raise ImportError(
                    "llama-index-core is required for from_documents(). "
                    "Install it with: pip install llama-index-core"
                )

        index = index_class.from_documents(documents, **index_kwargs)
        return cls(index=index, budget=budget)

    def as_query_engine(self, **kwargs) -> "TokenPakQueryEngine":
        """
        Get a TokenPakQueryEngine backed by this index.

        Args:
            **kwargs: Passed to the underlying index's as_query_engine().

        Returns:
            TokenPakQueryEngine with compression enabled.
        """
        base_engine = self.index.as_query_engine(**kwargs) if self.index else None
        return TokenPakQueryEngine(
            query_engine=base_engine,
            budget=self.budget,
            keep_headers=self.keep_headers,
            keep_code=self.keep_code,
        )

    def as_retriever(self, **kwargs) -> Any:
        """Get underlying retriever (no compression — use as_query_engine for that)."""
        if self.index is None:
            raise RuntimeError("No index available. Use from_documents() or wrap an existing index.")
        return self.index.as_retriever(**kwargs)

    def compress_nodes(self, nodes: List[Any]) -> List[Dict[str, Any]]:
        """
        Compress a list of nodes to fit within budget.

        Useful for custom retrieval pipelines.

        Args:
            nodes: LlamaIndex nodes (dict or TextNode/NodeWithScore).

        Returns:
            Compressed nodes as LlamaIndex-compatible dicts.
        """
        synthesizer = TokenPakSynthesizer(budget=self.budget)
        blocks = llamaindex_nodes_to_blocks(nodes)
        compressed = synthesizer._compress_blocks(blocks)
        return blocks_to_llamaindex_nodes(compressed)
