"""Token counting utilities with caching and lazy loading."""

from functools import lru_cache
from typing import Tuple, Optional

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


@lru_cache(maxsize=4096)
def count_tokens(text: str) -> int:
    """
    Count tokens with LRU cache (4096 entries).
    
    Cache key is the text itself. Python interns short strings,
    and for longer texts the hash collision rate is negligible.
    """
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    # Fallback: ~4 chars per token
    return max(1, len(text) // 4)


def count_tokens_uncached(text: str) -> int:
    """Count tokens without caching (for benchmarking)."""
    enc = _get_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> Tuple[str, int]:
    """
    Truncate text to approximately max_tokens.
    
    Returns: (truncated_text, actual_token_count)
    
    Uses binary search for efficient truncation point finding.
    """
    current_tokens = count_tokens(text)
    if current_tokens <= max_tokens:
        return text, current_tokens
    
    # Binary search for optimal truncation point
    # Start with char estimate (4 chars/token)
    left = 0
    right = min(len(text), max_tokens * 5)  # Upper bound estimate
    best_text = ""
    best_count = 0
    
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
    
    # Clean up at word boundary
    if best_text and " " in best_text:
        best_text = best_text.rsplit(" ", 1)[0] + "…"
        best_count = count_tokens(best_text)
    
    return best_text, best_count


def clear_cache():
    """Clear the token count cache."""
    count_tokens.cache_clear()


def cache_info():
    """Get cache statistics."""
    return count_tokens.cache_info()
