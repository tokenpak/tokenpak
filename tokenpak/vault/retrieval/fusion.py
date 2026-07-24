"""
Reciprocal Rank Fusion (RRF) implementation for combining multiple retrieval result lists.
No external dependencies.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .base import FusedResult, RetrievalResult


def rrf_fusion(
    result_lists: Dict[str, List[RetrievalResult]],
    weights: Optional[Dict[str, float]] = None,
    k: int = 60,
    top_n: int = 20,
) -> List[Tuple[str, float, RetrievalResult]]:
    """
    Reciprocal Rank Fusion across multiple ranked lists.

    Args:
        result_lists: mapping of source_name -> ranked RetrievalResult list
        weights: optional per-source weight multipliers (default 1.0 each)
        k: RRF constant (higher = less rank-sensitive, default 60)
        top_n: number of top results to return

    Returns:
        List of (doc_id, fused_score, best_RetrievalResult) tuples, sorted by score desc.
    """
    if not result_lists:
        return []

    # Normalize weights
    w: Dict[str, float] = {}
    for source in result_lists:
        w[source] = (weights or {}).get(source, 1.0)

    # Accumulate RRF scores and track best result per doc_id
    scores: Dict[str, float] = {}
    best_result: Dict[str, RetrievalResult] = {}

    for source, results in result_lists.items():
        source_weight = w[source]
        for rank, result in enumerate(results, start=1):
            doc_id = result.doc_id
            rrf_score = source_weight / (k + rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_score
            # Keep the result with the highest original score as canonical
            if doc_id not in best_result or result.score > best_result[doc_id].score:
                best_result[doc_id] = result

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    return [(doc_id, score, best_result[doc_id]) for doc_id, score in ranked]


def rrf_fusion_detailed(
    result_lists: Dict[str, List[RetrievalResult]],
    weights: Optional[Dict[str, float]] = None,
    k: int = 60,
    top_n: int = 20,
) -> List[FusedResult]:
    """
    Like rrf_fusion but returns FusedResult objects with per-source breakdown.
    """
    if not result_lists:
        return []

    w: Dict[str, float] = {}
    for source in result_lists:
        w[source] = (weights or {}).get(source, 1.0)

    scores: Dict[str, float] = {}
    source_results: Dict[str, Dict[str, RetrievalResult]] = {}

    for source, results in result_lists.items():
        source_weight = w[source]
        for rank, result in enumerate(results, start=1):
            doc_id = result.doc_id
            rrf_score = source_weight / (k + rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_score
            if doc_id not in source_results:
                source_results[doc_id] = {}
            source_results[doc_id][source] = result

    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    return [
        FusedResult(
            doc_id=doc_id,
            fused_score=score,
            source_results=source_results.get(doc_id, {}),
        )
        for doc_id, score in ranked
    ]


class WeightedFusion:
    """Stateful wrapper around rrf_fusion with stored configuration."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        k: int = 60,
        top_n: int = 20,
    ) -> None:
        self.weights = weights
        self.k = k
        self.top_n = top_n

    def fuse(
        self,
        result_lists: Dict[str, List[RetrievalResult]],
    ) -> List[FusedResult]:
        """Run RRF fusion with stored config. Returns FusedResult list."""
        return rrf_fusion_detailed(
            result_lists,
            weights=self.weights,
            k=self.k,
            top_n=self.top_n,
        )

    def fuse_simple(
        self,
        result_lists: Dict[str, List[RetrievalResult]],
    ) -> List[Tuple[str, float, RetrievalResult]]:
        """Run RRF fusion. Returns (doc_id, score, result) tuples."""
        return rrf_fusion(
            result_lists,
            weights=self.weights,
            k=self.k,
            top_n=self.top_n,
        )
