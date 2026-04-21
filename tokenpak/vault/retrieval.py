"""
TokenPak — Deterministic Retrieval Injection
============================================

Provides cache-stable retrieval by:
- Sorting results deterministically (score desc, path asc, chunk_id asc)
- Hard-capping injected tokens to a fixed budget
- Always emitting the same section header ("## Retrieved Context")

This ensures that repeated requests with semantically identical queries
produce byte-identical prompt injections, maximising Anthropic prompt cache hits.

Usage::

    from tokenpak.agent.vault.retrieval import sort_retrieval_results, inject_retrieved_context

    results = vault_index.search(query, top_k=10)
    injection = inject_retrieved_context(results, max_tokens=4000)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from tokenpak.agent.ingest.schema_converter import should_serve_schema
from tokenpak.agent.memory.session_capsules import capsule_retrieval_score

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

RETRIEVED_CONTEXT_HEADER = "## Retrieved Context"
DEFAULT_MAX_TOKENS = 4000
DEFAULT_TOP_K = 10


def sort_retrieval_results(
    results: List[Tuple[Dict[str, Any], float]],
) -> List[Tuple[Dict[str, Any], float]]:
    """Sort retrieval results for cache stability.

    Ordering:
    1. BM25 score — highest first (primary)
    2. ``source_path`` — ascending (tie-break A)
    3. ``block_id`` — ascending (tie-break B)

    This guarantees byte-identical ordering across repeated requests, even
    when two blocks receive the same BM25 score.

    Args:
        results: List of (block_dict, score) tuples as returned by
                 :meth:`VaultIndex.search`.

    Returns:
        Sorted list with the same (block_dict, score) structure.
    """
    return sorted(
        results,
        key=lambda item: (
            -capsule_retrieval_score(
                item[1],
                (item[0].get("metadata") or {}).get("session_capsule")
                if isinstance(item[0].get("metadata"), dict)
                else None,
            ),
            item[0].get("source_path", ""),  # path asc
            item[0].get("block_id", ""),  # chunk_id asc
        ),
    )


def inject_retrieved_context(
    results: List[Tuple[Dict[str, Any], float]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    count_tokens_fn: Optional[Any] = None,
    intent: Optional[str] = None,
) -> Tuple[str, int, List[str]]:
    """Build a cache-stable injection block from retrieval results.

    - Sorts deterministically via :func:`sort_retrieval_results`.
    - Respects a hard token budget (``max_tokens``).
    - Emits a fixed ``## Retrieved Context`` header every time.
    - Each result formatted as a fenced block with source path + relevance.

    Args:
        results: List of (block_dict, score) tuples.
        max_tokens: Hard token cap for the entire injection (header + content).
        count_tokens_fn: Optional callable(text) -> int for token counting.
                         Falls back to ``tokenpak.tokens.count_tokens`` if not
                         provided.

    Returns:
        Tuple of (injection_text, tokens_used, source_refs).
        Returns ("", 0, []) if nothing fits in the budget.
    """
    if count_tokens_fn is None:
        try:
            from tokenpak.telemetry.tokens import count_tokens  # type: ignore

            count_tokens_fn = count_tokens
        except ImportError:
            # Rough fallback: 4 chars ≈ 1 token
            def count_tokens_fn(t):
                return max(1, len(t) // 4)

    sorted_results = sort_retrieval_results(results)

    header = f"\n\n{RETRIEVED_CONTEXT_HEADER}\n"
    header_tokens = count_tokens_fn(header)

    parts: List[str] = []
    tokens_used = header_tokens
    source_refs: List[str] = []

    prefer_schema = should_serve_schema(intent)

    for block, score in sorted_results:
        source_path = block.get("source_path", block.get("block_id", "unknown"))
        content = _select_block_content(block, prefer_schema=prefer_schema)

        block_text = f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}"
        block_tokens = count_tokens_fn(block_text)

        remaining = max_tokens - tokens_used
        if remaining < 50:
            break  # Not enough budget for even a tiny block

        if block_tokens > remaining:
            # Truncate to fit within budget (char-based approximation)
            char_limit = remaining * 4
            content = content[:char_limit].rsplit("\n", 1)[0]
            block_text = f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}"
            block_tokens = count_tokens_fn(block_text)
            if block_tokens > remaining:
                break  # Still doesn't fit after truncation

        parts.append(block_text)
        tokens_used += block_tokens
        source_refs.append(source_path)

    if not parts:
        return "", 0, []

    injection_text = header + "\n\n".join(parts)
    # Final recount for accuracy
    tokens_used = count_tokens_fn(injection_text)

    return injection_text, tokens_used, source_refs


def _select_block_content(block: Dict[str, Any], prefer_schema: bool) -> str:
    """Choose schema summary or raw block content for retrieval injection."""
    if prefer_schema:
        metadata = block.get("metadata") or {}
        schema = metadata.get("schema") if isinstance(metadata, dict) else None
        doc_type = metadata.get("doc_type") if isinstance(metadata, dict) else None
        if isinstance(schema, dict) and schema:
            payload = {"doc_type": doc_type, "schema": schema}
            return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    return block.get("content", "")


def measure_injection_consistency(
    injection_fn,
    query: str,
    runs: int = 5,
) -> Dict[str, Any]:
    """Run the injection function N times and measure consistency.

    Args:
        injection_fn: Callable(query) -> (text, tokens, refs)
        query: The search query to use.
        runs: Number of repeated runs.

    Returns:
        Dict with keys:
            - ``consistent``: bool — True if all injections are identical
            - ``unique_texts``: number of distinct injection texts
            - ``tokens_per_run``: list of token counts
            - ``avg_tokens``: float average
    """
    texts = []
    token_counts = []

    for _ in range(runs):
        text, tokens, _ = injection_fn(query)
        texts.append(text)
        token_counts.append(tokens)

    unique_texts = len(set(texts))
    return {
        "consistent": unique_texts == 1,
        "unique_texts": unique_texts,
        "tokens_per_run": token_counts,
        "avg_tokens": sum(token_counts) / len(token_counts) if token_counts else 0,
    }


# ===========================================================================
# Multi-Signal Scoring + Coverage Score
# ===========================================================================

import re

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

_W_SEM = 0.45
_W_BM25 = 0.45
_W_META = 0.10

_BOOST_SYMBOL = 0.15
_BOOST_PATH = 0.10
_BOOST_RECENCY = 0.05

_PENALTY_STALE = 0.15
_PENALTY_NOISE = 0.10

# Boilerplate/noise patterns
_NOISE_PATTERNS = re.compile(r"(^import\s|^#\s*-+|^pass$|^\.\.\.|\bTODO\b|\bFIXME\b)", re.MULTILINE)
_NOISE_THRESHOLD = 0.60  # If >60% of lines are noise → noise penalty applies

# Coverage thresholds
COVERAGE_STRONG = 0.75
COVERAGE_OK = 0.55


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_noisy(content: str) -> bool:
    """Return True if the chunk is mostly boilerplate."""
    lines = [l for l in content.splitlines() if l.strip()]
    if not lines:
        return True
    noise_lines = sum(1 for l in lines if _NOISE_PATTERNS.search(l.strip()))
    return (noise_lines / len(lines)) > _NOISE_THRESHOLD


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
    """Compute the multi-signal final score for a single chunk.

    Parameters
    ----------
    sem_norm : float
        Normalised semantic similarity in [0, 1].
    bm25_norm : float
        Normalised BM25 score in [0, 1].
    meta_norm : float
        Normalised metadata score in [0, 1].
    symbol_hit : bool
        True if the chunk contains query identifiers (function/class names).
    path_hit : bool
        True if the query explicitly references this chunk's file path.
    is_recent : bool
        True if the chunk is from the current commit / latest artifact.
    is_stale : bool
        True if the chunk is from an old artifact and the repo has a newer version.
    is_noisy : bool
        True if the chunk is mostly boilerplate / noise.

    Returns
    -------
    float
        Unclamped final score (may be slightly negative for noisy+stale chunks).
    """
    score = _W_SEM * sem_norm + _W_BM25 * bm25_norm + _W_META * meta_norm
    if symbol_hit:
        score += _BOOST_SYMBOL
    if path_hit:
        score += _BOOST_PATH
    if is_recent:
        score += _BOOST_RECENCY
    if is_stale:
        score -= _PENALTY_STALE
    if is_noisy:
        score -= _PENALTY_NOISE
    return score


# ---------------------------------------------------------------------------
# Must-hit term extraction
# ---------------------------------------------------------------------------

# Identifier pattern: function names, class names, error codes (e.g. E501, TypeError)
_IDENTIFIER_RE = re.compile(
    r"\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning|Type|Config|Manager|Handler)?|"
    r"[a-z_][a-z0-9_]{2,}|"
    r"[A-Z][A-Z0-9_]{2,}|"
    r"[A-Z]\d{3,})\b"
)
_STOP_WORDS = frozenset(
    "the a an is are was were be been being have has had do does did "
    "will would could should may might must shall can i you he she we they "
    "it its this that these those and or not but if for in on at to of by "
    "with from as about into through during before after above below "
    "what how when where why who which "
    # programming keywords / generic terms (not identifiers)
    "function method class type var let const def return import from pass "
    "true false none null undefined void int str float bool list dict set "
    "value key name item result output input code file path query search "
    "use using show explain how does work".split()
)


def extract_must_hit_terms(query: str) -> list[str]:
    """Extract identifier tokens from *query* that must appear in results.

    Returns a list of unique identifiers (function names, class names, error
    codes, etc.) found in the query.

    Parameters
    ----------
    query : str
        The search query text.

    Returns
    -------
    list[str]
        Unique must-hit identifiers.
    """
    tokens = _IDENTIFIER_RE.findall(query)
    seen: set[str] = set()
    result: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low not in _STOP_WORDS and tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def chunks_contain_term(chunks: list[dict[str, Any]], term: str) -> bool:
    """Return True if at least one chunk contains *term* (case-insensitive)."""
    low = term.lower()
    return any(low in chunk.get("content", "").lower() for chunk in chunks)


def all_must_hits_found(
    chunks: list[dict[str, Any]],
    must_hit_terms: list[str],
) -> bool:
    """Return True if every must-hit term appears in at least one chunk."""
    return all(chunks_contain_term(chunks, t) for t in must_hit_terms)


# ---------------------------------------------------------------------------
# Coverage score
# ---------------------------------------------------------------------------


def compute_coverage_score(
    scored_chunks: list[tuple[dict[str, Any], float]],
    must_hit_terms: list[str],
) -> float:
    """Compute the retrieval coverage score.

    Coverage = must_hit_factor + concentration_factor + mass_factor

    - must_hit_factor:       0.45 if all must-hit terms found, else 0.0
    - concentration_factor:  clamp(1 - (unique_files - 1) * 0.15, 0.0, 0.25)
    - mass_factor:           clamp(sum_top5_scores / 4.0, 0.0, 0.30)

    Parameters
    ----------
    scored_chunks : list[tuple[dict, float]]
        (block_dict, score) pairs, already scored by :func:`compute_final_score`.
    must_hit_terms : list[str]
        Identifiers that must appear in the retrieved chunks.

    Returns
    -------
    float
        Coverage score in [0.0, 1.0].
    """
    if not scored_chunks:
        return 0.0

    chunks_only = [c for c, _ in scored_chunks]
    scores_only = [s for _, s in scored_chunks]

    # must_hit_factor
    if must_hit_terms:
        must_hit_factor = 0.45 if all_must_hits_found(chunks_only, must_hit_terms) else 0.0
    else:
        # No must-hit terms → full must_hit_factor by default
        must_hit_factor = 0.45

    # concentration_factor: fewer unique source files = more focused
    unique_files = len({c.get("source_path", c.get("block_id", "?")) for c in chunks_only})
    concentration_factor = _clamp(1.0 - (unique_files - 1) * 0.15, 0.0, 0.25)

    # mass_factor: sum of top-5 scores / 4
    top5_scores = sorted(scores_only, reverse=True)[:5]
    mass_factor = _clamp(sum(top5_scores) / 4.0, 0.0, 0.30)

    return must_hit_factor + concentration_factor + mass_factor


def interpret_coverage(coverage: float) -> str:
    """Return a human-readable coverage interpretation."""
    if coverage >= COVERAGE_STRONG:
        return "strong"
    elif coverage >= COVERAGE_OK:
        return "ok"
    else:
        return "weak"


# ---------------------------------------------------------------------------
# Score-aware sort (extends existing sort_retrieval_results)
# ---------------------------------------------------------------------------


def score_and_sort(
    raw_results: List[Tuple[Dict[str, Any], float]],
    *,
    query: str = "",
    semantic_scores: Optional[Dict[str, float]] = None,
    meta_scores: Optional[Dict[str, float]] = None,
    recent_ids: Optional[set] = None,
    stale_ids: Optional[set] = None,
) -> List[Tuple[Dict[str, Any], float]]:
    """Apply multi-signal scoring to raw retrieval results and sort.

    Wraps :func:`compute_final_score` for each chunk in *raw_results*.

    Parameters
    ----------
    raw_results : list
        (block_dict, bm25_score) pairs as returned by the vault index.
    query : str
        The original search query (used for symbol/path boost detection).
    semantic_scores : dict, optional
        Map of block_id → normalised semantic similarity [0, 1].
    meta_scores : dict, optional
        Map of block_id → normalised metadata score [0, 1].
    recent_ids : set, optional
        Set of block_ids considered recent/current.
    stale_ids : set, optional
        Set of block_ids considered stale.

    Returns
    -------
    list
        (block_dict, final_score) pairs sorted by final_score desc, then
        deterministically by source_path and block_id.
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

    # Query-level signal: identifiers and path references
    query_terms = set(t.lower() for t in extract_must_hit_terms(query))
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
        is_noisy = _is_noisy(content)

        final = compute_final_score(
            sem_norm=sem_norm,
            bm25_norm=bm25_norm,
            meta_norm=meta_norm,
            symbol_hit=symbol_hit,
            path_hit=path_hit,
            is_recent=is_recent,
            is_stale=is_stale,
            is_noisy=is_noisy,
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
