"""
TokenPak adapter for Weaviate vector database.

Converts Weaviate query results into TokenPak VectorBlocks.
Supports both weaviate-client v3 (dict-based) and v4 (object-based).

Usage:
    import weaviate
    from tokenpak_vectordb import WeaviateAdapter

    client = weaviate.connect_to_local()  # v4
    adapter = WeaviateAdapter(client, collection_name="Document")

    blocks = adapter.query_as_blocks("What is TokenPak?", limit=10)
    for block in blocks:
        print(block.quality, block.content[:80])
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import VectorBlock, VectorDBAdapter


class WeaviateAdapter(VectorDBAdapter):
    """
    Adapter for Weaviate → TokenPak VectorBlocks.

    Supports two modes:
      - near_text: semantic search from a text query string
      - near_vector: ANN search from a query embedding

    Weaviate certainty (v3) or distance (v4) → quality mapping:
      - certainty (0-1): quality = certainty
      - distance (0-2): quality = 1 - distance/2 (clamped to 0-1)
    """

    source_type = "weaviate"

    def __init__(
        self,
        client: Any,
        collection_name: str,
        content_field: str = "text",
        default_block_type: str = "evidence",
        default_limit: int = 10,
        distance_metric: bool = False,
    ):
        """
        Args:
            client: weaviate.Client (v3) or weaviate.WeaviateClient (v4)
            collection_name: Weaviate class/collection name
            content_field: Property name that holds document text
            default_block_type: Default TokenPak block type
            default_limit: Default result limit
            distance_metric: True if Weaviate returns distance (lower=better),
                             False if it returns certainty (higher=better)
        """
        super().__init__(
            default_block_type=default_block_type,
            default_limit=default_limit,
            content_field=content_field,
        )
        self._client = client
        self.collection_name = collection_name
        self.distance_metric = distance_metric

    def query_as_blocks(
        self,
        query: Any,
        limit: int | None = None,
        block_type: str | None = None,
        mode: str = "near_text",
        certainty: float = 0.7,
        **kwargs,
    ) -> List[VectorBlock]:
        """
        Query Weaviate and return results as VectorBlocks.

        Args:
            query: Text query (near_text mode) or embedding vector (near_vector mode)
            limit: Max results
            block_type: TokenPak block type override
            mode: "near_text" or "near_vector"
            certainty: Minimum certainty threshold (near_text mode)
            **kwargs: Extra args passed to Weaviate query

        Returns:
            List[VectorBlock] ordered by descending quality
        """
        n = limit or self.default_limit
        btype = block_type or self.default_block_type

        raw_results = self._run_query(query, n, mode, certainty, **kwargs)
        blocks = []
        for result in raw_results:
            block = self._result_to_block(result, btype)
            if block is not None:
                blocks.append(block)
        # Sort by quality descending
        blocks.sort(key=lambda b: b.quality, reverse=True)
        return blocks

    def _run_query(
        self,
        query: Any,
        limit: int,
        mode: str,
        certainty: float,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Execute the Weaviate query, return list of raw result dicts."""
        # Detect v4 vs v3
        if hasattr(self._client, "collections"):
            return self._run_v4_query(query, limit, mode, certainty, **kwargs)
        return self._run_v3_query(query, limit, mode, certainty, **kwargs)

    def _run_v4_query(
        self,
        query: Any,
        limit: int,
        mode: str,
        certainty: float,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Weaviate client v4 query."""
        collection = self._client.collections.get(self.collection_name)
        if mode == "near_text":
            response = collection.query.near_text(
                query=str(query),
                limit=limit,
                certainty=certainty,
                return_metadata=["certainty", "distance"],
                **kwargs,
            )
        else:
            response = collection.query.near_vector(
                near_vector=query,
                limit=limit,
                return_metadata=["certainty", "distance"],
                **kwargs,
            )
        results = []
        for obj in (response.objects or []):
            item = dict(obj.properties or {})
            meta = obj.metadata
            if meta:
                item["_certainty"] = getattr(meta, "certainty", None)
                item["_distance"] = getattr(meta, "distance", None)
            item["_id"] = str(obj.uuid) if hasattr(obj, "uuid") else str(obj.collection)
            results.append(item)
        return results

    def _run_v3_query(
        self,
        query: Any,
        limit: int,
        mode: str,
        certainty: float,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Weaviate client v3 query."""
        q = (
            self._client.query
            .get(self.collection_name)
            .with_limit(limit)
            .with_additional(["id", "certainty", "distance"])
        )
        if mode == "near_text":
            q = q.with_near_text({"concepts": [str(query)], "certainty": certainty})
        else:
            q = q.with_near_vector({"vector": query})
        response = q.do()
        items = (
            response.get("data", {})
            .get("Get", {})
            .get(self.collection_name, [])
        ) or []
        results = []
        for item in items:
            extra = item.get("_additional", {})
            flat = {k: v for k, v in item.items() if k != "_additional"}
            flat["_id"] = extra.get("id", "")
            flat["_certainty"] = extra.get("certainty")
            flat["_distance"] = extra.get("distance")
            results.append(flat)
        return results

    def _result_to_block(
        self,
        result: Any,
        block_type: str,
    ) -> Optional[VectorBlock]:
        """Convert a Weaviate result dict to VectorBlock."""
        result_id = str(result.get("_id", "") or result.get("id", "unknown"))
        content = self._extract_content(result)

        # Determine quality from certainty or distance
        certainty = result.get("_certainty")
        distance = result.get("_distance")
        if certainty is not None:
            quality = self._score_to_quality(float(certainty))
        elif distance is not None:
            # distance 0-2; quality = max(0, 1 - distance/2)
            quality = self._score_to_quality(1.0 - float(distance) / 2.0)
        else:
            quality = 0.5

        clean_meta = {
            k: v for k, v in result.items()
            if not k.startswith("_")
            and k not in (self.content_field, "text", "content")
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
                    "certainty": certainty,
                    "distance": distance,
                },
            ),
        )
