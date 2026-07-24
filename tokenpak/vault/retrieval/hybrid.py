"""
Hybrid retriever orchestrator: combines BM25 + optional local vector search via RRF fusion.
"""

from __future__ import annotations

__all__ = (
    "BM25Retriever",
    "FusedResult",
    "HybridRetriever",
    "HybridSearchConfig",
    "LocalVectorRetriever",
    "RetrievalQuery",
    "rrf_fusion_detailed",
)


import asyncio
import logging
from typing import Dict, List, Optional

from .base import FusedResult, HybridSearchConfig, RetrievalQuery, RetrievalResult
from .bm25 import BM25Retriever
from .fusion import rrf_fusion_detailed
from .vector_local import LocalVectorRetriever

logger = logging.getLogger(__name__)


class HybridRetriever:
    """
    Orchestrates BM25 + optional vector retrieval, fusing results via RRF.

    Usage:
        config = HybridSearchConfig(vault_index_path="/path/to/.tokenpak")
        retriever = HybridRetriever(config)
        results = await retriever.search("my query", top_k=5)
    """

    def __init__(self, config: Optional[HybridSearchConfig] = None) -> None:
        self._config = config or HybridSearchConfig()
        self._bm25 = BM25Retriever(vault_index_path=self._config.vault_index_path)
        self._vector: Optional[LocalVectorRetriever] = None

        # Lazily initialize vector retriever only if sentence-transformers available
        try:
            self._vector = LocalVectorRetriever(
                model_name=self._config.vector_model,
                index_path=self._config.vector_index_path,
            )
        except Exception as e:
            logger.warning("Could not initialize LocalVectorRetriever: %s", e)
            self._vector = None

    def is_available(self) -> bool:
        """Returns True if at least BM25 is ready."""
        return self._bm25.is_available()

    @property
    def vector_available(self) -> bool:
        return self._vector is not None and self._vector.is_available()

    async def index(self, documents: list[dict[str, object]]) -> int:
        """Index documents into both BM25 and vector retrievers."""
        tasks = [self._bm25.index(documents)]
        vector = self._vector
        if vector is not None and vector.is_available():
            tasks.append(vector.index(documents))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("Indexing error: %s", r)
        # Return BM25 count as the canonical count
        first_result = results[0]
        return first_result if not isinstance(first_result, BaseException) else 0

    async def search(self, query_text: str, top_k: int = 5) -> List[FusedResult]:
        """
        Run enabled retrievers in parallel, fuse with RRF, return top_k FusedResults.
        Falls back to BM25-only if vector is unavailable.
        """
        config = self._config
        bm25_query = RetrievalQuery(
            text=query_text,
            top_k=max(top_k * 4, config.top_k),
            min_score=config.bm25_min_score,
        )

        vector = self._vector
        if vector is not None and vector.is_available():
            vec_query = RetrievalQuery(
                text=query_text,
                top_k=max(top_k * 4, config.top_k),
                min_score=0.0,
            )
            bm25_out, vector_out = await asyncio.gather(
                self._bm25.search(bm25_query),
                vector.search(vec_query),
                return_exceptions=True,
            )

            if isinstance(bm25_out, BaseException):
                logger.warning("BM25 search error: %s", bm25_out)
                bm25_results: List[RetrievalResult] = []
            else:
                bm25_results = bm25_out
            if isinstance(vector_out, BaseException):
                logger.warning("Vector search error: %s", vector_out)
                vec_results: List[RetrievalResult] = []
            else:
                vec_results = vector_out

            result_lists: Dict[str, List[RetrievalResult]] = {}
            if bm25_results:
                result_lists["bm25"] = bm25_results
            if vec_results:
                result_lists["vector"] = vec_results

            weights = {
                "bm25": config.bm25_weight,
                "vector": config.vector_weight,
            }
        else:
            # BM25-only fallback
            bm25_results = await self._bm25.search(bm25_query)
            if isinstance(bm25_results, BaseException):
                logger.warning("BM25 search error: %s", bm25_results)
                bm25_results = []
            result_lists = {"bm25": bm25_results} if bm25_results else {}
            weights = {"bm25": 1.0}

        if not result_lists:
            return []

        fused = rrf_fusion_detailed(
            result_lists,
            weights=weights,
            k=config.rrf_k,
            top_n=top_k,
        )
        return fused
