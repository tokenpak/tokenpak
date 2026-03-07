"""
TokenPak adapter for Pinecone vector database.

Converts Pinecone query results into TokenPak VectorBlocks.
Works with pinecone-client v3+ (new pinecone package).

Usage:
    from pinecone import Pinecone
    from tokenpak_vectordb import PineconeAdapter

    pc = Pinecone(api_key="...")
    index = pc.Index("my-index")
    adapter = PineconeAdapter(index)

    query_embedding = [0.1, 0.2, ...]  # your embedding
    blocks = adapter.query_as_blocks(query_embedding, top_k=10)
    for block in blocks:
        print(block.quality, block.content[:80])
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import VectorBlock, VectorDBAdapter


class PineconeAdapter(VectorDBAdapter):
    """
    Adapter for Pinecone Index → TokenPak VectorBlocks.

    Pinecone returns:
      - id (str)
      - score (float, cosine similarity 0-1)
      - metadata (dict, includes text/content fields)

    The adapter maps score → quality directly (cosine similarity is already 0-1).
    """

    source_type = "pinecone"

    def __init__(
        self,
        index: Any,
        namespace: str = "",
        content_field: str = "text",
        default_block_type: str = "evidence",
        default_limit: int = 10,
    ):
        """
        Args:
            index: Pinecone Index object (pinecone.Index or pinecone.data.Index)
            namespace: Pinecone namespace to query (empty = default namespace)
            content_field: Metadata field containing the document text
            default_block_type: Default TokenPak block type
            default_limit: Default top_k
        """
        super().__init__(
            default_block_type=default_block_type,
            default_limit=default_limit,
            content_field=content_field,
        )
        self._index = index
        self.namespace = namespace

    def query_as_blocks(
        self,
        query: Any,
        limit: int | None = None,
        block_type: str | None = None,
        namespace: str | None = None,
        filter: Optional[Dict[str, Any]] = None,
        include_metadata: bool = True,
        **kwargs,
    ) -> List[VectorBlock]:
        """
        Query Pinecone and return results as VectorBlocks.

        Args:
            query: Query embedding (list of floats)
            limit: top_k (number of results)
            block_type: TokenPak block type override
            namespace: Pinecone namespace override
            filter: Pinecone metadata filter dict
            include_metadata: Whether to fetch metadata (must be True for content)
            **kwargs: Extra args passed to index.query()

        Returns:
            List[VectorBlock] ordered by descending similarity score
        """
        top_k = limit or self.default_limit
        btype = block_type or self.default_block_type
        ns = namespace if namespace is not None else self.namespace

        query_kwargs: Dict[str, Any] = {
            "vector": query,
            "top_k": top_k,
            "include_metadata": include_metadata,
            **kwargs,
        }
        if ns:
            query_kwargs["namespace"] = ns
        if filter:
            query_kwargs["filter"] = filter

        response = self._index.query(**query_kwargs)

        blocks = []
        matches = getattr(response, "matches", None) or response.get("matches", [])
        for match in matches:
            block = self._result_to_block(match, btype)
            if block is not None:
                blocks.append(block)
        return blocks

    def _result_to_block(
        self,
        result: Any,
        block_type: str,
    ) -> Optional[VectorBlock]:
        """Convert a Pinecone match object/dict to VectorBlock."""
        # Handle both object-style (pinecone v3+) and dict-style results
        if hasattr(result, "id"):
            result_id = result.id
            score = float(getattr(result, "score", 0.0))
            metadata = dict(getattr(result, "metadata", {}) or {})
        else:
            result_id = result.get("id", "")
            score = float(result.get("score", 0.0))
            metadata = dict(result.get("metadata", {}) or {})

        if not result_id:
            return None

        content = self._extract_content(metadata)
        quality = self._score_to_quality(score)  # cosine sim is already 0-1

        # Remove content field from metadata to avoid duplication
        clean_meta = {k: v for k, v in metadata.items()
                      if k not in (self.content_field, "text", "content")}

        return VectorBlock(
            id=result_id,
            content=content,
            block_type=block_type,
            quality=quality,
            metadata=clean_meta,
            provenance=self._make_provenance(
                result_id,
                extra={
                    "raw_score": score,
                    "namespace": self.namespace,
                    "index": getattr(self._index, "name", None)
                        or getattr(self._index, "_name", "pinecone"),
                },
            ),
        )
