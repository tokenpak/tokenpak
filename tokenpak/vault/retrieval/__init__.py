"""
tokenpak.vault.retrieval — hybrid search subsystem.

Public API:
    HybridRetriever   — orchestrates BM25 + vector retrieval with RRF fusion
    BM25Retriever     — standalone BM25 retriever
    RetrievalResult   — single retriever result
    RetrievalQuery    — query parameters
    FusedResult       — post-fusion result with per-source breakdown
    HybridSearchConfig — configuration for the hybrid retriever
    rrf_fusion        — raw RRF fusion function
"""
from .base import (
    FusedResult,
    HybridSearchConfig,
    Retriever,
    RetrievalQuery,
    RetrievalResult,
    RetrieverType,
)
from .bm25 import BM25Retriever
from .fusion import WeightedFusion, rrf_fusion, rrf_fusion_detailed
from .hybrid import HybridRetriever
from .vault_index import VaultIndex, _bm25_tokenize
from .vector_local import LocalVectorRetriever

__all__ = ['HybridRetriever', 'BM25Retriever', 'LocalVectorRetriever', 'Retriever', 'RetrievalResult', 'RetrievalQuery', 'FusedResult', 'HybridSearchConfig', 'RetrieverType', 'rrf_fusion', 'rrf_fusion_detailed', 'WeightedFusion', 'VaultIndex', '_bm25_tokenize', 'base', 'bm25', 'fusion', 'hybrid', 'vault_index', 'vector_local']
