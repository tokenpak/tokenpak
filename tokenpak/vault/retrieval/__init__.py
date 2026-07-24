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

# Re-exports from vault.search (backward-compat with the old vault/retrieval.py shim)
from tokenpak.vault.search import (
    _BOOST_PATH,
    _BOOST_RECENCY,
    _BOOST_SYMBOL,
    _PENALTY_NOISE,
    _PENALTY_STALE,
    _W_BM25,
    _W_META,
    _W_SEM,
    COVERAGE_OK,
    COVERAGE_STRONG,
    all_must_hits_found,
    chunks_contain_term,
    compute_coverage_score,
    compute_final_score,
    extract_must_hit_terms,
    inject_retrieved_context,
    interpret_coverage,
    measure_injection_consistency,
    score_and_sort,
    sort_retrieval_results,
)

from .base import (
    FusedResult,
    HybridSearchConfig,
    RetrievalQuery,
    RetrievalResult,
    Retriever,
    RetrieverType,
)
from .bm25 import BM25Retriever
from .fusion import WeightedFusion, rrf_fusion, rrf_fusion_detailed
from .hybrid import HybridRetriever
from .vault_index import VaultIndex, _bm25_tokenize
from .vector_local import LocalVectorRetriever

__all__ = [
    "HybridRetriever",
    "BM25Retriever",
    "LocalVectorRetriever",
    "Retriever",
    "RetrievalResult",
    "RetrievalQuery",
    "FusedResult",
    "HybridSearchConfig",
    "RetrieverType",
    "rrf_fusion",
    "rrf_fusion_detailed",
    "WeightedFusion",
    "VaultIndex",
    "_bm25_tokenize",
    "inject_retrieved_context",
    "sort_retrieval_results",
    "compute_final_score",
    "extract_must_hit_terms",
    "all_must_hits_found",
    "measure_injection_consistency",
    "COVERAGE_OK",
    "COVERAGE_STRONG",
    "chunks_contain_term",
    "compute_coverage_score",
    "interpret_coverage",
    "score_and_sort",
    "base",
    "bm25",
    "fusion",
    "hybrid",
    "vault_index",
    "vector_local",
]
