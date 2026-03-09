# SPDX-License-Identifier: MIT
"""Token counting utilities with caching, lazy loading, and robust truncation."""

from functools import lru_cache
from typing import Optional, Tuple

# Lazy-loaded encoder (tiktoken init is slow ~100ms)
_ENC: Optional[object] = None
_FALLBACK_MODE = False


def _get_encoder():
    """Lazy-load tiktoken encoder on first use."""
    global _ENC, _FALLBACK_MODE
    if _ENC is None and not _FALLBACK_MODE:
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            _FALLBACK_MODE = True
    return _ENC


@lru_cache(maxsize=8192)
def count_tokens(text: str) -> int:
    """
    Count tokens with LRU cache (8192 entries).

    Cache key is the text itself. Python interns short strings,
    and for longer texts the hash collision rate is negligible.
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    # Fallback: ~4 chars per token
    return max(1, len(text) // 4)


def count_tokens_uncached(text: str) -> int:
    """Count tokens without caching (for benchmarking)."""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> Tuple[str, int]:
    """
    Truncate text to approximately max_tokens.

    Returns: (truncated_text, actual_token_count)

    Hardened for edge cases:
    - max_tokens <= 0 returns empty
    - Empty text returns ("", 0)
    - High token-density text (CJK, emoji) handled via full-range search
    - Always respects max_tokens constraint
    """
    # Edge cases
    if not text:
        return "", 0
    if max_tokens <= 0:
        return "", 0

    current_tokens = count_tokens(text)
    if current_tokens <= max_tokens:
        return text, current_tokens

    # Binary search for optimal truncation point
    # Use full text length as upper bound (handles high token-density text)
    left = 0
    right = len(text)
    best_text = ""
    best_count = 0

    # Optimization: start with estimate to reduce iterations
    estimate = min(len(text), max(1, max_tokens * 4))
    est_tokens = count_tokens(text[:estimate])
    if est_tokens <= max_tokens:
        left = estimate
        best_text = text[:estimate]
        best_count = est_tokens
    else:
        right = estimate

    while left < right:
        mid = (left + right + 1) // 2
        candidate = text[:mid]
        token_count = count_tokens(candidate)

        if token_count <= max_tokens:
            best_text = candidate
            best_count = token_count
            left = mid
        else:
            right = mid - 1

    # Clean up at word boundary (only if we have content and spaces)
    if best_text and len(best_text) > 10 and " " in best_text:
        truncated = best_text.rsplit(" ", 1)[0]
        if truncated:  # Don't truncate to empty
            best_text = truncated + "…"
            best_count = count_tokens(best_text)
            # Safety: ensure we didn't exceed max_tokens with ellipsis
            if best_count > max_tokens and len(truncated) > 1:
                best_text = truncated[:-1] + "…"
                best_count = count_tokens(best_text)

    return best_text, best_count


def estimate_tokens(text: str) -> int:
    """Fast token estimate without encoding (for pre-filtering)."""
    if not text:
        return 0
    # Heuristic: ~4 chars per token for English, ~1.5 for CJK
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, (ascii_chars // 4) + (non_ascii // 2))


def clear_cache():
    """Clear the token count cache."""
    count_tokens.cache_clear()


def cache_info():
    """Get cache statistics."""
    return count_tokens.cache_info()
