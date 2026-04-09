"""Vault search utilities for agent.vault package.

Provides score_and_sort for multi-signal result ranking (BM25 + semantic).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Weights for multi-signal scoring
# ---------------------------------------------------------------------------
W_BM25 = 0.5
W_SEMANTIC = 0.35
W_META = 0.1
BOOST_SYMBOL = 0.05
BOOST_PATH = 0.05
BOOST_RECENT = 0.05
PENALTY_STALE = 0.1
PENALTY_NOISY = 0.1


def _is_noisy(content: str) -> bool:
    """Detect noisy/boilerplate content."""
    if not content:
        return False
    if len(content) < 20:
        return True
    # Very short content ratio of unique words
    words = content.lower().split()
    if len(words) < 3:
        return True
    return False


def extract_must_hit_terms(query: str) -> List[str]:
    """Extract significant terms from query for symbol matching."""
    if not query:
        return []
    tokens = query.lower().split()
    stop = {"the", "a", "an", "is", "are", "was", "for", "to", "in", "of", "and", "or", "with"}
    return [t for t in tokens if t not in stop and len(t) > 1]


def compute_final_score(
    *,
    sem_norm: float = 0.0,
    bm25_norm: float = 0.0,
    meta_norm: float = 0.0,
    symbol_hit: bool = False,
    path_hit: bool = False,
    is_recent: bool = False,
    is_stale: bool = False,
    is_noisy: bool = False,
) -> float:
    """Compute weighted final score from multiple signals."""
    score = (
        W_BM25 * bm25_norm
        + W_SEMANTIC * sem_norm
        + W_META * meta_norm
    )
    if symbol_hit:
        score += BOOST_SYMBOL
    if path_hit:
        score += BOOST_PATH
    if is_recent:
        score += BOOST_RECENT
    if is_stale:
        score -= PENALTY_STALE
    if is_noisy:
        score -= PENALTY_NOISY
    return max(0.0, score)


def score_and_sort(
    raw_results: List[Tuple[Dict[str, Any], float]],
    *,
    query: str = "",
    semantic_scores: Optional[Dict[str, float]] = None,
    meta_scores: Optional[Dict[str, float]] = None,
    recent_ids: Optional[Set[str]] = None,
    stale_ids: Optional[Set[str]] = None,
) -> List[Tuple[Dict[str, Any], float]]:
    """Apply multi-signal scoring to raw retrieval results and sort.

    Parameters
    ----------
    raw_results : list
        (block_dict, bm25_score) pairs as returned by the vault index.
    query : str
        The original search query (used for symbol/path boost detection).
    semantic_scores : dict, optional
        Map of block_id -> normalised semantic similarity [0, 1].
    meta_scores : dict, optional
        Map of block_id -> normalised metadata score [0, 1].
    recent_ids : set, optional
        Set of block_ids considered recent/current.
    stale_ids : set, optional
        Set of block_ids considered stale.

    Returns
    -------
    list
        (block_dict, final_score) pairs sorted by final_score desc.
    """
    if not raw_results:
        return []

    semantic_scores = semantic_scores or {}
    meta_scores = meta_scores or {}
    recent_ids = recent_ids or set()
    stale_ids = stale_ids or set()

    # Normalise BM25 scores to [0, 1]
    bm25_values = [s for _, s in raw_results]
    bm25_max = max(bm25_values) if bm25_values else 1.0
    bm25_min = min(bm25_values) if bm25_values else 0.0
    bm25_range = bm25_max - bm25_min or 1.0

    query_terms = set(extract_must_hit_terms(query))
    query_lower = query.lower()

    rescored: List[Tuple[Dict[str, Any], float]] = []
    for block, raw_bm25 in raw_results:
        block_id = block.get("block_id", block.get("source_path", ""))
        content = block.get("content", "")
        source = block.get("source_path", "")

        bm25_norm = (raw_bm25 - bm25_min) / bm25_range
        sem_norm = semantic_scores.get(block_id, 0.0)
        meta_norm = meta_scores.get(block_id, 0.0)

        symbol_hit = bool(query_terms and any(t in content.lower() for t in query_terms))
        path_hit = bool(source and source.lower() in query_lower)
        is_recent = block_id in recent_ids
        is_stale = block_id in stale_ids
        noisy = _is_noisy(content)

        final = compute_final_score(
            sem_norm=sem_norm,
            bm25_norm=bm25_norm,
            meta_norm=meta_norm,
            symbol_hit=symbol_hit,
            path_hit=path_hit,
            is_recent=is_recent,
            is_stale=is_stale,
            is_noisy=noisy,
        )
        rescored.append((block, final))

    return sorted(
        rescored,
        key=lambda item: (
            -item[1],
            item[0].get("source_path", ""),
            item[0].get("block_id", ""),
        ),
    )


# ---------------------------------------------------------------------------
# Re-exports for router compatibility
# ---------------------------------------------------------------------------
# router.py imports these from tokenpak.agent.vault.search; the canonical
# implementations live in tokenpak.vault.search (the older module path).

from tokenpak.vault.search import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    RETRIEVED_CONTEXT_HEADER,
    inject_retrieved_context,
    measure_injection_consistency,
    sort_retrieval_results,
)

__all__ = [
    "compute_final_score",
    "extract_must_hit_terms",
    "score_and_sort",
    "DEFAULT_MAX_TOKENS",
    "RETRIEVED_CONTEXT_HEADER",
    "inject_retrieved_context",
    "measure_injection_consistency",
    "sort_retrieval_results",
]
