"""
TokenPak adapter for Chroma vector database (bonus adapter).

Converts Chroma query results into TokenPak VectorBlocks.
Works with chromadb v0.4+.

Usage:
    import chromadb
    from tokenpak_vectordb import ChromaAdapter

    client = chromadb.Client()
    collection = client.get_collection("docs")
    adapter = ChromaAdapter(collection)

    query_embedding = [0.1, 0.2, ...]
    blocks = adapter.query_as_blocks(query_embedding, limit=10)

    # Or with text query (uses Chroma's built-in embedding)
    blocks = adapter.query_as_blocks("What is TokenPak?", limit=10)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import VectorBlock, VectorDBAdapter


class ChromaAdapter(VectorDBAdapter):
    """
    Adapter for Chroma Collection → TokenPak VectorBlocks.

    Chroma returns results as parallel lists:
      - ids: List[List[str]]
      - documents: List[List[str]]
      - distances: List[List[float]]  (lower = more similar)
      - metadatas: List[List[dict]]

    Distance mapping:
      - L2 distance: quality = 1 / (1 + distance)
      - Cosine distance: quality = 1 - distance (chroma cosine: 0=identical, 2=opposite)
    """

    source_type = "chroma"

    def __init__(
        self,
        collection: Any,
        content_field: str = "text",
        default_block_type: str = "evidence",
        default_limit: int = 10,
        distance_metric: str = "l2",
    ):
        """
        Args:
            collection: chromadb.Collection object
            content_field: Metadata key for document text (if not in documents)
            default_block_type: Default TokenPak block type
            default_limit: Default result limit
            distance_metric: "l2" (default), "cosine", or "ip" (inner product)
        """
        super().__init__(
            default_block_type=default_block_type,
            default_limit=default_limit,
            content_field=content_field,
        )
        self._collection = collection
        self.distance_metric = distance_metric.lower()
        self.collection_name = getattr(collection, "name", "chroma")

    def query_as_blocks(
        self,
        query: Any,
        limit: int | None = None,
        block_type: str | None = None,
        where: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[VectorBlock]:
        """
        Query Chroma and return results as VectorBlocks.

        Args:
            query: Query embedding (list of floats) or text string
            limit: Max results
            block_type: TokenPak block type override
            where: Chroma metadata filter dict
            **kwargs: Extra args passed to collection.query()

        Returns:
            List[VectorBlock] ordered by descending quality
        """
        n = limit or self.default_limit
        btype = block_type or self.default_block_type

        query_kwargs: Dict[str, Any] = {
            "n_results": n,
            "include": ["documents", "distances", "metadatas"],
            **kwargs,
        }
        if where:
            query_kwargs["where"] = where

        # Chroma accepts either query_embeddings or query_texts
        if isinstance(query, str):
            query_kwargs["query_texts"] = [query]
        else:
            query_kwargs["query_embeddings"] = [query]

        response = self._collection.query(**query_kwargs)

        # Response is parallel lists; results[0] = first query's results
        ids = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]

        blocks = []
        for i, result_id in enumerate(ids):
            doc = documents[i] if i < len(documents) else ""
            distance = distances[i] if i < len(distances) else 0.0
            meta = metadatas[i] if i < len(metadatas) else {}

            block = self._result_to_block(
                {"id": result_id, "document": doc, "distance": distance, "metadata": meta},
                btype,
            )
            if block is not None:
                blocks.append(block)
        return blocks

    def _result_to_block(
        self,
        result: Any,
        block_type: str,
    ) -> Optional[VectorBlock]:
        """Convert a Chroma result dict to VectorBlock."""
        result_id = str(result.get("id", "unknown"))
        document = result.get("document", "")
        distance = float(result.get("distance", 0.0))
        metadata = dict(result.get("metadata", {}) or {})

        # Content: prefer document field, fall back to metadata
        if document:
            content = document
        else:
            content = self._extract_content(metadata)

        quality = self._chroma_distance_to_quality(distance)

        clean_meta = {
            k: v for k, v in metadata.items()
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
                    "raw_distance": distance,
                    "distance_metric": self.distance_metric,
                },
            ),
        )

    def _chroma_distance_to_quality(self, distance: float) -> float:
        """Convert Chroma distance to quality score."""
        if self.distance_metric == "cosine":
            # Chroma cosine distance: 0 = identical, 2 = opposite
            return max(0.0, min(1.0, 1.0 - distance / 2.0))
        elif self.distance_metric == "ip":
            # Inner product: higher is better; Chroma stores as negative distance
            # quality = sigmoid(-distance) ≈ 1/(1+e^distance)
            import math
            return 1.0 / (1.0 + math.exp(distance))
        else:
            # L2: lower distance = better quality; quality = 1/(1+d)
            return self._score_to_quality(distance, invert=True)
