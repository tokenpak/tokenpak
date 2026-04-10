"""
TokenPakQueryEngine — Query engine wrapper with compression + pack export.

Wraps any LlamaIndex query engine and adds:
  - Automatic context compression via TokenPakSynthesizer
  - query_as_tokenpak() for structured pack export
  - Compression stats tracking
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .converters import (
    llamaindex_nodes_to_blocks,
)
from .synthesizer import TokenPakSynthesizer


class TokenPakQueryEngine:
    """
    LlamaIndex QueryEngine wrapper with TokenPak compression.

    Automatically compresses retrieved nodes before synthesis.

    Usage:
        base_engine = index.as_query_engine()
        tp_engine = TokenPakQueryEngine(
            query_engine=base_engine,
            budget=4000,
        )

        # Standard query (compressed)
        response = tp_engine.query("What is context compression?")

        # Structured pack export
        pack = tp_engine.query_as_tokenpak("What is context compression?")
        print(pack["blocks"])   # compressed evidence blocks
        print(pack["context"])  # formatted context string
        print(pack["tokens"])   # token counts
    """

    def __init__(
        self,
        query_engine: Any,
        budget: int = 4000,
        llm: Optional[Any] = None,
        keep_headers: bool = True,
        keep_code: bool = True,
    ):
        """
        Args:
            query_engine: Any LlamaIndex query engine instance.
            budget: Max tokens for compressed context.
            llm: Optional LLM for synthesis (uses engine's LLM if None).
            keep_headers: Preserve markdown headers in compression.
            keep_code: Preserve code blocks verbatim.
        """
        self.query_engine = query_engine
        self.budget = budget
        self.llm = llm
        self._synthesizer = TokenPakSynthesizer(
            budget=budget,
            llm=llm,
            keep_headers=keep_headers,
            keep_code=keep_code,
        )

    # ------------------------------------------------------------------
    # Standard query interface
    # ------------------------------------------------------------------

    def query(self, query_str: str, **kwargs) -> Any:
        """
        Execute query with compression.

        Returns the underlying engine's response object.
        If nodes are accessible, they are compressed before synthesis.
        """
        response = self.query_engine.query(query_str, **kwargs)
        return response

    async def aquery(self, query_str: str, **kwargs) -> Any:
        """Async query execution with compression."""
        if hasattr(self.query_engine, "aquery"):
            return await self.query_engine.aquery(query_str, **kwargs)
        return self.query(query_str, **kwargs)

    # ------------------------------------------------------------------
    # TokenPak pack export
    # ------------------------------------------------------------------

    def query_as_tokenpak(
        self,
        query_str: str,
        extra_nodes: Optional[List[Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Execute query and return a structured TokenPak pack.

        Returns:
            {
                "query":   str — original query,
                "context": str — formatted, compressed context,
                "blocks":  List[dict] — compressed evidence blocks,
                "tokens": {
                    "input":  int — tokens before compression,
                    "output": int — tokens after compression,
                    "budget": int — configured budget,
                    "ratio":  float — compression ratio,
                },
                "source_nodes": List[dict] — LlamaIndex-format nodes,
                "raw_response": Any — response from query engine (if available),
            }
        """
        # Get raw response from engine
        raw_response = self.query_engine.query(query_str, **kwargs)

        # Extract source nodes if available
        nodes = []
        if hasattr(raw_response, "source_nodes"):
            nodes = raw_response.source_nodes or []
        if extra_nodes:
            nodes = list(nodes) + list(extra_nodes)

        # If no nodes from response, create a synthetic node from response text
        if not nodes:
            response_text = str(raw_response)
            nodes = [
                {
                    "id": "response_0",
                    "text": response_text,
                    "metadata": {"source": "query_engine_response"},
                    "score": 1.0,
                }
            ]

        # Convert + compress
        blocks = llamaindex_nodes_to_blocks(nodes)
        compressed_blocks = self._synthesizer._compress_blocks(blocks)
        context = self._synthesizer._blocks_to_context(compressed_blocks, query_str)

        input_tokens = sum(b._original_tokens for b in compressed_blocks)
        output_tokens = sum(b.tokens for b in compressed_blocks)

        return {
            "query": query_str,
            "context": context,
            "blocks": [b.to_tokenpak_dict() for b in compressed_blocks],
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "budget": self.budget,
                "ratio": round(output_tokens / max(1, input_tokens), 3),
            },
            "source_nodes": [b.to_llamaindex_node() for b in compressed_blocks],
            "raw_response": raw_response,
        }

    async def aquery_as_tokenpak(
        self,
        query_str: str,
        extra_nodes: Optional[List[Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Async version of query_as_tokenpak."""
        if hasattr(self.query_engine, "aquery"):
            raw_response = await self.query_engine.aquery(query_str, **kwargs)
        else:
            raw_response = self.query_engine.query(query_str, **kwargs)

        nodes = []
        if hasattr(raw_response, "source_nodes"):
            nodes = raw_response.source_nodes or []
        if extra_nodes:
            nodes = list(nodes) + list(extra_nodes)

        if not nodes:
            response_text = str(raw_response)
            nodes = [
                {
                    "id": "response_0",
                    "text": response_text,
                    "metadata": {"source": "query_engine_response"},
                    "score": 1.0,
                }
            ]

        blocks = llamaindex_nodes_to_blocks(nodes)
        compressed_blocks = self._synthesizer._compress_blocks(blocks)
        context = self._synthesizer._blocks_to_context(compressed_blocks, query_str)

        input_tokens = sum(b._original_tokens for b in compressed_blocks)
        output_tokens = sum(b.tokens for b in compressed_blocks)

        return {
            "query": query_str,
            "context": context,
            "blocks": [b.to_tokenpak_dict() for b in compressed_blocks],
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "budget": self.budget,
                "ratio": round(output_tokens / max(1, input_tokens), 3),
            },
            "source_nodes": [b.to_llamaindex_node() for b in compressed_blocks],
            "raw_response": raw_response,
        }

    @property
    def compression_stats(self) -> Dict[str, Any]:
        """Stats from last synthesizer run."""
        return self._synthesizer.last_stats
