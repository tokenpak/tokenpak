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
            -item[1],  # score desc
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
            from tokenpak.tokens import count_tokens  # type: ignore

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
