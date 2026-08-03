"""
TokenPak adapter for Qdrant vector database.

Converts Qdrant search results into TokenPak VectorBlocks.
Works with qdrant-client v1.6+.

Usage:
    from qdrant_client import QdrantClient
    from tokenpak_vectordb import QdrantAdapter

    client = QdrantClient("localhost", port=6333)
    adapter = QdrantAdapter(client, collection_name="docs")

    query_vector = [0.1, 0.2, ...]
    blocks = adapter.query_as_blocks(query_vector, limit=10)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import VectorBlock, VectorDBAdapter


class QdrantAdapter(VectorDBAdapter):
    """
    Adapter for Qdrant → TokenPak VectorBlocks.

    Qdrant returns ScoredPoint objects with:
      - id (int or str)
      - score (float, cosine: -1 to 1, dot: unbounded, euclid: 0+)
      - payload (dict with document fields)

    Score normalization:
      - Cosine (default): clamp to 0-1
      - Euclid/L2 distance: invert (lower = better)
    """

    source_type = "qdrant"

    def __init__(
        self,
        client: Any,
        collection_name: str,
        content_field: str = "text",
        default_block_type: str = "evidence",
        default_limit: int = 10,
        score_metric: str = "cosine",
    ):
        """
        Args:
            client: QdrantClient instance
            collection_name: Qdrant collection name
            content_field: Payload field name for document text
            default_block_type: Default TokenPak block type
            default_limit: Default result limit
            score_metric: "cosine", "dot", or "euclid"
                          Affects score → quality mapping
        """
        super().__init__(
            default_block_type=default_block_type,
            default_limit=default_limit,
            content_field=content_field,
        )
        self._client = client
        self.collection_name = collection_name
        self.score_metric = score_metric.lower()

    def query_as_blocks(
        self,
        query: Any,
        limit: int | None = None,
        block_type: str | None = None,
        score_threshold: Optional[float] = None,
        query_filter: Any = None,
        with_payload: bool = True,
        **kwargs,
    ) -> List[VectorBlock]:
        """
        Query Qdrant and return results as VectorBlocks.

        Args:
            query: Query embedding (list of floats)
            limit: Max results
            block_type: TokenPak block type override
            score_threshold: Minimum score threshold
            query_filter: Qdrant Filter object or dict
            with_payload: Whether to fetch payload (must be True for content)
            **kwargs: Extra args passed to client.search()

        Returns:
            List[VectorBlock] ordered by descending quality
        """
        n = limit or self.default_limit
        btype = block_type or self.default_block_type

        search_kwargs: Dict[str, Any] = {
            "collection_name": self.collection_name,
            "query_vector": query,
            "limit": n,
            "with_payload": with_payload,
            **kwargs,
        }
        if score_threshold is not None:
            search_kwargs["score_threshold"] = score_threshold
        if query_filter is not None:
            search_kwargs["query_filter"] = query_filter

        hits = self._client.search(**search_kwargs)

        blocks = []
        for hit in hits:
            block = self._result_to_block(hit, btype)
            if block is not None:
                blocks.append(block)
        return blocks

    def _result_to_block(
        self,
        result: Any,
        block_type: str,
    ) -> Optional[VectorBlock]:
        """Convert a Qdrant ScoredPoint to VectorBlock."""
        # Handle object-style (qdrant-client) and dict-style
        if hasattr(result, "id"):
            result_id = str(result.id)
            score = float(getattr(result, "score", 0.0))
            payload = dict(getattr(result, "payload", {}) or {})
        else:
            result_id = str(result.get("id", "unknown"))
            score = float(result.get("score", 0.0))
            payload = dict(result.get("payload", {}) or {})

        content = self._extract_content(payload)
        quality = self._normalize_qdrant_score(score)

        clean_meta = {
            k: v for k, v in payload.items()
            if k not in (self.content_field, "text", "content")
        }

        return VectorBlock(
            id=result_id,
            content=content,
            block_type=block_type,
            quality=quality,
            metadata=clean_meta,
            provenance=self._make_provenance(
                result_id,
                extra={
                    "collection": self.collection_name,
                    "raw_score": score,
                    "score_metric": self.score_metric,
                },
            ),
        )

    def _normalize_qdrant_score(self, score: float) -> float:
        """Normalize Qdrant score to 0-1 quality based on metric."""
        if self.score_metric == "euclid":
            # Euclidean distance: lower is better; quality = 1/(1+d)
            return self._score_to_quality(score, invert=True)
        elif self.score_metric == "dot":
            # Dot product: unbounded; use sigmoid approximation
            import math
            return 1.0 / (1.0 + math.exp(-score))
        else:
            # Cosine: already -1 to 1; map to 0-1
            return max(0.0, min(1.0, (score + 1.0) / 2.0))
